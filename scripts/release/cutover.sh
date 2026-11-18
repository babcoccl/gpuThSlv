#!/usr/bin/env bash
# Cutover from the pre-release deployment (systemd *user* units from
# deploy/install-user-units.sh, API on :8000, data in
# ~<user>/.local/share/holdem-solver) to the release layout (system units,
# /opt/holdem, /var/lib/holdem), and the exact rollback.
#
#   sudo scripts/release/cutover.sh [--old-user U] --dry-run   # identify only
#   sudo env PATH="$PATH" scripts/release/cutover.sh [--old-user U] [--job ID] [version]
#   sudo scripts/release/cutover.sh --rollback                 # back to the old units
#
# Steps: identify old units/data/port -> back up (online SQLite + unit/env
# copies) -> stop+disable old units -> install the release (units not
# started) -> migrate the DB via restore.sh (strategy files copied,
# strategy_path rewritten, old API key kept) -> start -> verify /healthz and
# /node of a pre-cutover job. Any failure after the old units stopped runs
# the rollback. The old data dir is only read, never modified.
source "$(dirname "$0")/common.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
CUTOVER_ROOT="${HOLDEM_CUTOVER_DIR:-/var/backups/holdem-cutover}"

OLD_USER="" MODE=cutover JOB="" VERSION=""
while [ $# -gt 0 ]; do
    case "$1" in
        --old-user) OLD_USER="${2:?}"; shift 2 ;;
        --job) JOB="${2:?}"; shift 2 ;;
        --dry-run) MODE=dry; shift ;;
        --rollback) MODE=rollback; shift ;;
        -*) die "unknown option $1" ;;
        *) VERSION="$1"; shift ;;
    esac
done
[ "$(id -u)" -eq 0 ] || die "cutover needs root"
[ -z "$NO_SYSTEMD" ] || die "cutover installs system units; unset HOLDEM_NO_SYSTEMD"

