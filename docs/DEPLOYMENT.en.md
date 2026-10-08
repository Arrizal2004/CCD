# Deployment Guide: Campus Cloud Dashboard (CCD)

**Student Lab Portal**: a self-hosted lab portal for Proxmox VE.

This is the English version of the first three sections of the deployment guide: from an empty VPS to a dashboard that is connected to Proxmox. It follows the steps we ran and verified ourselves.

> **Scope of this document.** Sections 1 to 3 are translated here. The remaining sections (templates, deploying VMs, classes and switches, monitoring, access from outside the network, the SSH bastion, backups) are only available in Indonesian for now, in [`PANDUAN_DEPLOYMENT.md`](PANDUAN_DEPLOYMENT.md). Links to them are given at the end of each section.

**Order:** (1) Deploy the dashboard on a VPS → (2) Prepare Proxmox (token, user, pool) → (3) Register Proxmox in the dashboard → then templates, VMs, and Connect (Indonesian guide).

**Contents**
1. [Clone and deploy the dashboard on a VPS](#1-clone-and-deploy-the-dashboard-on-a-vps)
2. [Prepare Proxmox: user, pool, API token](#2-prepare-proxmox-user-pool-api-token)
3. [Register Proxmox in the dashboard](#3-register-proxmox-in-the-dashboard)

---

## 1. Clone and deploy the dashboard on a VPS

### 1.1 Requirements
- A Linux VPS or server (Ubuntu, Debian or openSUSE; the whole stack runs in containers, so the distribution does not matter) with SSH access as root or with sudo.
- The VPS does **not** have to be on the same network as Proxmox. It only needs to reach `https://<proxmox-ip>:8006` (the Proxmox API port) over any network: a LAN, a VPN, or the internet if Proxmox exposes that port.
- Docker does **not** have to be installed already. `setup.sh` (section 1.2) checks for it and installs it if it is missing.

### 1.2 The quick way: `./setup.sh` (recommended)

```bash
git clone <your-repo-url> campus-cloud-dashboard
cd campus-cloud-dashboard
./setup.sh
```

The script does the following:
1. Checks for Docker and the Docker Compose plugin and installs them if missing (Ubuntu and Debian through the official Docker installer; openSUSE through `zypper`; other distributions are tried through the official installer as a fallback).
2. Creates `backend/.env` from `.env.example`. Every secret and password is generated randomly with `openssl rand`: `JWT_SECRET`, `AGENT_ENC_SECRET`, `GUAC_ADMIN_PASS`, `INITIAL_ADMIN_PASSWORD`, `POSTGRES_PASSWORD`, `GUAC_DB_PASSWORD` and `BASTION_TOKEN`. `ALLOWED_ORIGINS` is filled in from the detected server IP. The file gets mode `600`.
3. Makes sure port 80 is free, builds the backend and then the frontend image one at a time, starts all containers, and waits for the backend to be ready.
4. Installs a daily database backup cron job (section 9 of the [Indonesian guide](PANDUAN_DEPLOYMENT.md#9-backup-database)).
5. If the server has less than 4 GB of RAM and no swap, creates a 2 GB swap file (`/swapfile`) so the kernel does not kill containers when memory runs out.
6. Prints a summary: the dashboard URL, the dashboard admin password and the Guacamole admin password. Both passwords are printed only once, so save them.

**Idempotent**: it is safe to run again at any time (for example to redeploy after `git pull`). It does not reinstall Docker and never overwrites an existing `backend/.env`.

To update to the latest version:
```bash
git pull
./setup.sh
```
The images are rebuilt (including the bastion, if enabled), the containers are replaced, and the database and `backend/.env` are left untouched. The dashboard is unavailable for about a minute while the containers are replaced.

If you created `backend/.env` by hand (section 1.3 below) before running `setup.sh`, the script uses it as it is.

Continue with **section 1.5** (first sign-in), then **section 2**.

### 1.3 The manual way (when you want control over every step)

Extra requirement for this route: Docker and the Docker Compose plugin are already installed (`docker compose version` must work).

```bash
git clone <your-repo-url> campus-cloud-dashboard
cd campus-cloud-dashboard/backend
```

Create a `.env` file in the `backend/` folder. The **required** variables:

| Variable | How to fill it | Notes |
|---|---|---|
| `JWT_SECRET` | `openssl rand -hex 32` | Used to sign JWTs **and** as the basis of the encryption key (Fernet) that protects VM credentials and Proxmox tokens in the database. Never expose or commit this value. |
| `GUAC_ADMIN_PASS` | A strong password of your choice | The Guacamole admin (`guacadmin`) password is rotated to this value automatically the first time the backend starts, replacing the publicly known default `guacadmin/guacadmin`. |
| `ALLOWED_ORIGINS` | The dashboard's domain or IP, for example `http://vps-ip` or `https://dashboard.example.com` | CORS. If the dashboard is reached through several different addresses (for example LAN and Tailscale), separate them with commas. |
| `INITIAL_ADMIN_PASSWORD` | A strong password of your choice | The password of the `admin` account, created while the database is still empty. If left empty, `admin123` is used and **must** be changed right away. |
| `POSTGRES_PASSWORD`, `GUAC_DB_PASSWORD` | `openssl rand -hex 24` | Passwords of the application database and the Guacamole database. They only take effect when the database volume is first created. |

**Optional** variables:

| Variable | When to set it |
|---|---|
| `AGENT_ENC_SECRET` | `openssl rand -hex 32`: enables encryption of Redis payloads. Leave empty if you do not need it (compatibility mode; credentials stay safe because they are already encrypted through `JWT_SECRET` above). |
| `COMPOSE_PROFILES`, `BASTION_*` | Only when enabling SSH through the bastion. See section 8 of the [Indonesian guide](PANDUAN_DEPLOYMENT.md#8-opsional-ssh-dari-terminal-sendiri--bastion). |
| `AUDIT_RETENTION_DAYS` | How many days audit logs and session history are kept before they are deleted automatically (default 180). It can also be set in the **System** tab, which takes precedence. |
| `GUAC_DATABASE_URL` | Only if Guacamole uses a database other than `guacamoledb` on the same PostgreSQL server. Used to read the Remote session history. |

A minimal `.env`:
```bash
JWT_SECRET=<output of openssl rand -hex 32>
GUAC_ADMIN_PASS=<your-strong-password>
ALLOWED_ORIGINS=http://<vps-ip>
INITIAL_ADMIN_PASSWORD=<your-strong-password>
POSTGRES_PASSWORD=<output of openssl rand -hex 24>
GUAC_DB_PASSWORD=<output of openssl rand -hex 24>
```
Restrict access to the file: `chmod 600 .env`.

### 1.4 Run Docker Compose (manual route)
The dashboard uses port 80, so make sure no other web server is using it (`sudo ss -ltnp 'sport = :80'`). Build the images one at a time so a small VPS does not run out of memory:
```bash
sudo docker compose build backend
sudo docker compose build frontend
sudo docker compose up -d
```

Wait until every container is `Up`:
```bash
sudo docker compose ps
```
There should be 6 containers: `ccd-backend`, `ccd-frontend`, `ccd-guacamole`, `ccd-guacd`, `ccd-postgres` and `ccd-redis`. If the bastion is enabled, there is one more: `ccd-bastion`.

On the first deployment `up` takes longer (1 to 2 minutes), because the backend deliberately waits for Postgres, Redis, guacd and Guacamole to be *healthy* first (see the `STATUS` column), so the Guacamole admin password is certain to be set before Connect is used. If the backend never starts, check which container is not healthy:
```bash
sudo docker compose ps
sudo docker compose logs --tail=30 guacamole
```

Check the backend log to make sure there are no errors at startup:
```bash
sudo docker compose logs --tail=30 backend
```

### 1.5 First sign-in
Open `http://<vps-ip>` in a browser and sign in as `admin`. The account is created automatically the first time the backend runs with an empty database. Its password is:
- With `./setup.sh`: the random password printed at the end (also in `backend/.env`, the `INITIAL_ADMIN_PASSWORD` variable).
- With the manual route: the `INITIAL_ADMIN_PASSWORD` you set, or `admin123` if you left it empty.

Change the password after your first sign-in from the Profile menu (top right). This is mandatory if you used `admin123`, because that password is written in the source code.

Then open the **System** tab (superadmin only) to adapt the dashboard to your school or campus:
- **Identity and appearance:** system name, short name (the header on phones), institution name, tagline, logo (PNG, JPG or WebP, at most 512 KB) and accent colour. They are used in the browser tab title and icon, the sign-in page, header, footer and the About window. A colour that is too dark is rejected so the text on buttons stays readable.
- **Default language and theme:** Indonesian or English, and a dark theme, a light theme, or follow the user's device. Every user, including visitors who do not have an account yet on the sign-in page, can switch with the **ID / EN** button and the sun or moon icon button; the choice is saved in each browser. Every page and the error messages from the server follow the language the user picked. The details of the activity log, its CSV export, and system messages in tickets (for example a status change) are stored in both languages and shown in the language currently in use; older records created before this feature are translated automatically where they use the system's standard sentences. Names, titles and text typed by users are shown as they were written.
- **Announcement:** text of type Info, Warning or Important, with an optional display period, which can also be shown on the sign-in page. Users can dismiss Info and Warning announcements.
- **Time zone:** the dashboard's time zone (default Asia/Jakarta, WIB). It is used for the live clock in the header next to the About button, for every date and time in the dashboard, and for exported CSV files. Only the way records are shown changes; existing records are not modified. Indonesian zones (WIB, WITA, WIT) are listed first.
- **Defaults:** the lease for new VMs (prefilled in the Create VM form) and the validity of new accounts (self-registration and CSV import rows without `expires_at`).
- **Audit log retention:** how many days the activity log and the Remote, Web and SSH session history are kept before they are deleted automatically (at least 7 days). If empty, `AUDIT_RETENTION_DAYS` in `.env` is used (default 180). Below the field you can see the number of entries, the oldest entry and the size. The cleanup runs every 6 hours and cannot be undone, so shortening this number deletes older records at the next cleanup.
- **SSH address for users:** the host name or IP of the SSH bastion shown in students' SSH commands (section 8 of the Indonesian guide). Change it here when the domain changes, without editing `.env`.
- **Helpdesk ticket categories** are managed from the **Helpdesk** page (the *Manage categories* button, superadmin): rename, add or delete. *Lease extension* and *Other* always exist because the system uses them.
- **OS choices in infrastructure requests** are managed from the **Infra Requests** page (the *Manage OS choices* button, superadmin): the list of operating systems students can choose when requesting a VPS (default: Windows and Ubuntu). They can be added, renamed, reordered or deleted, with at least one remaining. Each OS may have a logo (PNG, JPG or WebP, at most 256 KB) shown next to its name; the logo follows the OS when it is renamed and is removed when the OS is deleted. The top one is preselected. Existing requests keep showing the OS that was chosen at the time.
- **Self-registration:** can be opened or closed. The list of allowed email addresses holds one rule per line: `@campus.example` accepts every address on that domain including its subdomains (for example `@student.campus.example`), while a full address accepts only that address. If the list is empty, any email is accepted and email stays optional. The sign-in page only shows the domains, never individual addresses.

The dashboard does not send verification emails, so the email list only filters what was typed. Full access still waits for admin verification (through an infrastructure request). One email can only be used by one account. Every settings change is recorded in the activity log.

### 1.6 Connecting the VPS to Proxmox through Tailscale
The dashboard VPS must be able to reach the Proxmox API (port 8006) and the IP of every VM (for SSH, RDP and Open Web). If the VPS and Proxmox are not on the same network, the way we do it is Tailscale with subnet routes. Run the commands as root.

1. Install Tailscale on the VPS and on every Proxmox, then sign in to the same tailnet:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   tailscale up
   ```
2. On every Proxmox, enable IP forwarding:
   ```bash
   echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/99-tailscale.conf
   sysctl -p /etc/sysctl.d/99-tailscale.conf
   ```
3. On every Proxmox, advertise the VM subnet (replace it with yours):
   ```bash
   tailscale up --advertise-routes=10.0.1.0/24
   ```
   If it is refused because of earlier flags, repeat it with all the earlier flags, or use `tailscale set --advertise-routes=...`.
4. In the Tailscale admin console: Machines, pick the Proxmox machine, Edit route settings, then approve its subnet.
5. On the VPS, accept the routes, then test:
   ```bash
   tailscale up --accept-routes
   ip route get <ip-of-a-vm>     # must go through tailscale0
   ```

If your tailnet uses custom ACLs, make sure the VPS is allowed to reach that subnet. When you register Proxmox in the dashboard (section 3), fill Host with Proxmox's tailnet IP, not its LAN IP.

If you later use the Switch feature (section 5.6 of the [Indonesian guide](PANDUAN_DEPLOYMENT.md#56-switch-jaringan-terisolasi-per-kelas)), the switch address block is advertised the same way by the host setup script: once per block, not once per switch.

---

## 2. Prepare Proxmox: user, pool, API token

The dashboard **never** signs in to Proxmox with a username and password. Everything goes through a **tightly scoped API token**, which can only see and control VMs inside one particular pool, plus the storage you allow. That is what makes it safe to keep the token in the dashboard's `.env` and database.

Run all of the following commands **on the Proxmox server** (SSH as root, or the Shell in the Proxmox web GUI: Datacenter → node → Shell).

### 2.1 Create a pool
```bash
pveum pool add campus-cloud --comment "Campus Cloud Dashboard"
```
Every VM and template the dashboard should manage must live in this pool. The dashboard's token can only "see" the contents of this pool, not the whole of Proxmox.

### 2.2 Create an API token
Two options, pick one:

**Option A: a token of the `root@pam` user (what we use):**
```bash
pveum user token add root@pam ccd-dashboard --privsep 1
```
`--privsep 1` matters: it means the token does **not** automatically get root's permissions. It is fully bound by the ACLs you grant by hand in step 2.3. Without privsep the token would have the same full access as root (do not do that).

**Option B: a dedicated PVE user, more isolated (best practice if you want to be more careful):**
```bash
pveum user add ccd@pve --comment "Campus Cloud Dashboard service account"
pveum user token add ccd@pve dashboard --privsep 1
```

After running one of the commands above, Proxmox prints the **token secret**. **It is shown only once**, so copy it and keep it now. The format:
```
┌──────────────┬──────────────────────────────────────┐
│ key          │ value                                  │
├──────────────┼──────────────────────────────────────┤
│ full-tokenid │ root@pam!ccd-dashboard                 │
│ value        │ xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx   │
└──────────────┴──────────────────────────────────────┘
```
`full-tokenid` is the **Token ID** and `value` is the **Token secret**. You will enter both in the dashboard later (section 3).

### 2.3 Grant permissions (ACLs) to the token

Replace `root@pam!ccd-dashboard` below with your token ID, and `<storage-disk>` with the name of the storage where VM disks live (check with `pvesm status`, usually `local-lvm`).

**Required:**
```bash
# Full control over the campus-cloud pool (create/delete VMs, snapshots, and so on, but ONLY inside this pool)
pveum acl modify /pool/campus-cloud --tokens 'root@pam!ccd-dashboard' --roles PVEAdmin

# Read and write VM disks on the storage where VMs are kept
pveum acl modify /storage/<storage-disk> --tokens 'root@pam!ccd-dashboard' --roles PVEDatastoreUser
```

**Optional** (enable according to the features you want):
```bash
# If the VM network bridge is managed through an SDN zone (not a normal Linux bridge such as vmbr0)
pveum acl modify /sdn/zones/<zone-name>/<bridge> --tokens 'root@pam!ccd-dashboard' --roles PVESDNUser

# To show the "Host Performance" panel (live CPU, RAM, disk and network) on the main dashboard (shown to admins only)
pveum acl modify /nodes/<node-name> --tokens 'root@pam!ccd-dashboard' --roles PVEAuditor
```

Verify that the ACLs are in place:
```bash
pveum acl list
```

Note: explicit ACLs are still required even though the token belongs to `root@pam`, because of `--privsep 1`. A token with privsep does not inherit the super-user rights of `root`. It is treated as a separate identity that must be given ACLs by hand, exactly like a normal PVE user. That is what makes the token safe to hand to an external application.

---

## 3. Register Proxmox in the dashboard

1. Sign in to CCD as an admin or superadmin.
2. Open the **Integrations** tab (top menu; shown only to superadmins and sysadmins).
3. **Manage instances** → **+ Add instance**.
4. Fill in:
   - **Label**: any name, for example `lab` (used internally as an identifier, never shown to students).
   - **Host**: `<proxmox-ip>:8006`.
   - **Token ID**: from step 2.2, for example `root@pam!ccd-dashboard`.
   - **Token secret**: from step 2.2.
   - **Verify the SSL certificate**: turn it off if Proxmox uses a self-signed certificate (the default on a fresh Proxmox install).
5. Save. The dashboard tries to connect right away. If it works, the nodes and VMs inside the `campus-cloud` pool appear in the **Servers** tab.

If it fails to connect, check again: the token ID and secret are right, the pool ACL has been granted (section 2.3), and Proxmox's port 8006 is reachable from the dashboard VPS (`curl -k https://<proxmox-ip>:8006` from the VPS).

**Who can manage instances.** Sysadmins and superadmins can add instances. Only superadmins can edit or delete them; a sysadmin does not see those buttons, and the server refuses the request (403).

**SSH to the Proxmox host.** In the instance list (the **Integrations** tab, or the *Manage instances* button in Servers) every instance has an **SSH terminal** button. It opens an SSH terminal to the Proxmox host in a new tab through Guacamole. The dashboard deliberately does not store the host's username or password: Guacamole asks for them every time it connects, so only someone who holds those credentials can get in. Only sysadmins and superadmins can open it; students are not given access to the connection. The requirement is that the dashboard VPS can reach port 22 of the Proxmox host (over the same network or Tailscale, section 1.6) and that SSH login is allowed on that host. The address used is the instance address without port 8006. Every opening is recorded in the activity log (`PVE_HOST_SSH`), and the connection is removed when the instance is deleted.

---

## What comes next (Indonesian guide)

- Build VM templates: [section 4](PANDUAN_DEPLOYMENT.md#4-buat-template), details in [`PANDUAN_TEMPLATE_VM.md`](PANDUAN_TEMPLATE_VM.md).
- Deploy VMs from CCD, assign them to students, accounts and groups, VM lease, and isolated class networks: [section 5](PANDUAN_DEPLOYMENT.md#5-deploy-vm-dari-ccd).
- Connect, Open Web and monitoring sessions: [section 6](PANDUAN_DEPLOYMENT.md#6-connect).
- Access from outside the network, or a private VPS with your own domain: [sections 7 and 7.1](PANDUAN_DEPLOYMENT.md#7-opsional-akses-dari-luar-jaringan--tailscale).
- SSH from a student's own terminal through the bastion: [section 8](PANDUAN_DEPLOYMENT.md#8-opsional-ssh-dari-terminal-sendiri--bastion). The guide for students is [`PANDUAN_SSH.md`](PANDUAN_SSH.md).
- Database backups and copying them off the server: [section 9](PANDUAN_DEPLOYMENT.md#9-backup-database).
