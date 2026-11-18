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

# 1. Backup before touching anything (DB, and the env file: install.sh may
# switch HOLDEM_SOLVER_ENGINE, which the previous release must not inherit).
"$REPO/scripts/release/backup.sh"
ENV_BAK=""
if [ -f "$ENV_FILE" ]; then
    ENV_BAK="$(mktemp "$CONF_DIR/.holdem.env.upgrade.XXXXXX")"
    cp -p "$ENV_FILE" "$ENV_BAK"
    trap 'rm -f "$ENV_BAK"' EXIT
fi

# 2. Build + install the new release (units already installed; env kept).
# The call itself is guarded below: once install.sh has flipped `current`,
# activation may already have begun, so its failure rolls back too.

restart_stack() {
    if [ -n "${HOLDEM_RESTART_CMD:-}" ]; then
        $HOLDEM_RESTART_CMD restart
    elif [ -z "$NO_SYSTEMD" ]; then
        IFS=',' read -ra gpu_ids <<< "$HOLDEM_GPUS"
        units="holdem-api.service"
        for g in "${gpu_ids[@]}"; do units="$units holdem-worker@$g.service"; done
        # A crash-looping release can exhaust StartLimitBurst; without the
        # reset, systemd refuses the rollback's restart (found in Phase 11.1).
        # shellcheck disable=SC2086
        systemctl reset-failed $units 2>/dev/null || true
        # shellcheck disable=SC2086
        systemctl restart $units
    fi
}

rollback_to_prev() {
    log "upgrade to $VERSION failed ($1) — rolling back to ${PREV:-none}"
    if [ -n "$ENV_BAK" ]; then cp -p "$ENV_BAK" "$ENV_FILE"; log "restored $ENV_FILE"; fi
    if [ -z "$PREV" ] || [ ! -d "$PREV" ]; then
        die "no previous release to roll back to; system left on $VERSION"
    fi
    if [ "$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)" = "$PREV" ]; then
        die "failure happened before the release switch; live stack untouched on $(basename "$PREV")"
    fi
    ln -sfn "$PREV" "$CURRENT_LINK"
    restart_stack || true
    if wait_healthy "$TIMEOUT"; then
        log "rollback to $(basename "$PREV") healthy; failed release left at $RELEASES/$VERSION"
        exit 1
    fi
    die "rollback also unhealthy — investigate: journalctl -u 'holdem-*'"
}

# Activation can already have begun once install.sh flipped `current`, so any
# install or restart failure rolls back too — not only a failed health check.
"$REPO/scripts/release/install.sh" "$VERSION" || rollback_to_prev "install failed"
restart_stack || rollback_to_prev "restart failed"

# 3. Health gate with automatic rollback.
if wait_healthy "$TIMEOUT"; then
    log "upgrade to $VERSION healthy"
    exit 0
fi
rollback_to_prev "health check failed"
