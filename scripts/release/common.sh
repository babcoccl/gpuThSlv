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
# Pinned interpreters (full paths; never resolved via PATH at install time).
API_PYTHON="${HOLDEM_API_PYTHON:-/usr/bin/python3.15}"
ENGINE_PYTHON="${HOLDEM_ENGINE_PYTHON:-$HOME/.local/bin/python3.9}"
# Test hooks: HOLDEM_NO_SYSTEMD=1 launches processes directly instead of
# installing units; HOLDEM_STUB_VENVS=1 creates empty venv dirs (tests only).
NO_SYSTEMD="${HOLDEM_NO_SYSTEMD:-}"
STUB_VENVS="${HOLDEM_STUB_VENVS:-}"

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
