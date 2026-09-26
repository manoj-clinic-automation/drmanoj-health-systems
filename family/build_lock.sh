#!/bin/bash
# build_lock.sh -- the server-wide build lock, shared with the clinic repository.
#
# FAMILY_EDITION_V1 (26-Sep-2026). Sourced by upgrade_all.sh, install_family.sh,
# install_kitchen.sh and any hand-run install step:
#
#     . /root/family/build_lock.sh
#     take_build_lock "CLAUDE_CODE_PROMPT_xxx.md"      # waits; releases itself on exit
#
# One folder, /root/deploy/.claude_code_build.lock (mkdir is atomic). If it
# exists, another build is installing: wait and re-check every 2 minutes,
# never remove it -- unless it is older than 3 hours AND its owner file says
# that build finished, in which case it is taken over with a log line. The
# owner file names this repository and the brief. A trap releases the lock on
# exit, also on failure. BUILD_LOCK_DIR / BUILD_LOCK_WAIT_S / BUILD_LOCK_STALE_S
# exist for the test suite (test_family_lock.py).
BUILD_LOCK_DIR="${BUILD_LOCK_DIR:-/root/deploy/.claude_code_build.lock}"
BUILD_LOCK_WAIT_S="${BUILD_LOCK_WAIT_S:-120}"
BUILD_LOCK_STALE_S="${BUILD_LOCK_STALE_S:-10800}"
BUILD_LOCK_REPO="${BUILD_LOCK_REPO:-drmanoj-health-systems}"
_BUILD_LOCK_HELD=0

_lock_age_s() {
  local now m
  now=$(date +%s)
  m=$(stat -c %Y "$BUILD_LOCK_DIR" 2>/dev/null || echo "$now")
  echo $((now - m))
}

release_build_lock() {
  if [ "$_BUILD_LOCK_HELD" = 1 ]; then
    echo "finished $(date '+%Y-%m-%d %H:%M:%S')" >> "$BUILD_LOCK_DIR/owner" 2>/dev/null
    rm -rf "$BUILD_LOCK_DIR"
    _BUILD_LOCK_HELD=0
    echo "build lock released"
  fi
}

take_build_lock() {
  local brief="${1:-unnamed build}"
  mkdir -p "$(dirname "$BUILD_LOCK_DIR")"
  while ! mkdir "$BUILD_LOCK_DIR" 2>/dev/null; do
    local age owner
    age=$(_lock_age_s)
    owner=$(cat "$BUILD_LOCK_DIR/owner" 2>/dev/null | head -3 | tr '\n' ' ')
    if [ "$age" -gt "$BUILD_LOCK_STALE_S" ] && grep -q '^finished ' "$BUILD_LOCK_DIR/owner" 2>/dev/null; then
      echo "build lock: stale (${age}s old, owner reports finished: $owner) -- taking it over"
      rm -rf "$BUILD_LOCK_DIR"
      continue
    fi
    echo "build lock held by: ${owner:-unknown} (${age}s) -- waiting ${BUILD_LOCK_WAIT_S}s"
    sleep "$BUILD_LOCK_WAIT_S"
  done
  _BUILD_LOCK_HELD=1
  printf '%s\n%s\nstarted %s\n' "$BUILD_LOCK_REPO" "$brief" "$(date '+%Y-%m-%d %H:%M:%S')" > "$BUILD_LOCK_DIR/owner"
  trap release_build_lock EXIT
  echo "build lock taken for: $brief"
}
