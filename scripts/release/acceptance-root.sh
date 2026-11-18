#!/usr/bin/env bash
# Privileged acceptance on the real systemd path: preflight, install.sh as
# root (system units, `holdem` user, /opt/holdem, /etc/holdem, /var/lib/holdem),
# assert the units' identity/hardening, run the shared smoke steps against
# them (login, estimate, solves on GPU 0 and GPU 1, /node, SSE reconnect,
# /ui/, restore drill), upgrade, forced-failure rollback, uninstall --purge.
# Log: scripts/release/smoke-results/<ts>-<tier>-<engine>.log.
#
#   sudo env PATH="$PATH" scripts/release/acceptance-root.sh [--engine fake|real]
#
# --engine fake: no GPU needed (Devin VM / CI); GPU behaviour is NOT
# exercised. --engine real on the outpost is the operator's run.
# Refuses to run over an existing install (it ends with --purge). On the
# outpost, next to the old :8000 deployment, use HOLDEM_API_PORT=8100
# HOLDEM_ALLOW_COEXIST=1 (see RUNBOOK.md).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE=real
while [ $# -gt 0 ]; do
    case "$1" in
        --engine) ENGINE="${2:?}"; shift 2 ;;
        *) echo "usage: acceptance-root.sh [--engine fake|real]" >&2; exit 2 ;;
    esac
done
case "$ENGINE" in real|fake) ;; *) echo "--engine must be real or fake" >&2; exit 2 ;; esac
export HOLDEM_INSTALL_ENGINE="$ENGINE"
unset HOLDEM_NO_SYSTEMD HOLDEM_STUB_VENVS HOLDEM_RESTART_CMD
source "$HERE/common.sh"

TIER="$(detect_tier)"
case "$TIER" in
    T4) die "T4: not root — nothing privileged can run. Stop: re-run with sudo, or use smoke.sh (no systemd)." ;;
    T3) die "T3: root but PID 1 is not systemd — system units cannot run here; use smoke.sh + systemd-analyze verify (docs/PHASE11_1.md)." ;;
esac
for t in jq curl ss systemd-analyze; do command -v "$t" >/dev/null || die "$t not on PATH"; done
# Also refuse kept data/releases (a plain uninstall leaves them): the final
# --purge would delete their jobs and backups.
if [ -e "$ENV_FILE" ] || [ -L "$CURRENT_LINK" ] || [ -e "$SYSTEMD_UNIT_DIR/holdem-api.service" ] \
    || [ -e "$DATA_DIR" ] || [ -e "$RELEASES" ]; then
    die "existing install or data found ($ENV_FILE / $CURRENT_LINK / holdem-api.service / $DATA_DIR / $RELEASES); acceptance ends with uninstall --purge — refusing"
fi
source "$HERE/smoke-steps.sh"

WORK="$(mktemp -d /tmp/holdem-accept.XXXXXX)"
LOG_DIR="$REPO/scripts/release/smoke-results"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-$TIER-$ENGINE.log"
exec > >(tee -a "$LOG_FILE") 2>&1
PASSED=""
cleanup() {
    if [ -z "$PASSED" ] && [ -z "${HOLDEM_ACCEPT_KEEP:-}" ]; then
        echo "--- FAILED: cleaning up with uninstall.sh --purge (HOLDEM_ACCEPT_KEEP=1 keeps it)"
        "$HERE/uninstall.sh" --purge || true
    fi
    rm -rf "$WORK"
    if [ -n "${SUDO_UID:-}" ]; then chown "$SUDO_UID:${SUDO_GID:-$SUDO_UID}" "$LOG_DIR" "$LOG_FILE" 2>/dev/null || true; fi
}
trap cleanup EXIT

IFS=',' read -ra GPU_IDS <<< "$HOLDEM_GPUS"
UNITS="holdem-api.service"
for g in "${GPU_IDS[@]}"; do UNITS="$UNITS holdem-worker@$g.service"; done

svc_stop_api() { systemctl stop holdem-api.service; }
svc_start_api() { :; }  # restore.sh restarts the API unit itself
svc_diag() {
    # shellcheck disable=SC2086
    systemctl --no-pager status $UNITS || true
    journalctl --no-pager -n 40 -u 'holdem-*' || true
}

_prop() { systemctl show -p "$2" --value "$1"; }

