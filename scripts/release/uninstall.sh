#!/usr/bin/env bash
# Uninstall: stop + remove the units, remove the `current` symlink and the
# env file. `--purge` also removes every release and /var/lib/holdem.
#
#   sudo scripts/release/uninstall.sh [--purge]
source "$(dirname "$0")/common.sh"

PURGE=""
[ "${1:-}" = "--purge" ] && PURGE=1

if [ -z "$NO_SYSTEMD" ] && [ "$(id -u)" -eq 0 ]; then
    systemctl stop 'holdem-worker@*.service' holdem-api.service holdem-solver.target 2>/dev/null || true
    IFS=',' read -ra gpu_ids <<< "$HOLDEM_GPUS"
    for g in "${gpu_ids[@]}"; do systemctl disable "holdem-worker@$g.service" 2>/dev/null || true; done
    systemctl disable holdem-api.service holdem-solver.target 2>/dev/null || true
    rm -f /etc/systemd/system/holdem-api.service \
          /etc/systemd/system/holdem-worker@.service \
          /etc/systemd/system/holdem-solver.target \
          /etc/systemd/journald.conf.d/holdem.conf
    systemctl daemon-reload
    log "units removed"
else
    log "skipping unit removal (NO_SYSTEMD or non-root)"
fi

rm -f "$CURRENT_LINK"
rm -f "$ENV_FILE"
log "removed $CURRENT_LINK and $ENV_FILE"

if [ -n "$PURGE" ]; then
    rm -rf "$RELEASES" "$DATA_DIR" "$RUN_DIR"
    rmdir "$PREFIX" 2>/dev/null || true
    log "purged $RELEASES, $DATA_DIR and $RUN_DIR"
else
    log "kept $RELEASES and $DATA_DIR (pass --purge to remove)"
fi