OLD_UNITS="holdem-solver.target holdem-api.service holdem-worker@0.service holdem-worker@1.service"
user_ctl() { # user_ctl <user> systemctl-args...
    local u="$1" uid; shift; uid="$(id -u "$u")"
    runuser -u "$u" -- env XDG_RUNTIME_DIR="/run/user/$uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" systemctl --user "$@"
}
env_get() { grep "^$2=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
node_ok() { # node_ok <port> <key> <job>
    curl -fsS --max-time 30 -H "Authorization: Bearer $2" "http://127.0.0.1:$1/v1/solves/$3/node" \
        | jq -e 'type == "object"' >/dev/null
}
health_at() { curl -fsS --max-time 5 "http://127.0.0.1:$1/healthz" >/dev/null 2>&1; }
wait_at() { local d=$((SECONDS + $2)); while (( SECONDS < d )); do health_at "$1" && return 0; sleep 2; done; return 1; }

# -- rollback ---------------------------------------------------------------------
rollback() {
    local state="${1:-$(ls -d "$CUTOVER_ROOT"/*/ 2>/dev/null | sort | tail -1)}"
    state="${state%/}"
    [ -f "$state/state.env" ] || die "no cutover state under $CUTOVER_ROOT"
    # Parsed, never sourced: OLD_KEY comes from the old user's own env file.
    OLD_USER="$(env_get "$state/state.env" OLD_USER)"
    OLD_PORT="$(env_get "$state/state.env" OLD_PORT)"
    OLD_KEY="$(env_get "$state/state.env" OLD_KEY)"
    CHECK_JOB="$(env_get "$state/state.env" CHECK_JOB)"
    id "$OLD_USER" >/dev/null 2>&1 || die "state names unknown user '$OLD_USER'"
    [[ "$OLD_PORT" =~ ^[0-9]+$ ]] || die "bad OLD_PORT in $state/state.env"
    log "rollback: stopping release units, restoring $OLD_USER's user units ($state)"
    systemctl stop holdem-solver.target holdem-api.service 'holdem-worker@*.service' 2>/dev/null || true
    systemctl disable holdem-solver.target holdem-api.service holdem-worker@0.service holdem-worker@1.service 2>/dev/null || true
    # shellcheck disable=SC2086
    user_ctl "$OLD_USER" enable $OLD_UNITS
    user_ctl "$OLD_USER" start holdem-solver.target
    wait_at "$OLD_PORT" 120 || die "old API not healthy on :$OLD_PORT after rollback (journal: runuser -u $OLD_USER -- journalctl --user)"
    log "old API healthy on :$OLD_PORT"
    if [ -n "$CHECK_JOB" ]; then
        node_ok "$OLD_PORT" "$OLD_KEY" "$CHECK_JOB" || die "old API: /node for $CHECK_JOB failed"
        log "old API serves /node for pre-cutover job $CHECK_JOB"
    fi
    log "rollback complete; release left installed at $CURRENT_LINK (disabled), data at $DATA_DIR"
}
if [ "$MODE" = rollback ]; then rollback; exit 0; fi

# -- 1. identify --------------------------------------------------------------------
if [ -z "$OLD_USER" ]; then
    for f in /home/*/.config/systemd/user/holdem-api.service /root/.config/systemd/user/holdem-api.service; do
        [ -f "$f" ] || continue
        OLD_USER="$(stat -c %U "$f")"; break
    done
fi
[ -n "$OLD_USER" ] || die "no old deployment found (no ~/.config/systemd/user/holdem-api.service); pass --old-user"
OLD_HOME="$(getent passwd "$OLD_USER" | cut -d: -f6)"
OLD_UNIT_DIR="$OLD_HOME/.config/systemd/user"
OLD_API_UNIT="$OLD_UNIT_DIR/holdem-api.service"
[ -f "$OLD_API_UNIT" ] || die "$OLD_API_UNIT missing"
OLD_REPO="$(sed -n 's/^WorkingDirectory=//p' "$OLD_API_UNIT" | head -1)"
OLD_DATA="$(grep -o 'HOLDEM_API_DATA_DIR=[^ ]*' "$OLD_API_UNIT" | head -1 | cut -d= -f2-)"
OLD_ENV="$(sed -n 's/^EnvironmentFile=-\{0,1\}//p' "$OLD_API_UNIT" | head -1 | sed "s|%h|$OLD_HOME|")"
OLD_PORT="$(env_get "$OLD_ENV" HOLDEM_API_PORT)"; OLD_PORT="${OLD_PORT:-8000}"
OLD_KEY="$(env_get "$OLD_ENV" HOLDEM_API_KEY)"
OLD_DB="$OLD_DATA/solves.db"
echo "old deployment:"
echo "  user      $OLD_USER (user units in $OLD_UNIT_DIR)"
echo "  units     $OLD_UNITS"
echo "  active    $(user_ctl "$OLD_USER" is-active holdem-api.service 2>/dev/null || true)"
echo "  checkout  $OLD_REPO"
echo "  data      $OLD_DATA ($( [ -f "$OLD_DB" ] && echo "solves.db present" || echo "NO solves.db"))"
echo "  env       $OLD_ENV"
echo "  port      $OLD_PORT"
echo "release target: $PREFIX, $DATA_DIR, $ENV_FILE, port $API_PORT"
[ -f "$OLD_DB" ] || die "no $OLD_DB"
[ "$OLD_PORT" = "$API_PORT" ] || log "note: release port $API_PORT != old port $OLD_PORT"
if [ -z "$JOB" ]; then
    JOB="$(python3 - "$OLD_DB" <<'PY'
import sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
row = db.execute("SELECT id FROM jobs WHERE status='done' AND strategy_path IS NOT NULL "
                 "ORDER BY finished_at DESC LIMIT 1").fetchone()
print(row[0] if row else "")
PY
)"
fi
echo "  check job ${JOB:-<none: /node check skipped>}"
if [ "$MODE" = dry ]; then echo "dry run: nothing changed"; exit 0; fi
[ ! -L "$CURRENT_LINK" ] || die "a release is already installed at $CURRENT_LINK; this script is for the first cutover"

# -- 2. back up -------------------------------------------------------------------------
STATE="$CUTOVER_ROOT/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$STATE/units"
chmod 700 "$CUTOVER_ROOT" "$STATE"
HOLDEM_DATA_DIR="$OLD_DATA" HOLDEM_PREFIX=/nonexistent "$HERE/backup.sh" "$STATE/db" >/dev/null
cp -a "$OLD_UNIT_DIR"/holdem-* "$STATE/units/"
install -m 600 "$OLD_ENV" "$STATE/old.env"
cat > "$STATE/state.env" <<EOS
OLD_USER=$OLD_USER
OLD_PORT=$OLD_PORT
OLD_DATA=$OLD_DATA
OLD_KEY=$OLD_KEY
CHECK_JOB=$JOB
EOS
chmod 600 "$STATE/state.env"
log "backup -> $STATE (db, units, env)"

# Failures from here on restore the old units.
trap 'rc=$?; if [ $rc -ne 0 ]; then log "cutover failed (rc=$rc) — rolling back"; rollback "$STATE"; log "rolled back; cutover state in $STATE"; fi' EXIT

# -- 3. stop the old units --------------------------------------------------------------
# shellcheck disable=SC2086
user_ctl "$OLD_USER" stop $OLD_UNITS
# shellcheck disable=SC2086
user_ctl "$OLD_USER" disable $OLD_UNITS
for _ in $(seq 30); do [ -z "$(ss -Hltn "sport = :$OLD_PORT")" ] && break; sleep 1; done
log "old units stopped + disabled; :$OLD_PORT free"

# -- 4. install (not started), migrate ------------------------------------------------
HOLDEM_NO_START=1 "$HERE/install.sh" ${VERSION:+"$VERSION"}
if [ -n "$OLD_KEY" ]; then
    sed -i "s|^HOLDEM_API_KEY=.*|HOLDEM_API_KEY=$OLD_KEY|" "$ENV_FILE"
    log "kept the old API key in $ENV_FILE"
fi
mkdir -p "$DATA_DIR"
find "$OLD_DATA" -maxdepth 1 -type f \( -name 'strategy-*' -o -name '*.evs.npz' \) -exec cp -a {} "$DATA_DIR/" \;
python3 - "$STATE/db/solves.db" "$OLD_DATA" "$DATA_DIR" <<'PY'
import sqlite3, sys
db, old, new = sys.argv[1], sys.argv[2].rstrip("/"), sys.argv[3].rstrip("/")
with sqlite3.connect(db) as c:
    n = c.execute("UPDATE jobs SET strategy_path = ? || substr(strategy_path, ?) "
                  "WHERE strategy_path LIKE ? || '/%'", (new, len(old) + 1, old)).rowcount
print(f"rewrote strategy_path for {n} job(s): {old} -> {new}")
PY
chown -R "$HOLDEM_USER:$HOLDEM_GROUP" "$DATA_DIR"
"$HERE/restore.sh" "$STATE/db"   # starts holdem-api (+ workers via Wants=)

# -- 5. start + verify ---------------------------------------------------------------------
systemctl start holdem-solver.target
wait_healthy 240 || die "release API not healthy"
curl -fsS "http://$API_HOST:$API_PORT/healthz" | jq -c .
if [ -n "$JOB" ]; then
    node_ok "$API_PORT" "$(api_key)" "$JOB" || die "release API: /node for pre-cutover job $JOB failed"
    log "release serves /node for pre-cutover job $JOB"
fi
trap - EXIT
log "cutover complete. Rollback: sudo $HERE/cutover.sh --rollback   (state: $STATE)"
