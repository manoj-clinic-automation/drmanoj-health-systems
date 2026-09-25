#!/bin/bash
# install_family.sh -- one-time set-up of the Family Edition on the server.
#
# FAMILY_EDITION_V1. Idempotent: safe to run again. As root, after the files of
# family/ have been copied to /root/family (WinSCP or scp):
#
#     bash /root/family/install_family.sh
#
# It creates the folders (/opt/family/code, /srv/family, /etc/family), the
# systemd template units, the family.dr-manoj.in website in CyberPanel (vhost
# only -- SSL needs the DNS record first), a neutral landing page, the proxy
# log format that never records a query string, the first code tree, and two
# cron lines: the nightly backup, and a DNS watch that issues the certificate
# the moment family.dr-manoj.in resolves here and then removes itself.
set -uo pipefail
HOST=family.dr-manoj.in
IP=93.127.195.49
VH=/usr/local/lsws/conf/vhosts/$HOST/vhost.conf

mkdir -p /opt/family/code /srv/family /etc/family /root/family/care /root/family/logs /root/backups/family
chmod 755 /opt/family /opt/family/code /srv/family
chmod 700 /etc/family /root/family/care /root/backups/family

echo "== units"
for u in /root/family/units/family-*@.service; do
  install -m 644 "$u" /etc/systemd/system/
done
systemctl daemon-reload

echo "== website"
if [ ! -f "$VH" ]; then
  cyberpanel createWebsite --package Default --owner admin --domainName "$HOST" \
      --email drmka.ortho@gmail.com --php 8.2 || { echo "createWebsite FAILED"; exit 1; }
fi
[ -f "$VH" ] || { echo "no vhost after createWebsite"; exit 1; }
# Never log a query string on this host (CLAUDE.md 5): path, method, protocol only.
if grep -q '"%r"' "$VH"; then
  cp -p "$VH" "$VH.bak-noquery-$(date +%Y%m%d_%H%M%S)"
  sed -i 's/"%r"/"%m %U %H"/' "$VH"
fi
DOC=/home/$HOST/public_html
if [ -d "$DOC" ]; then
  cat > "$DOC/index.html" <<'HTML'
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Family</title>
<style>body{margin:0;font:18px/1.5 system-ui,sans-serif;background:#f6f7f9;color:#17202a;padding:24px}
@media (prefers-color-scheme: dark){body{background:#111821;color:#e6edf3}}</style></head>
<body><p>Family health diaries.</p><p>Open the link you were given.</p></body></html>
HTML
  chown --reference="$DOC" "$DOC/index.html" 2>/dev/null || true
fi

echo "== code tree"
if [ ! -e /opt/family/code/current ]; then
  TS=$(date +%Y%m%d_%H%M%S)
  /usr/bin/python3 -B /root/family/build_code.py --out "/opt/family/code/$TS" || exit 1
  chmod -R a+rX,go-w "/opt/family/code/$TS"
  ln -sfn "/opt/family/code/$TS" /opt/family/code/current
fi

echo "== cron"
( crontab -l 2>/dev/null | grep -v 'FAMILY_BACKUP' | grep -v 'FAMILY_SSL_WATCH'
  echo "40 2 * * * /usr/bin/python3 -B /root/family/family_backup.py >> /root/family/logs/backup.log 2>&1 # FAMILY_BACKUP"
  if [ ! -f /etc/letsencrypt/live/$HOST/fullchain.pem ]; then
    echo "*/10 * * * * /bin/bash /root/family/ssl_when_ready.sh >> /root/family/logs/ssl.log 2>&1 # FAMILY_SSL_WATCH"
  fi
) | crontab -
echo "done."
