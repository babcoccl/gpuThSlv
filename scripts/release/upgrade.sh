#!/usr/bin/env bash
# Upgrade to a new release: DB backup first, then install.sh, symlink flip,
# restart, and an automatic rollback to the previous release if /healthz
# does not go healthy within HOLDEM_UPGRADE_HEALTH_TIMEOUT (default 120 s).
#
#   sudo scripts/release/upgrade.sh <version>
source "$(dirname "$0")/common.sh"

VERSION="${1:?usage: upgrade.sh <version>}"
TIMEOUT="${HOLDEM_UPGRADE_HEALTH_TIMEOUT:-120}"

PREV="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
[ -n "$PREV" ] && log "current release: $PREV"

# 1. Backup before touching anything.
"$REPO/scripts/release/backup.sh"

# 2. Build + install the new release (units already installed; env kept).
"$REPO/scripts/release/install.sh" "$VERSION"

restart_stack() {
    if [ -n "${HOLDEM_RESTART_CMD:-}" ]; then
        $HOLDEM_RESTART_CMD restart
    elif [ -z "$NO_SYSTEMD" ]; then
        IFS=',' read -ra gpu_ids <<< "$HOLDEM_GPUS"
        units="holdem-api.service"
        for g in "${gpu_ids[@]}"; do units="$units holdem-worker@$g.service"; done
        # shellcheck disable=SC2086
        systemctl restart $units
    fi
}

restart_stack

# 3. Health gate with automatic rollback.
if wait_healthy "$TIMEOUT"; then
    log "upgrade to $VERSION healthy"
    exit 0
fi

log "health check FAILED after upgrade — rolling back to ${PREV:-none}"
if [ -z "$PREV" ] || [ ! -d "$PREV" ]; then
    die "no previous release to roll back to; system left on $VERSION"
fi
ln -sfn "$PREV" "$CURRENT_LINK"
restart_stack
if wait_healthy "$TIMEOUT"; then
    log "rollback to $(basename "$PREV") healthy; failed release left at $RELEASES/$VERSION"
    exit 1
fi
die "rollback also unhealthy — investigate: journalctl -u 'holdem-*'"
