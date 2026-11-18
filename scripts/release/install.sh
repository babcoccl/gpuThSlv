#!/usr/bin/env bash
# Install a release: build /opt/holdem/releases/<version>/ with both venvs
# from the pinned requirements files, build ui/dist, link /opt/holdem/current,
# create the unprivileged `holdem` user and /var/lib/holdem, generate the API
# key and session signing key into a root-only 0600 env file (existing files
# are never overwritten), install the systemd units, restart, health-check.
#
#   sudo scripts/release/install.sh [version]
#   scripts/release/install.sh --check     # preflight only; no root, no changes
#
# Preflight (preflight.sh) runs first and aborts the install on any FAIL.
# HOLDEM_INSTALL_ENGINE=real|fake (default real; fake = no GPU, acceptance
# only) selects the engine venv requirements and HOLDEM_SOLVER_ENGINE.
#
# Idempotent: re-running for the same version rebuilds the release dir but
# keeps secrets; re-running for a new version is what upgrade.sh wraps.
#
# Overrides for throwaway installs (smoke/tests): HOLDEM_PREFIX,
# HOLDEM_DATA_DIR, HOLDEM_CONF_DIR, HOLDEM_RUN_DIR, HOLDEM_USER,
# HOLDEM_API_PORT, HOLDEM_GPUS, HOLDEM_API_PYTHON, HOLDEM_ENGINE_PYTHON,
# HOLDEM_NO_SYSTEMD=1 (skip units; processes managed by the caller),
# HOLDEM_STUB_VENVS=1 (skip pip installs; unit tests only),
# HOLDEM_NO_START=1 (install + enable units, don't restart them).
source "$(dirname "$0")/common.sh"
source "$(dirname "$0")/preflight.sh"

case "$INSTALL_ENGINE" in real|fake) ;; *) die "HOLDEM_INSTALL_ENGINE must be real or fake (got '$INSTALL_ENGINE')" ;; esac
if [ "$(id -u)" -ne 0 ] && [ -n "$NO_SYSTEMD" ]; then
    HOLDEM_USER="$(id -un)"; HOLDEM_GROUP="$(id -gn)"
fi

if [ "${1:-}" = "--check" ]; then
    run_checks check
    exit $?
fi
[ "$(id -u)" -eq 0 ] || [ -n "$NO_SYSTEMD" ] \
    || die "installing system units needs root (or HOLDEM_NO_SYSTEMD=1); try --check first"
run_checks install || die "preflight failed; fix the FAIL items above (re-run: $0 --check)"
API_PYTHON="$(discover_python api)"
ENGINE_PYTHON="$(discover_python engine)"

VERSION="${1:-${HOLDEM_VERSION:-$(date +%Y%m%d%H%M%S)-$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo nogit)}}"
RELEASE="$RELEASES/$VERSION"
[[ "$VERSION" =~ ^[A-Za-z0-9._-]+$ ]] || die "bad version '$VERSION'"
[ -f "$REPO/requirements/api.txt" ] || die "run from a gpuThSlv checkout (requirements/api.txt missing)"

log "installing version $VERSION into $RELEASE"

# -- system user + directories ------------------------------------------------
if [ "$(id -u)" -eq 0 ]; then
    if [ -z "$NO_SYSTEMD" ]; then
        for g in $DEVICE_GROUPS; do
            getent group "$g" >/dev/null || { groupadd --system "$g"; log "created group $g"; }
        done
    fi
    if ! id "$HOLDEM_USER" >/dev/null 2>&1; then
        useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$HOLDEM_USER"
        log "created system user $HOLDEM_USER"
    fi
    if [ -z "$STUB_VENVS" ]; then
        # Re-check traversal now that the real service user exists.
        for py in "$API_PYTHON" "$ENGINE_PYTHON"; do
            blk="$(traverse_blocker "$py")"
            [ -z "$blk" ] || die "$HOLDEM_USER cannot traverse $blk on the way to $py (chmod o+rx it)"
        done
    fi
