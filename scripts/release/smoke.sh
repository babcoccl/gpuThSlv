#!/usr/bin/env bash
# Phase 11 acceptance: fresh install in a throwaway prefix and user, then an
# upgrade and a forced rollback. Verifies login, estimate, a solve on both
# GPUs, /node, SSE reconnect, and the UI. Results are appended to
# scripts/release/smoke-results/<ts>.log.
#
# Runs unprivileged (HOLDEM_NO_SYSTEMD=1): the API and workers are launched
# by service.sh exactly as the units would launch them.
#
#   scripts/release/smoke.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# Throwaway prefix + user unless the caller overrode them.
SMOKE_ROOT="$(mktemp -d /tmp/holdem-smoke.XXXXXX)"
export HOLDEM_PREFIX="$SMOKE_ROOT/opt/holdem"
export HOLDEM_DATA_DIR="$SMOKE_ROOT/var/lib/holdem"
export HOLDEM_CONF_DIR="$SMOKE_ROOT/etc/holdem"
export HOLDEM_RUN_DIR="$SMOKE_ROOT/run/holdem"
export HOLDEM_BACKUP_DIR="$HOLDEM_DATA_DIR/backups"
export HOLDEM_NO_SYSTEMD=1
export HOLDEM_RESTART_CMD="$HERE/service.sh"
export HOLDEM_API_PORT="${HOLDEM_API_PORT:-18777}"
export HOLDEM_USER="$(id -un)" HOLDEM_GROUP="$(id -gn)"
export HOLDEM_GPUS="${HOLDEM_GPUS:-0,1}"
source "$HERE/common.sh"

LOG_DIR="$REPO/scripts/release/smoke-results"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
step() { printf '\n=== %s ===\n' "$*"; }
cleanup() { "$HERE/service.sh" stop >/dev/null 2>&1 || true; rm -rf "$SMOKE_ROOT"; }
trap cleanup EXIT

BASE="http://$API_HOST:$API_PORT"
auth() { echo "Authorization: Bearer $(api_key)"; }

step "1. fresh install"
V1="smoke-$(date +%s)"
"$HERE/install.sh" "$V1"
"$HERE/service.sh" start
wait_healthy 240 || { for f in "$RUN_DIR"/logs/*.log; do echo "== $f"; tail -n 20 "$f"; done; die "fresh install not healthy"; }
curl -fsS "$BASE/healthz" | grep -q '"status":"ok"' || die "healthz not ok"
curl -fsS "$BASE/version" | grep -q '"version"'
echo "fresh install healthy"

KEY="$(api_key)"
step "2. login + cookie"
curl -fsS -c "$SMOKE_ROOT/cookies.txt" -H 'Content-Type: application/json' \
    -d "{\"key\":\"$KEY\"}" "$BASE/v1/auth/login" | grep -q csrf
grep -q holdem_session "$SMOKE_ROOT/cookies.txt" && grep -q '#HttpOnly' "$SMOKE_ROOT/cookies.txt" \
    || die "session cookie missing/!HttpOnly"
echo "login ok, HttpOnly cookie set"

step "3. estimate + solve"
curl -fsS -H "$(auth)" -H 'Content-Type: application/json' -X POST "$BASE/v1/estimate" \
    -d '{"spot":{"board":"KsJsTh7d2c","range0":"QQ+, AKs","range1":"TT, AQ","pot":20,"stack":100,"bet_sizes":"standard"}}' \
    | jq -e '.num_nodes > 0' || die "estimate failed"
echo "estimate ok"

solve() { # solve <body> -> job id
    curl -fsS -H "$(auth)" -H 'Content-Type: application/json' -X POST "$BASE/v1/solves" -d "$1" | jq -r .id
}
wait_done() {
    for _ in $(seq 300); do
        st=$(curl -fsS -H "$(auth)" "$BASE/v1/solves/$1" | jq -r .status)
        case "$st" in done) return 0;; failed|cancelled) return 1;; esac
        sleep 2
    done
    return 1
}
JOB=$(solve '{"board":"KsJsTh7d2c","range0":"QQ+, AKs","range1":"TT, AQ","pot":20,"stack":100,"bet_sizes":"standard","iterations":50,"progress_every":10}')
JOB2=$(solve '{"board":"AhKhQdJcTs","range0":"AA, KK","range1":"QQ, JJ","pot":20,"stack":100,"bet_sizes":"standard","iterations":50,"progress_every":10}')
wait_done "$JOB" || die "job $JOB failed"
wait_done "$JOB2" || die "job $JOB2 failed"
GPUS_USED=$(for j in "$JOB" "$JOB2"; do curl -fsS -H "$(auth)" "$BASE/v1/solves/$j" | jq -r .gpu; done | sort -u | tr '\n' ' ')
echo "solves done; gpus used: $GPUS_USED"

step "4. /node on the finished job"
curl -fsS -H "$(auth)" "$BASE/v1/solves/$JOB/node" | jq -e . >/dev/null && echo "/node ok"

step "5. SSE reconnect"
JOB3=$(solve '{"board":"KsJsTh7d2c","range0":"QQ+, AKs","range1":"TT, AQ","pot":20,"stack":100,"bet_sizes":"standard","iterations":200,"progress_every":5}')
timeout 20 curl -fsSN -H "$(auth)" "$BASE/v1/solves/$JOB3/events" > "$SMOKE_ROOT/sse1.txt" 2>/dev/null || true
LAST_ID=$(grep '^id:' "$SMOKE_ROOT/sse1.txt" | tail -1 | cut -d' ' -f2 | tr -d '\r')
[ -n "$LAST_ID" ] || die "no SSE id on first connect"
timeout 20 curl -fsSN -H "$(auth)" -H "Last-Event-ID: $LAST_ID" \
    "$BASE/v1/solves/$JOB3/events" > "$SMOKE_ROOT/sse2.txt" 2>/dev/null || true
grep -q 'event: snapshot' "$SMOKE_ROOT/sse2.txt" || die "reconnect missing snapshot"
echo "SSE reconnect ok (last id $LAST_ID)"

step "6. UI"
curl -fsS "$BASE/ui/" | grep -qi 'html' || die "/ui/ did not serve the SPA"
echo "ui ok"

step "7. restore drill"
BK="$("$HERE/backup.sh" | tail -1)"
"$HERE/service.sh" stop
rm -f "$DATA_DIR/solves.db"
"$HERE/restore.sh" "$BK"
"$HERE/service.sh" start
wait_healthy 120 || die "not healthy after restore"
curl -fsS -H "$(auth)" "$BASE/v1/solves/$JOB/node" | jq -e . >/dev/null \
    && echo "restored job $JOB still served"

step "8. upgrade"
V2="smoke-$(date +%s)-up"
"$HERE/upgrade.sh" "$V2" || die "upgrade failed"
curl -fsS -H "$(auth)" "$BASE/v1/solves/$JOB" | jq -e '.id' >/dev/null
echo "upgrade to $V2 healthy, old job served"

step "9. rollback on failed health check"
V3="smoke-$(date +%s)-rb"
set +e
HOLDEM_BREAK_RELEASE=holdem_solver/api/app.py "$HERE/upgrade.sh" "$V3"
rc=$?
set -e
[ "$rc" -ne 0 ] || die "expected upgrade to $V3 to fail and roll back"
[ "$(readlink -f "$CURRENT_LINK")" = "$(readlink -f "$RELEASES/$V2")" ] \
    && echo "rolled back to $V2" || die "current not on previous release"
wait_healthy 120 && echo "stack healthy on rolled-back release"

step "PASS"
echo "results recorded in $LOG_FILE"
