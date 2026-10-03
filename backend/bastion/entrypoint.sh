#!/bin/sh
set -e

if [ -z "$BASTION_TOKEN" ]; then
    echo "BASTION_TOKEN kosong. Isi di backend/.env (setup.sh membuatnya otomatis)." >&2
    exit 1
fi

# Token disimpan sebagai berkas header curl yang hanya bisa dibaca user ccdkeys, supaya tidak
# muncul di daftar proses atau environment sshd.
( umask 077; printf 'X-Bastion-Token: %s\n' "$BASTION_TOKEN" > /etc/ssh/bastion-header )
chown ccdkeys:ccdkeys /etc/ssh/bastion-header
chmod 400 /etc/ssh/bastion-header
unset BASTION_TOKEN

# Host key disimpan di volume supaya tidak berubah setiap container dibuat ulang.
[ -f /data/ssh_host_ed25519_key ] || ssh-keygen -q -t ed25519 -N '' -f /data/ssh_host_ed25519_key
[ -f /data/ssh_host_rsa_key ]     || ssh-keygen -q -t rsa -b 4096 -N '' -f /data/ssh_host_rsa_key
echo "Fingerprint host key bastion:"
ssh-keygen -lf /data/ssh_host_ed25519_key.pub
ssh-keygen -lf /data/ssh_host_rsa_key.pub

# sshd menulis log lewat syslog supaya setiap baris membawa PID sesinya (dipakai audit).
# syslogd menulis ke pipe, ccd-audit meneruskannya ke stdout container dan ke backend. Kalau
# rantai log ini berhenti, sshd ikut dihentikan supaya Docker me-restart container.
rm -f /dev/log
( syslogd -n -O - | su -s /bin/sh ccdkeys -c /usr/local/bin/ccd-audit; kill -TERM 1 ) &
i=0
while [ ! -S /dev/log ] && [ "$i" -lt 50 ]; do sleep 0.1; i=$((i + 1)); done

exec /usr/sbin/sshd -D
