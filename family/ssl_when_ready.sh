#!/bin/bash
# ssl_when_ready.sh -- issue family.dr-manoj.in's certificate the moment DNS allows.
#
# FAMILY_EDITION_V1. Cron, every 10 minutes, installed by install_family.sh.
# Let's Encrypt can only issue once the name resolves to this server, which
# needs one A record at GoDaddy (the owner's one step). This watches for it,
# asks CyberPanel for the certificate, checks the certificate file is really
# there, and removes its own cron line. Until then it does nothing but log.
HOST=family.dr-manoj.in
IP=93.127.195.49
CERT=/etc/letsencrypt/live/$HOST/fullchain.pem
now() { date '+%F %T'; }
if [ -f "$CERT" ]; then
  crontab -l 2>/dev/null | grep -v 'FAMILY_SSL_WATCH' | crontab -
  echo "$(now) certificate present; watch removed"
  exit 0
fi
GOT=$(dig +short "$HOST" A @8.8.8.8 2>/dev/null | tail -1)
if [ "$GOT" != "$IP" ]; then
  echo "$(now) waiting for DNS: $HOST -> '${GOT:-nothing}'"
  exit 0
fi
echo "$(now) DNS ok; issuing"
cyberpanel issueSSL --domainName "$HOST"
if [ -f "$CERT" ]; then
  /usr/local/lsws/bin/lswsctrl restart >/dev/null 2>&1
  crontab -l 2>/dev/null | grep -v 'FAMILY_SSL_WATCH' | crontab -
  echo "$(now) certificate issued; OpenLiteSpeed restarted; watch removed"
else
  echo "$(now) issueSSL ran but $CERT is not there; will retry"
fi
