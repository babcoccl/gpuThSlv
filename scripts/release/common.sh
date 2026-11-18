# Shared setup for scripts/release/*.sh — source it, don't run it.
# All paths are overridable so smoke.sh can run in a throwaway prefix+user.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Layout (spec: /opt/holdem/releases/<version>, /var/lib/holdem, /etc/holdem).
PREFIX="${HOLDEM_PREFIX:-/opt/holdem}"
RELEASES="$PREFIX/releases"
CURRENT_LINK="$PREFIX/current"
DATA_DIR="${HOLDEM_DATA_DIR:-/var/lib/holdem}"
CONF_DIR="${HOLDEM_CONF_DIR:-/etc/holdem}"
ENV_FILE="$CONF_DIR/holdem.env"
RUN_DIR="${HOLDEM_RUN_DIR:-/run/holdem}"
RUN_NAME="${HOLDEM_RUN_NAME:-holdem}"
STATE_NAME="${HOLDEM_STATE_NAME:-holdem}"
BACKUP_DIR="${HOLDEM_BACKUP_DIR:-$DATA_DIR/backups}"
# Health-check endpoint: an explicit HOLDEM_API_HOST/PORT in the caller's
# environment wins; else the persisted env file (what the service actually
# binds); else the defaults.
# systemd strips surrounding quotes in EnvironmentFile; do the same.
_env_val() { grep "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//" || true; }
API_PORT="${HOLDEM_API_PORT:-$(_env_val HOLDEM_API_PORT)}"
API_PORT="${API_PORT:-8000}"
API_HOST="${HOLDEM_API_HOST:-$(_env_val HOLDEM_API_HOST)}"
API_HOST="${API_HOST:-127.0.0.1}"
HOLDEM_USER="${HOLDEM_USER:-holdem}"
HOLDEM_GROUP="${HOLDEM_GROUP:-$HOLDEM_USER}"
HOLDEM_GPUS="${HOLDEM_GPUS:-0,1}"
# Pinned interpreter versions. Interpreters are discovered by
# discover_python (preflight.sh): explicit HOLDEM_API_PYTHON /
# HOLDEM_ENGINE_PYTHON, then $PYTHON_ROOT/**, then system paths — never
# $HOME, /root or /run/user (ProtectHome=yes hides them from the units).
API_PY_VERSION="3.15.2"
ENGINE_PY_SERIES="3.9"
PYTHON_ROOT="${HOLDEM_PYTHON_ROOT:-/opt/holdem/python}"
SYSTEM_PY_DIRS="${HOLDEM_SYSTEM_PYTHON_DIRS:-/usr/local/bin /usr/bin}"
FORBIDDEN_PREFIXES="${HOLDEM_FORBIDDEN_PREFIXES:-/home /root /run/user}"
API_PYTHON="${HOLDEM_API_PYTHON:-}"
ENGINE_PYTHON="${HOLDEM_ENGINE_PYTHON:-}"
# Engine for this install: explicit HOLDEM_INSTALL_ENGINE, else what the
# existing env file runs, else real. fake = no GPU (acceptance on CI/VMs).
INSTALL_ENGINE="${HOLDEM_INSTALL_ENGINE:-$(_env_val HOLDEM_SOLVER_ENGINE)}"
INSTALL_ENGINE="${INSTALL_ENGINE:-real}"
GPUCFR_DIR="${HOLDEM_GPUCFR_DIR:-$REPO/third_party/GPU-CFR}"
SYSTEMD_UNIT_DIR="${HOLDEM_SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
DEVICE_GROUPS="${HOLDEM_DEVICE_GROUPS:-video render}"
# Test hooks: HOLDEM_NO_SYSTEMD=1 launches processes directly instead of
# installing units; HOLDEM_STUB_VENVS=1 creates empty venv dirs (tests only).
NO_SYSTEMD="${HOLDEM_NO_SYSTEMD:-}"
STUB_VENVS="${HOLDEM_STUB_VENVS:-}"

# T1 root+systemd PID 1 (not a container), T2 same inside a container,
# T3 root without systemd, T4 no root. See docs/PHASE11_1.md.
detect_tier() {
    [ "$(id -u)" -eq 0 ] || { echo T4; return; }
    if [ "$(ps -p 1 -o comm= 2>/dev/null)" = systemd ]; then
        if systemd-detect-virt -cq 2>/dev/null; then echo T2; else echo T1; fi
    else
        echo T3
    fi
}

log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

health_ok() {
    curl -fsS --max-time 5 "http://$API_HOST:$API_PORT/healthz" >/dev/null 2>&1
}

wait_healthy() { # wait_healthy <seconds>
    local deadline=$((SECONDS + ${1:-90}))
    while (( SECONDS < deadline )); do
        health_ok && return 0
        sleep 2
    done
    return 1
}

api_key() { # read HOLDEM_API_KEY out of the env file
    grep '^HOLDEM_API_KEY=' "$ENV_FILE" | cut -d= -f2-
}

render_unit() { # render_unit <template> <dest>
    sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@CONFDIR@|$CONF_DIR|g" \
        -e "s|@DATADIR@|$DATA_DIR|g" -e "s|@RUNDIR@|$RUN_DIR|g" \
        -e "s|@RUNNAME@|$RUN_NAME|g" -e "s|@STATENAME@|$STATE_NAME|g" \
        -e "s|@USER@|$HOLDEM_USER|g" -e "s|@GROUP@|$HOLDEM_GROUP|g" \
        "$1" > "$2"
}
