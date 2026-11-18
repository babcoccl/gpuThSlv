#!/usr/bin/env bash
# Rehearse cutover.sh on a throwaway VM (never on the outpost): build a
# simulated pre-release deployment — user `holdemold` running the
# deploy/install-user-units.sh user units (same unit names, data in
# ~/.local/share/holdem-solver, env in ~/.config/holdem-solver, API :8000),
# engine forced to fake — solve a job on it, then cutover.sh, verify,
# cutover.sh --rollback, verify, tear everything down.
# Log: scripts/release/smoke-results/<ts>-<tier>-fake-cutover.log.
#
#   sudo env PATH="$PATH" scripts/release/cutover-rehearsal.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export HOLDEM_INSTALL_ENGINE=fake ENGINE=fake
source "$HERE/common.sh"
TIER="$(detect_tier)"
[ "$TIER" = T1 ] || [ "$TIER" = T2 ] || die "$TIER: cutover rehearsal needs root + systemd"
OLD=holdemold
id "$OLD" >/dev/null 2>&1 && die "user $OLD exists; remove it first"
[ ! -L "$CURRENT_LINK" ] || die "release already installed at $CURRENT_LINK"
source "$HERE/smoke-steps.sh"
export HOLDEM_CUTOVER_DIR="$(mktemp -d /tmp/holdem-cutover.XXXXXX)"
LOG_DIR="$REPO/scripts/release/smoke-results"; mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-$TIER-fake-cutover.log"
exec > >(tee -a "$LOG_FILE") 2>&1

teardown() {
    step "teardown"
    "$HERE/uninstall.sh" --purge >/dev/null 2>&1 || true
    if id "$OLD" >/dev/null 2>&1; then
        loginctl disable-linger "$OLD" || true
        systemctl stop "user@$(id -u "$OLD").service" 2>/dev/null || true
        pkill -u "$OLD" 2>/dev/null || true; sleep 1
        userdel -r "$OLD" 2>/dev/null || true
    fi
    rm -rf "$HOLDEM_CUTOVER_DIR"
    [ -n "${SUDO_UID:-}" ] && chown "$SUDO_UID:${SUDO_GID:-$SUDO_UID}" "$LOG_DIR" "$LOG_FILE" 2>/dev/null || true
    echo "removed $OLD, release install and cutover state"
}
trap teardown EXIT

acceptance_header cutover-rehearsal.sh "$TIER" fake "simulated old deployment: user $OLD, systemd user units, API :8000 -> release system units"

