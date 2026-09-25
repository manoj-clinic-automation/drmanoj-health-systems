#!/bin/bash
# upgrade_all.sh -- bring every Family Edition copy up to the owner's current code.
#
# FAMILY_EDITION_V1. One command, as root, after any release of the owner's
# GutLog / RxGuard / FitLog (or of /root/family itself):
#
#     /root/family/upgrade_all.sh
#
#  1. builds a NEW code tree from /root/{gutlog,rxguard,fitlog,family} by the
#     allowlist in build_code.py (no *.local.json, database or secret can
#     reach it) into /opt/family/code/<stamp>;
#  2. runs the family suites against SCRATCH members built from that same code
#     (loopback ports, scratch folders, never a real member). Any failure stops
#     here: nothing is switched, every member keeps running the old tree;
#  3. runs each member's migrations as that member (FitLog's health-ingest
#     tables; GutLog and RxGuard migrate themselves on first use);
#  4. switches /opt/family/code/current to the new tree, restarts every
#     enabled member, and checks each app's health route says the new version;
#  5. keeps the three newest trees, so going back is `ln -sfn <old> current`
#     and a restart.
set -uo pipefail
TS=$(date +%Y%m%d_%H%M%S)
CODE=/opt/family/code
NEW=$CODE/$TS
LOG=/root/family/logs/upgrade_$TS.log
mkdir -p /root/family/logs "$CODE"
echo "== build $NEW"
/usr/bin/python3 -B /root/family/build_code.py --out "$NEW" || { echo "build FAILED"; exit 1; }
chmod -R a+rX,go-w "$NEW"

echo "== self-test against scratch members (log: $LOG)"
: > "$LOG"
for suite in /root/family/test_family_*.py; do
  case "$suite" in *_ui*.py) continue ;; esac   # browser suites run offline only
  echo "-- $(basename "$suite")" | tee -a "$LOG"
  /usr/bin/python3 -B "$suite" /root/gutlog/app.py >> "$LOG" 2>&1
  if ! tail -3 "$LOG" | grep -q "RESULT: ALL PASS"; then
    echo "self-test FAILED in $(basename "$suite") -- nothing switched. Tail:"
    grep -E "^\[FAIL\]" "$LOG" | tail -20
    rm -rf "$NEW"
    exit 1
  fi
  grep -E "checks, [0-9]+ failed" "$LOG" | tail -1
done

SLUGS=$(/usr/bin/python3 - <<'PY'
import json
try:
    reg = json.load(open("/root/family/members.local.json"))
except Exception:
    reg = {"members": []}
print(" ".join(m["slug"] for m in reg["members"] if m.get("enabled", True)))
PY
)
echo "== migrations for: ${SLUGS:-none}"
for s in $SLUGS; do
  runuser -u "fam_$s" -- /usr/bin/python3 -B "$NEW/fitlog/migrate_health_ingest.py" \
      "/srv/family/$s/fitlog/fitlog.db" >> "$LOG" 2>&1 && echo "  $s: fitlog ok" \
      || { echo "  $s: fitlog migration FAILED -- nothing switched"; exit 1; }
done

echo "== switch"
PREV=$(readlink -f "$CODE/current" 2>/dev/null || true)
ln -sfn "$NEW" "$CODE/current.new" && mv -T "$CODE/current.new" "$CODE/current"
for s in $SLUGS; do
  systemctl restart "family-gut@$s" "family-rx@$s" "family-fit@$s"
done
sleep 4
BAD=0
GV=$(grep -o 'APP_VERSION = "[0-9.]*"' "$NEW/gutlog/app.py" | grep -o '[0-9.]*')
RV=$(grep -o 'APP_VERSION = "[0-9.]*"' "$NEW/rxguard/app.py" | grep -o '[0-9.]*')
FV=$(grep -o 'APP_VERSION = "[0-9.]*"' "$NEW/fitlog/app.py" | grep -o '[0-9.]*')
for s in $SLUGS; do
  P=$(/usr/bin/python3 -c "import json;m=[x for x in json.load(open('/root/family/members.local.json'))['members'] if x['slug']=='$s'][0];print(m['ports']['gut'],m['ports']['rx'],m['ports']['fit'])")
  set -- $P
  g=$(curl -s "http://127.0.0.1:$1/$s/healthz"); r=$(curl -s "http://127.0.0.1:$2/$s/rx/healthz")
  f=$(curl -s "http://127.0.0.1:$3/$s/fit/health")
  echo "  $s: gut '$g' rx '$r' fit '$f'"
  [ "$g" = "ok $GV" ] && [ "$r" = "ok $RV" ] && echo "$f" | grep -q "\"$FV\"" || BAD=1
done
if [ "$BAD" = 1 ]; then
  echo "A member did not come back on the new version. Previous tree: $PREV"
  echo "Go back:  ln -sfn $PREV $CODE/current && systemctl restart 'family-*@*'"
  exit 1
fi
ls -1dt "$CODE"/20* 2>/dev/null | tail -n +4 | xargs -r rm -rf
echo "== done: every member on gut $GV / rx $RV / fit $FV (tree $TS)"