fi
mkdir -p "$RELEASES" "$DATA_DIR" "$CONF_DIR" "$RUN_DIR"

# -- release tree --------------------------------------------------------------
mkdir -p "$RELEASE"
# --no-owner/--no-group: as root, plain -a would copy the checkout owner's
# uid into the release; the tree must be root-owned (found in Phase 11.1).
rsync -a --no-owner --no-group --delete \
    --exclude='.git/' --exclude='.venv-*/' --exclude='api-data/' \
    --exclude='ui/node_modules/' --exclude='ui/dist/' \
    --exclude='__pycache__/' --exclude='*.pyc' \
    "$REPO/" "$RELEASE/"
# rsync -a applies the checkout's own mode to the release dir; the service
# user must be able to enter it.
chmod 0755 "$RELEASE"
git -C "$REPO" rev-parse HEAD > "$RELEASE/GIT_SHA" 2>/dev/null || true
# Test hook (smoke.sh only): delete a file from the release to simulate a
# bad build so the upgrade health check fails and rollback is exercised.
if [ -n "${HOLDEM_BREAK_RELEASE:-}" ]; then
    rm -f "$RELEASE/$HOLDEM_BREAK_RELEASE"
fi

# -- venvs from pinned requirements --------------------------------------------
# A deliberately broken release (test hook) skips the slow venv/UI builds: the
# missing runtime is exactly what the health check must catch.
if [ -n "${HOLDEM_BREAK_RELEASE:-}" ]; then
    log "HOLDEM_BREAK_RELEASE set: skipping venv/UI build"
elif [ -n "$STUB_VENVS" ]; then
    mkdir -p "$RELEASE/.venv-api/bin" "$RELEASE/.venv-engine/bin"
    for py in .venv-api/bin/python .venv-engine/bin/python; do
        printf '#!/bin/sh\nexec %s "$@"\n' "$(command -v python3)" > "$RELEASE/$py"
        chmod +x "$RELEASE/$py"
    done
else
    # Interpreters were discovered and validated by the preflight (version,
    # no home-dir prefix under systemd, service-user traversal).
    if [ ! -x "$RELEASE/.venv-api/bin/python" ]; then
        log "building .venv-api ($API_PYTHON)"
        "$API_PYTHON" -m venv "$RELEASE/.venv-api"
    fi
    # pip is idempotent; always run it so a retry after a failed download
    # completes the environment instead of activating a partial one.
    "$RELEASE/.venv-api/bin/pip" install --no-input -r "$RELEASE/requirements/api.txt"
    if [ ! -x "$RELEASE/.venv-engine/bin/python" ]; then
        log "building .venv-engine ($ENGINE_PYTHON)"
        "$ENGINE_PYTHON" -m venv "$RELEASE/.venv-engine"
    fi
    # The fake engine needs no torch/CUDA wheels (docs/PHASE11_1.md).
    engine_reqs="engine.txt"; [ "$INSTALL_ENGINE" = fake ] && engine_reqs="engine-fake.txt"
    "$RELEASE/.venv-engine/bin/pip" install --no-input -r "$RELEASE/requirements/$engine_reqs"
fi

# -- UI ------------------------------------------------------------------------
if [ -n "${HOLDEM_BREAK_RELEASE:-}" ]; then
    :
elif [ -n "$STUB_VENVS" ]; then
    mkdir -p "$RELEASE/ui/dist"; echo stub > "$RELEASE/ui/dist/index.html"
elif [ ! -f "$RELEASE/ui/dist/index.html" ]; then
    log "building ui/dist (npm ci && npm run build)"
    (cd "$RELEASE/ui" && npm ci && npm run build)
fi