assert_units() { # assert_units <expected release dir>
    local u pid want="$1" exe gids g gid
    for u in $UNITS; do
        [ "$(systemctl is-active "$u")" = active ] || { svc_diag; die "$u not active"; }
        [ "$(_prop "$u" User)" = "$HOLDEM_USER" ] || die "$u User != $HOLDEM_USER"
        pid="$(_prop "$u" MainPID)"
        [ "$(ps -o user= -p "$pid" | tr -d ' ')" = "$HOLDEM_USER" ] || die "$u pid $pid not running as $HOLDEM_USER"
        [ "$(_prop "$u" NoNewPrivileges)" = yes ] && [ "$(_prop "$u" ProtectSystem)" = strict ] \
            && [ "$(_prop "$u" ProtectHome)" = yes ] && [ "$(_prop "$u" PrivateTmp)" = yes ] \
            || die "$u hardening properties missing"
        exe="$(readlink -f "/proc/$pid/exe")"
        case "$exe" in /home/*|/root/*|/run/user/*) die "$u runs $exe from a home dir" ;; esac
        [ "$(readlink -f "/proc/$pid/cwd")" = "$want" ] || die "$u cwd $(readlink -f /proc/$pid/cwd) != $want"
        echo "$u: active, pid $pid, user $HOLDEM_USER, exe $exe, cwd $want, NoNewPrivileges/ProtectSystem=strict/ProtectHome/PrivateTmp"
    done
    for g in "${GPU_IDS[@]}"; do
        u="holdem-worker@$g.service"; pid="$(_prop "$u" MainPID)"
        tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q -- "--engine $ENGINE --gpu $g" \
            || die "$u cmdline: $(tr '\0' ' ' < /proc/$pid/cmdline)"
        tr '\0' '\n' < "/proc/$pid/environ" | grep -qx "CUDA_VISIBLE_DEVICES=$g" || die "$u CUDA_VISIBLE_DEVICES != $g"
        gids=" $(awk '/^Groups:/ {$1=""; print}' "/proc/$pid/status") "
        for grp in $DEVICE_GROUPS; do
            gid="$(getent group "$grp" | cut -d: -f3)"
            case "$gids" in *" $gid "*) ;; *) die "$u not in group $grp ($gid): $gids" ;; esac
        done
        if [ "$ENGINE" = fake ]; then
            ! grep -qE 'gpu_cfr|libtorch|libcuda' "/proc/$pid/maps" || die "$u (fake) mapped gpu_cfr/torch/cuda"
            echo "$u: '--engine fake --gpu $g', CUDA_VISIBLE_DEVICES=$g, groups $DEVICE_GROUPS, no gpu_cfr/torch/cuda mapped"
        else
            echo "$u: '--engine real --gpu $g', CUDA_VISIBLE_DEVICES=$g, groups $DEVICE_GROUPS"
        fi
    done
}

acceptance_header acceptance-root.sh "$TIER" "$ENGINE" "root via sudo; system units in $SYSTEMD_UNIT_DIR; prefix $PREFIX; user $HOLDEM_USER; port $API_PORT"
echo "# probe:  id=$(id -un) pid1=$(ps -p 1 -o comm=) system=$(systemctl is-system-running || true) virt=$(systemd-detect-virt 2>/dev/null || true) container=$(systemd-detect-virt -c 2>/dev/null || true)"

step "0. preflight (install.sh --check)"
"$HERE/install.sh" --check || die "preflight failed"

step "1. fresh install (system units)"
V1="accept-$(date +%s)"
"$HERE/install.sh" "$V1"
smoke_health
step "1b. installed layout + units"
[ "$(stat -c '%a %U' "$ENV_FILE")" = "600 root" ] || die "$ENV_FILE not 0600 root"
grep -qx "HOLDEM_SOLVER_ENGINE=$ENGINE" "$ENV_FILE" || die "env file engine != $ENGINE"
notroot="$(find "$RELEASES/$V1" ! -user root -print -quit)"
[ -z "$notroot" ] || die "release tree not root-owned: $notroot ($(stat -c %U "$notroot"))"
[ "$(stat -c %U "$DATA_DIR")" = "$HOLDEM_USER" ] || die "$DATA_DIR not owned by $HOLDEM_USER"
echo "env 0600 root (engine=$ENGINE); release tree root-owned; $DATA_DIR owned by $HOLDEM_USER"
systemd-analyze verify "$SYSTEMD_UNIT_DIR/holdem-api.service" "$SYSTEMD_UNIT_DIR/holdem-worker@0.service" \
    "$SYSTEMD_UNIT_DIR/holdem-solver.target" && echo "systemd-analyze verify: ok"
assert_units "$RELEASES/$V1"

smoke_login
smoke_estimate_solves
smoke_node
smoke_sse
smoke_ui
smoke_restore
assert_units "$RELEASES/$V1"
smoke_upgrade "accept-$(date +%s)-up"
assert_units "$RELEASES/$V2"
smoke_rollback "accept-$(date +%s)-rb"
assert_units "$RELEASES/$V2"
journalctl --no-pager -u holdem-api.service -n 3 -o cat | grep -q . && echo "journal: holdem-api logs present"

step "uninstall --purge"
"$HERE/uninstall.sh" --purge
for u in holdem-api.service holdem-worker@.service holdem-solver.target; do
    [ ! -e "$SYSTEMD_UNIT_DIR/$u" ] || die "$u still installed"
done
# shellcheck disable=SC2086
for u in $UNITS; do [ "$(systemctl is-active "$u" || true)" != active ] || die "$u still active"; done
[ ! -e "$RELEASES" ] && [ ! -e "$CURRENT_LINK" ] && [ ! -e "$DATA_DIR" ] && [ ! -e "$ENV_FILE" ] \
    || die "purge left files behind"
[ -z "$(ss -Hltn "sport = :$API_PORT")" ] || die "port $API_PORT still bound"
! pgrep -u "$HOLDEM_USER" -f holdem_solver >/dev/null || die "holdem_solver processes still running"
echo "units removed, releases/current/data/env purged, port $API_PORT free, no processes"

step "PASS"
PASSED=1
echo "tier=$TIER engine=$ENGINE; results recorded in $LOG_FILE"
[ "$ENGINE" = fake ] && echo "operator verifies (not exercised here): see docs/PHASE11_1.md 'Operator verifies'"
exit 0
