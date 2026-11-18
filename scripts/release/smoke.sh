#!/usr/bin/env bash
# Non-systemd acceptance: fresh install in a throwaway prefix and user, then
# an upgrade and a forced rollback. Verifies the preflight, login, estimate,
# a solve on each worker, /node, SSE reconnect, the UI and a restore drill.
# Log: scripts/release/smoke-results/<ts>-nosys-<engine>.log.
#
# Runs unprivileged (HOLDEM_NO_SYSTEMD=1): the API and workers are launched
# by service.sh exactly as the units would launch them. The privileged
# counterpart on the real systemd path is acceptance-root.sh.
#
#   scripts/release/smoke.sh [--engine real|fake]    (default real)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE=real
while [ $# -gt 0 ]; do
    case "$1" in
        --engine) ENGINE="${2:?}"; shift 2 ;;
        *) echo "usage: smoke.sh [--engine real|fake]" >&2; exit 2 ;;
    esac
done
case "$ENGINE" in real|fake) ;; *) echo "--engine must be real or fake" >&2; exit 2 ;; esac
export HOLDEM_INSTALL_ENGINE="$ENGINE"
TIER=nosys

# Throwaway prefix + user unless the caller overrode them.
SMOKE_ROOT="$(mktemp -d /tmp/holdem-smoke.XXXXXX)"
WORK="$SMOKE_ROOT"
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
source "$HERE/smoke-steps.sh"

LOG_DIR="$REPO/scripts/release/smoke-results"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-$TIER-$ENGINE.log"
exec > >(tee -a "$LOG_FILE") 2>&1
cleanup() { "$HERE/service.sh" stop >/dev/null 2>&1 || true; rm -rf "$SMOKE_ROOT"; }
trap cleanup EXIT
svc_stop_api() { "$HERE/service.sh" stop; }
svc_start_api() { "$HERE/service.sh" start; }
svc_diag() { for f in "$RUN_DIR"/logs/*.log; do echo "== $f"; tail -n 20 "$f"; done; }

acceptance_header smoke.sh "$TIER" "$ENGINE" "HOLDEM_NO_SYSTEMD=1, service.sh processes, throwaway prefix $SMOKE_ROOT (no system units)"

step "0. preflight (install.sh --check)"
"$HERE/install.sh" --check || die "preflight failed"

step "1. fresh install"
V1="smoke-$(date +%s)"
"$HERE/install.sh" "$V1"
"$HERE/service.sh" start
smoke_health
for p in "$RUN_DIR"/pids/*.pid; do
    [ "$(readlink -f "/proc/$(cat "$p")/cwd")" = "$(readlink -f "$CURRENT_LINK")" ] \
        || die "$(basename "$p" .pid) not running from the release ($(readlink -f "/proc/$(cat "$p")/cwd"))"
done
echo "API + workers run from $(readlink -f "$CURRENT_LINK")"
smoke_login
smoke_estimate_solves
smoke_node
smoke_sse
smoke_ui
smoke_restore
smoke_upgrade "smoke-$(date +%s)-up"
smoke_rollback "smoke-$(date +%s)-rb"

step "PASS"
echo "tier=$TIER engine=$ENGINE; results recorded in $LOG_FILE"
