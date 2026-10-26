#!/bin/sh
# Post-deploy smoke check for CLOCKIN.
#
#   ./scripts/smoke.sh http://192.168.1.10:5000
#
# Read-only: it never creates an account, imports a roster, or changes data.
# Exits 0 if the deployment looks healthy, 1 otherwise. Safe to run against a
# live classroom instance between periods.
set -u

BASE="${1:-http://127.0.0.1:5000}"
BASE="${BASE%/}"
fails=0

# curl prints 000 on a connection failure and exits non-zero; stdout is what we
# want, so don't append a fallback on top of it.
code() { curl -s -o /dev/null -m 5 -w '%{http_code}' "$@" 2>/dev/null; }
ok()   { printf '  PASS  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }

expect() { # label url expected-codes...
  label="$1"; shift
  url="$1"; shift
  got=$(code "$url")
  for want in "$@"; do
    if [ "$got" = "$want" ]; then ok "$label ($got)"; return; fi
  done
  bad "$label (got $got, wanted one of: $*)"
}

echo "CLOCKIN smoke check — $BASE"

case "$BASE" in
  *:*) : ;;
  *) bad "URL should include a port, e.g. http://server:5000"; echo; echo "$fails failure(s)"; exit 1 ;;
esac

if [ "$(code "$BASE/")" = "000" ]; then
  bad "cannot reach $BASE at all (connection refused or DNS)"
  echo
  echo "$fails failure(s) — nothing else can be checked"
  exit 1
fi

# The kiosk is public and must render without a session.
expect "kiosk page renders"        "$BASE/"           200
# These three touch the session. A blank or placeholder CLOCKIN_SECRET used to
# make them 500 while everything else looked fine — the exact failure this
# check exists to catch.
expect "login page reachable"      "$BASE/login"      200 302
expect "setup page reachable"      "$BASE/setup"      200 302
# Auth must actually be enforced.
expect "dashboard needs a login"   "$BASE/dashboard"  302
expect "badge PDF needs a login"   "$BASE/badges"     302

scan=$(code -X POST -H 'Content-Type: application/json' -d '{}' "$BASE/api/scan")
case "$scan" in
  400) ok "scan API rejects an empty payload (400)" ;;
  000) bad "scan API did not answer" ;;
  *)   bad "scan API returned $scan, expected 400" ;;
esac

# A server error anywhere above is the thing worth alarming on.
for path in / /login /setup /dashboard; do
  got=$(code "$BASE$path")
  case "$got" in
    5*) bad "$path returned $got — check 'docker compose logs' for the reason" ;;
  esac
done

echo
if [ "$fails" -eq 0 ]; then
  echo "All checks passed."
  exit 0
fi
echo "$fails failure(s) — see docs/RUNBOOK.md for the symptom table."
exit 1
