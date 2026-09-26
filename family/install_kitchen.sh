#!/bin/bash
# install_kitchen.sh -- one-time set-up of the Family Kitchen (Phase C).
#
# FAMILY_EDITION_V1. Idempotent. As root, after /root/family has the Phase C
# files and upgrade_all.sh has put them in /opt/family/code/current:
#
#     bash /root/family/install_kitchen.sh
#
#  * user fam_kitchen and /srv/family/kitchen (700), the unit, started;
#  * tokens: the owner's (/root/family/kitchen/owner.token + owner.capture)
#    and every existing member's (stamp_member.py --kitchen-sync); new
#    members get theirs when they are stamped;
#  * the pool seeded from the owner's recipe cards (seed_kitchen.py), once;
#  * the /kitchen/ route on family.dr-manoj.in;
#  * the reader, every 5 minutes, as root with the Sarvam-capable Python --
#    the same way the owner's records worker runs.
set -uo pipefail
. "$(dirname "$0")/build_lock.sh"
take_build_lock "install_kitchen.sh"
K=/srv/family/kitchen
id -u fam_kitchen >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent \
    --shell /sbin/nologin --user-group fam_kitchen
mkdir -p "$K/attach"
chown -R fam_kitchen:fam_kitchen "$K"; chmod 700 "$K" "$K/attach"
install -m 644 /root/family/units/family-kitchen.service /etc/systemd/system/
systemctl daemon-reload
/usr/bin/python3 -B /root/family/stamp_member.py --kitchen-sync || exit 1
if [ ! -f "$K/.seeded" ]; then
  runuser -u fam_kitchen -- /usr/bin/python3 -B -c "import sys; sys.path.insert(0,'/opt/family/code/current/family'); import os; os.environ['KITCHEN_DIR']='$K'; import kitchen, sqlite3; c=sqlite3.connect('$K/kitchen.db'); c.executescript(kitchen.SCHEMA); c.commit()"
  NAME=$(/usr/bin/python3 -c "import json;print(json.load(open('/root/family/members.local.json')).get('caretaker','Manoj'))" 2>/dev/null || echo Manoj)
  /usr/bin/python3 -B /root/family/seed_kitchen.py --gutlog-db /root/gutlog/health3.db --kitchen-db "$K/kitchen.db" --name "$NAME" --apply || exit 1
  chown fam_kitchen:fam_kitchen "$K/kitchen.db"; chmod 600 "$K/kitchen.db"
  touch "$K/.seeded"; chown fam_kitchen:fam_kitchen "$K/.seeded"
fi
systemctl enable --now family-kitchen.service
sleep 2
echo "kitchen healthz: $(curl -s http://127.0.0.1:8199/kitchen/healthz)"
( crontab -l 2>/dev/null | grep -v 'FAMILY_KITCHEN_READER'
  echo "*/5 * * * * flock -n /tmp/kitchen_reader.lock /root/gutlog/venv/bin/python -B /opt/family/code/current/family/kitchen_reader.py >> /root/family/logs/kitchen_reader.log 2>&1 # FAMILY_KITCHEN_READER"
) | crontab -
echo "done."
