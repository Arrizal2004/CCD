# Campus Cloud Dashboard (CCD)

[Bahasa Indonesia](README.md) | **English**

**Student Lab Portal**: a self-hosted lab portal for Proxmox VE that gives students access to their lab VMs from the browser.

CCD lets students reach lab VMs from a browser. Students do not need a VPN, and no SSH or RDP port has to be opened to the internet. CCD runs on top of the Proxmox VE you already have.

A student signs in, sees the VMs assigned to them, and clicks **Connect**. An SSH or RDP session opens in a browser tab through Apache Guacamole. Admins decide who can access which VM, create VMs from templates, and monitor sessions from the same dashboard.

> The deployment guide is available in English for the first three sections (install, prepare Proxmox, register Proxmox): [`docs/DEPLOYMENT.en.md`](docs/DEPLOYMENT.en.md). The rest of the guides in [`docs/`](docs/) are in Indonesian for now. The dashboard itself, including server error messages and the audit log, works in both Indonesian and English.

## Features

- SSH and RDP from the browser.
- Access per VM or per group. Students only see their own VMs, without Proxmox host names. Access can be limited to "Open Web only": the student sees the VM status, opens the web page on that VM and files Helpdesk tickets, without Connect, credentials, power or snapshots.
- Create VMs from templates (clone and cloud-init), snapshots and rollback by students, and RAM, CPU and storage resize by admins. Resize only works while the VM is off, and storage can only grow.
- Bulk VMs per class (one VM per group member, assigned right away, progress in the background, credentials as CSV), and a Create VM form that is prefilled from a student's infrastructure request.
- SSH to the Proxmox host from the dashboard through Guacamole, for sysadmins and superadmins. The username and password are asked for every time and are never stored. Sysadmins can add Proxmox instances, but only superadmins can edit or delete them.
- Open Web: open a web application running on a VM from inside the dashboard. Private addresses are loaded through the dashboard proxy.
- Helpdesk tickets, including reports sent straight from the VM detail view, and VM requests (infrastructure requests). Superadmins can delete tickets and requests that are no longer needed; a summary stays in the audit log.
- The dashboard and Connect work on phones and tablets.
- Two languages, Indonesian and English, including error messages from the server. Each user picks their own with the ID / EN button.
- Status page: service health and resources of the dashboard VPS, live and with 30 days of history.
- VM lease: a VM is shut down automatically when its lease ends, and students can ask for an extension through the Helpdesk.
- Accounts: CSV import (including class groups), account expiry, bulk activate and deactivate, and password reset by admins. A user who forgot their password can send a request from the sign-in page, no email needed.
- Isolated networks ("switches") per class, each with its own subnet, created from the dashboard: internet through NAT, and still reachable from CCD for Connect. Switches live in address blocks per Proxmox that can be added one at a time; a new switch inside an existing block no longer needs a new Tailscale route advertisement.
- OS accounts inside a VM through the QEMU Guest Agent: create users for shared VMs, reset the password of a user who forgot it, and delete users, without SSH or the old password.
- System settings for superadmins: name, logo, accent colour, default language (Indonesian or English), time zone (a live date and time in the header), announcements, sign-up rules, defaults (VM lease, account validity), audit log retention, and the SSH bastion address. Helpdesk categories are managed from the Helpdesk page, and OS choices (with logos) from the Infra Requests page. A school or campus can adapt the dashboard without touching the code.
- SSH from your own terminal (optional): through a bastion on the VPS with an SSH key, only to the VMs that belong to the user. Works for `scp`, `sftp` and VS Code Remote-SSH.
- Audit & Remote:
  - An activity log that can be filtered by account, action, severity and date, exported to CSV, and shown in the language of the admin viewing it (Indonesian or English). Changes to accounts, groups, Proxmox instances, tickets and infrastructure requests are recorded.
  - A summary of failed sign-ins per account and per IP.
  - Active Remote, Open Web and SSH sessions and their history (who, from which IP, to which VM). Admins can disconnect them, and at the same time revoke access to that VM or deactivate the account.
  - Everything about one user in a single window. Log retention is set in the System tab.

## Components

| Component | Role |
|---|---|
| Proxmox VE | The hypervisor. The dashboard uses an API token with limited permissions and never reaches the host over SSH. |
| Apache Guacamole + guacd | Renders SSH and RDP as HTML5 in the browser. |
| FastAPI (backend) | REST API, access control, synchronisation with Guacamole, audit log. |
| React + Vite (frontend) | The interface, served by nginx, which is also the reverse proxy to the backend and Guacamole. |
| PostgreSQL | Application data and Guacamole data. |
| Redis | Cache, sign-in rate limiting, list of revoked tokens. |
| Tailscale (optional, on the OS) | Connects the VPS to Proxmox through subnet routes, and gives HTTPS access from outside. Not part of docker-compose. |

Everything above, except Proxmox and Tailscale, runs as containers from a single file, `backend/docker-compose.yml`.

## Installation

Requirements: a Linux server (Ubuntu, Debian or openSUSE) with root or sudo access, port 80 free, and the ability to reach the Proxmox API (port 8006). We run it on a VPS with 2 vCPU and 2 GB of RAM.