# -- env file (0600 root; secrets generated once, never overwritten) -----------
if [ ! -f "$ENV_FILE" ]; then
    umask 077  # secrets must never exist in a group/world-readable file
    py="${API_PYTHON:-python3}"
    api_key="$("$py" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    sess_key="$("$py" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    {
        echo "# holdem-solver secrets — generated by install.sh, root-only."
        echo "HOLDEM_API_KEY=$api_key"
        echo "HOLDEM_SESSION_SECRET=$sess_key"
        echo "HOLDEM_API_HOST=$API_HOST"
        echo "HOLDEM_API_PORT=$API_PORT"
        echo "HOLDEM_GPUS=$HOLDEM_GPUS"
        echo "HOLDEM_WORKER_MODE=external"
        echo "HOLDEM_SOLVER_ENGINE=$INSTALL_ENGINE"
        echo "HOLDEM_SECURE_COOKIES=1"
        echo "HOLDEM_GIT_SHA=$(cat "$RELEASE/GIT_SHA" 2>/dev/null || true)"
        grep -v '^HOLDEM_API_KEY=\|^HOLDEM_SESSION_SECRET=\|^HOLDEM_API_HOST=\|^HOLDEM_API_PORT=\|^HOLDEM_GPUS=\|^HOLDEM_WORKER_MODE=\|^HOLDEM_SOLVER_ENGINE=\|^HOLDEM_SECURE_COOKIES=\|^HOLDEM_GIT_SHA=' \
            "$REPO/deploy/holdem.env.example" | grep -v '^#' | grep -v '^$' || true
    } > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    log "wrote $ENV_FILE (new API key + session secret)"
else
    log "keeping existing $ENV_FILE"
    # version change only: record the SHA this release was built from.
    sed -i "s|^HOLDEM_GIT_SHA=.*|HOLDEM_GIT_SHA=$(cat "$RELEASE/GIT_SHA" 2>/dev/null)|" "$ENV_FILE" \
        || echo "HOLDEM_GIT_SHA=$(cat "$RELEASE/GIT_SHA" 2>/dev/null)" >> "$ENV_FILE"
    if grep -q '^HOLDEM_SOLVER_ENGINE=' "$ENV_FILE"; then
        sed -i "s|^HOLDEM_SOLVER_ENGINE=.*|HOLDEM_SOLVER_ENGINE=$INSTALL_ENGINE|" "$ENV_FILE"
    else
        echo "HOLDEM_SOLVER_ENGINE=$INSTALL_ENGINE" >> "$ENV_FILE"
    fi
fi

# -- ownership ------------------------------------------------------------------
if [ "$(id -u)" -eq 0 ]; then
    chown -R "$HOLDEM_USER:$HOLDEM_GROUP" "$DATA_DIR" "$RUN_DIR"
    # The release tree stays root-owned: the service user must not be able to
    # rewrite code or scripts that later run as root or in its own units.
fi

# -- flip current ---------------------------------------------------------------
ln -sfn "$RELEASE" "$CURRENT_LINK"
log "current -> $RELEASE"

# -- systemd --------------------------------------------------------------------
if [ -z "$NO_SYSTEMD" ]; then
    for unit in "$REPO"/deploy/systemd/system/*.service "$REPO"/deploy/systemd/system/*.target; do
        render_unit "$unit" "/etc/systemd/system/$(basename "$unit")"
    done
    install -D -m 644 "$REPO/deploy/systemd/holdem-journald.conf" \
        /etc/systemd/journald.conf.d/holdem.conf
    systemctl daemon-reload
    systemctl restart systemd-journald || true
    gpu_units=""
    IFS=',' read -ra gpu_ids <<< "$HOLDEM_GPUS"
    for g in "${gpu_ids[@]}"; do gpu_units="$gpu_units holdem-worker@$g.service"; done
    # shellcheck disable=SC2086
    systemctl enable holdem-solver.target holdem-api.service $gpu_units
    if [ -n "${HOLDEM_NO_START:-}" ]; then
        log "units installed + enabled (HOLDEM_NO_START=1: not started)"
    else
        # shellcheck disable=SC2086
        systemctl restart $gpu_units holdem-api.service
        log "units installed and restarted"
    fi
else
    log "HOLDEM_NO_SYSTEMD=1: skipping unit install"
fi

log "installed $VERSION"
echo "$VERSION"
