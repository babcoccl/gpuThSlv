#!/usr/bin/env bash
# Restore a backup produced by backup.sh into the data dir.
# Stops the API first (workers keep running), swaps in the backup DB, then
# restarts. Strategy files are untouched: jobs whose strategy file was
# deleted report strategy_available=false until regen_evs rebuilds the EVs.
#
#   sudo scripts/release/restore.sh <backup-dir>
source "$(dirname "$0")/common.sh"

SRC="${1:?usage: restore.sh <backup-dir containing solves.db>}"
[ -f "$SRC/solves.db" ] || die "no solves.db in $SRC"

stop_units() {
    [ -n "$NO_SYSTEMD" ] && return 0
    systemctl stop holdem-api.service
}
start_units() {
    [ -n "$NO_SYSTEMD" ] && return 0
    systemctl start holdem-api.service
}

stop_units
cp -a "$SRC/solves.db" "$DATA_DIR/solves.db"
# Drop the old database's WAL/SHM: a leftover WAL would be replayed over the
# restored main file on next open.
rm -f "$DATA_DIR/solves.db-wal" "$DATA_DIR/solves.db-shm"
if [ "$(id -u)" -eq 0 ]; then chown "$HOLDEM_USER:$HOLDEM_GROUP" "$DATA_DIR/solves.db"; fi
start_units

if [ -z "$NO_SYSTEMD" ]; then
    wait_healthy 60 || die "API did not come up healthy after restore"
fi
log "restored $SRC -> $DATA_DIR/solves.db"