step "1. simulated old deployment"
useradd -m -s /bin/bash "$OLD"
OH="$(getent passwd "$OLD" | cut -d: -f6)"; OU="$(id -u "$OLD")"
loginctl enable-linger "$OLD"
for _ in $(seq 30); do [ -S "/run/user/$OU/bus" ] && break; sleep 1; done
as_old() { runuser -u "$OLD" -- env HOME="$OH" XDG_RUNTIME_DIR="/run/user/$OU" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$OU/bus" "$@"; }
rsync -a --no-owner --no-group --exclude='.git/' --exclude='.venv-*/' --exclude='api-data/' \
    --exclude='ui/node_modules/' --exclude='ui/dist/' --exclude='__pycache__/' "$REPO/" "$OH/gpuThSlv/"
chown -R "$OLD:$OLD" "$OH/gpuThSlv"
source "$HERE/preflight.sh"
as_old "$(discover_python api)" -m venv "$OH/gpuThSlv/.venv-api"
as_old "$OH/gpuThSlv/.venv-api/bin/pip" install -q --no-input -r "$OH/gpuThSlv/requirements/api.txt"
as_old "$(discover_python engine)" -m venv "$OH/gpuThSlv/.venv-engine"
as_old "$OH/gpuThSlv/.venv-engine/bin/pip" install -q --no-input -r "$OH/gpuThSlv/requirements/engine-fake.txt"
as_old bash "$OH/gpuThSlv/deploy/install-user-units.sh" >/dev/null 2>&1 || true
# The only change from the outpost's layout: no GPUs, so the engine is fake.
sed -i 's/--engine real/--engine fake/' "$OH/.config/systemd/user/holdem-worker@.service"
sed -i 's/^HOLDEM_SOLVER_ENGINE=.*/HOLDEM_SOLVER_ENGINE=fake/' "$OH/.config/holdem-solver/holdem.env"
as_old systemctl --user daemon-reload
as_old systemctl --user restart holdem-solver.target
OLD_KEY="$(grep '^HOLDEM_API_KEY=' "$OH/.config/holdem-solver/holdem.env" | cut -d= -f2-)"
for _ in $(seq 60); do curl -fsS "http://127.0.0.1:8000/healthz" >/dev/null 2>&1 && break; sleep 2; done
curl -fsS http://127.0.0.1:8000/healthz | jq -c .
as_old systemctl --user is-active holdem-api.service holdem-worker@0.service holdem-worker@1.service
api_key() { echo "$OLD_KEY"; }   # smoke-steps' helpers talk to the old API
JOB=$(solve '{"board":"KsJsTh7d2c","range0":"QQ+, AKs","range1":"TT, AQ","pot":20,"stack":100,"bet_sizes":"standard","iterations":30,"progress_every":10')
wait_done "$JOB" || die "old deployment: job $JOB failed"
NODE_BEFORE="$(curl -fsS -H "Authorization: Bearer $OLD_KEY" "http://127.0.0.1:8000/v1/solves/$JOB/node" | jq -cS .)"
echo "old deployment healthy on :8000; job $JOB done, /node ok ($(echo "$NODE_BEFORE" | wc -c) bytes)"
ls "$OH/.local/share/holdem-solver"

step "2. cutover.sh --dry-run"
"$HERE/cutover.sh" --old-user "$OLD" --dry-run

step "3. cutover.sh"
unset -f api_key; source "$HERE/common.sh"
"$HERE/cutover.sh" --old-user "$OLD" --job "$JOB" "cutover-$(date +%s)"
systemctl is-active holdem-api.service holdem-worker@0.service holdem-worker@1.service
[ "$(as_old systemctl --user is-enabled holdem-api.service || true)" = disabled ] || die "old unit still enabled"
[ "$(as_old systemctl --user is-active holdem-api.service || true)" != active ] || die "old unit still active"
[ "$(ps -o user= -p "$(systemctl show -p MainPID --value holdem-api.service)" | tr -d ' ')" = holdem ] || die "release API not run by holdem"
NODE_AFTER="$(curl -fsS -H "Authorization: Bearer $(api_key)" "http://127.0.0.1:8000/v1/solves/$JOB/node" | jq -cS 'del(.id)')"
[ "$NODE_AFTER" = "$(echo "$NODE_BEFORE" | jq -cS 'del(.id)')" ] || die "/node differs after cutover"
[ "$(api_key)" = "$OLD_KEY" ] || die "API key not carried over"
sqlite3_paths="$(python3 -c 'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("SELECT strategy_path FROM jobs WHERE id=?", (sys.argv[2],)).fetchone()[0])' "$DATA_DIR/solves.db" "$JOB")"
echo "strategy_path now: $sqlite3_paths"
case "$sqlite3_paths" in "$DATA_DIR"/*) ;; *) die "strategy_path not migrated" ;; esac
echo "release healthy as holdem; old units disabled+inactive; old key kept; pre-cutover /node identical"

step "4. cutover.sh --rollback"
"$HERE/cutover.sh" --rollback
[ "$(systemctl is-active holdem-api.service || true)" != active ] || die "release API still active"
[ "$(systemctl is-enabled holdem-api.service || true)" = disabled ] || die "release API still enabled"
as_old systemctl --user is-active holdem-api.service holdem-worker@0.service holdem-worker@1.service
NODE_RB="$(curl -fsS -H "Authorization: Bearer $OLD_KEY" "http://127.0.0.1:8000/v1/solves/$JOB/node" | jq -cS 'del(.id)')"
[ "$NODE_RB" = "$(echo "$NODE_BEFORE" | jq -cS 'del(.id)')" ] || die "/node differs after rollback"
echo "old units enabled+active as $OLD; release units disabled; /node identical"

step "PASS"
echo "tier=$TIER engine=fake; results recorded in $LOG_FILE"
