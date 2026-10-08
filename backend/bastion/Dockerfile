# Bastion SSH untuk Campus Cloud Dashboard: hanya meneruskan koneksi ke VM (ssh -J),
# tanpa shell. Izin setiap key ditanyakan ke backend CCD saat login.
FROM alpine:3.22

RUN apk add --no-cache openssh-server curl \
 && adduser -D -s /sbin/nologin tunnel \
 && sed -i 's/^tunnel:!:/tunnel:*:/' /etc/shadow \
 && adduser -D -H -s /sbin/nologin ccdkeys \
 && mkdir -p /data /run/sshd

COPY sshd_config /etc/ssh/sshd_config
COPY ccd-keys ccd-audit ccd-kill /usr/local/bin/
COPY entrypoint.sh /entrypoint.sh
RUN chown root:root /usr/local/bin/ccd-keys /usr/local/bin/ccd-audit /usr/local/bin/ccd-kill /entrypoint.sh \
 && chmod 755 /usr/local/bin/ccd-keys /usr/local/bin/ccd-audit /usr/local/bin/ccd-kill /entrypoint.sh

EXPOSE 2222
# Cek proses, bukan koneksi ke port 2222, supaya log tidak dipenuhi koneksi healthcheck.
HEALTHCHECK --interval=30s --timeout=3s CMD pidof sshd >/dev/null && pidof syslogd >/dev/null || exit 1
ENTRYPOINT ["/entrypoint.sh"]