```bash
git clone <repo-url> campus-cloud-dashboard
cd campus-cloud-dashboard
./setup.sh
```

`setup.sh` installs Docker if it is missing, creates `backend/.env` with random secrets and passwords, builds the images one at a time, and starts all containers. It is safe to run again and never overwrites an existing `.env`.

When it finishes, open `http://<server-ip>` and sign in as `admin` with the password printed at the end. The Guacamole admin password is printed once as well. Keep both. The system name and the sign-up rules can be changed in the **System** tab.

The next steps are in the deployment guide. Sections 1 to 3 are in English in [`docs/DEPLOYMENT.en.md`](docs/DEPLOYMENT.en.md); the rest is in Indonesian in [`docs/PANDUAN_DEPLOYMENT.md`](docs/PANDUAN_DEPLOYMENT.md):
- Connecting the VPS to Proxmox through Tailscale (section 1.6, English).
- Creating the pool, API token and permissions in Proxmox (section 2, English).
- Registering Proxmox in the dashboard (section 3, English).
- Building VM templates (section 4, Indonesian; details in [`docs/PANDUAN_TEMPLATE_VM.md`](docs/PANDUAN_TEMPLATE_VM.md)).
- Access from outside the network, or a private VPS with your own domain (sections 7 and 7.1).
- Enabling SSH through the bastion (section 8). The guide for students is [`docs/PANDUAN_SSH.md`](docs/PANDUAN_SSH.md).
- Database backups and copying them off the server (section 9).

## Repository layout

```
backend/     FastAPI: routers/, services/, migrations/, tests/, scripts/ (backup), bastion/ (SSH), docker-compose.yml
frontend/    React + Vite: src/pages/, src/components/, nginx.conf
docs/        Deployment guide (English for sections 1 to 3, full guide in Indonesian), VM template guide, and the SSH guide for students (Indonesian)
setup.sh     One-command installation
SECURITY.md  How to report a security issue
CONTRIBUTING.md  How to contribute
```

## Development

```bash
# Backend: needs PostgreSQL and Redis, see .github/workflows/ci.yml for the variables
cd backend
pip install -r requirements.txt
pytest   # the full command with temporary PostgreSQL and Redis is in CONTRIBUTING.md

# Frontend
cd frontend
npm ci
npm run dev      # http://localhost:5173
npm run lint
npm test         # unit and component tests (Vitest)
npm run build
```

CI in `.github/workflows/ci.yml` runs the backend tests, lint, tests and the frontend build on every push and pull request.

## Security

- Proxmox tokens are created with privilege separation and only get permissions per path (pool, storage, and optionally SDN and nodes).
- Permissions are checked on the server. Hiding a button in the interface is not treated as access control.
- VM credentials and Proxmox tokens are stored encrypted in the application database. On Connect, credentials are also handed to Guacamole and kept in the Guacamole database without additional encryption from CCD.
- `setup.sh` generates the admin password, database passwords and all secrets randomly, and restricts `backend/.env` to mode `600`.
- Only port 80 is opened to the outside. The backend, Guacamole, PostgreSQL and Redis can only be reached from the server itself or from the Docker network. The SSH bastion (port 2222) is open only when enabled, accepts SSH keys only, gives no shell, and only forwards to VMs the dashboard allows.
- Sign-in is rate limited: 5 failed attempts lock the account for 5 minutes. JWT tokens last 8 hours and are revoked on sign-out. Every session of an account ends when its password is changed or reset, and a temporary password from an admin must be changed at sign-in.
- `/healthz` is open without signing in, but error messages and Proxmox details are shown to admins only.
- Important activity is recorded in the audit log, including who deleted a ticket or changed an account. The log cannot be deleted from the dashboard and is cleaned up automatically after its retention period (180 days by default, configurable). The content of remote sessions, and the conversation of a deleted ticket, are deliberately not recorded, for privacy.

The dashboard does not provide HTTPS itself. For access from outside the network, use `tailscale serve` or `tailscale funnel` (section 7 of the deployment guide), or a reverse proxy with a TLS certificate.

How to report a security issue is described in [SECURITY.md](SECURITY.md) (English and Indonesian); please do not use public issues for that.

## Known limitations

- Sign-in works with local dashboard accounts only.
- Roles: superadmin, sysadmin and student. There are no per-student resource quotas.
- There is no two-factor authentication (2FA) yet. Protect superadmin accounts with strong passwords.
- The built-in backup is stored on the same server; copy it off the server yourself (see section 9 of the guide).
- There are no outgoing notifications (email or messages) yet; service health is on the Status page.
- Dashboard passwords are reset by an admin; the dashboard does not send email.
- Proxmox VE is the only supported hypervisor.
- Used at the scale of a single practical lab.

## Status

A prototype that is already running in a practical lab. The project started as a research project and was presented at openSUSE Summit Asia 2026.

## Contributing

The contribution guide is in [CONTRIBUTING.md](CONTRIBUTING.md), in English and Indonesian.

## License

MIT. See [LICENSE](LICENSE).
