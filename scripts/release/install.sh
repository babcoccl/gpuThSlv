#!/usr/bin/env bash
# Install a release: build /opt/holdem/releases/<version>/ with both venvs
# from the pinned requirements files, build ui/dist, link /opt/holdem/current,
# create the unprivileged `holdem` user and /var/lib/holdem, generate the API
# key and session signing key into a root-only 0600 env file (existing files
# are never overwritten), install the systemd units, restart, health-check.
#
#   sudo scripts/release/install.sh [version]
#
# Idempotent: re-running for the same version rebuilds the release dir but
# keeps secrets; re-running for a new version is what upgrade.sh wraps.
#
# Overrides for throwaway installs (smoke/tests): HOLDEM_PREFIX,
# HOLDEM_DATA_DIR, HOLDEM_CONF_DIR, HOLDEM_RUN_DIR, HOLDEM_USER,
# HOLDEM_API_PORT, HOLDEM_GPUS, HOLDEM_API_PYTHON, HOLDEM_ENGINE_PYTHON,
# HOLDEM_NO_SYSTEMD=1 (skip units; processes managed by the caller),
# HOLDEM_STUB_VENVS=1 (skip pip installs; unit tests only).
source "$(dirname "$0")/common.sh"

VERSION="${1:-${HOLDEM_VERSION:-$(date +%Y%m%d%H%M%S)-$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo nogit)}}"
RELEASE="$RELEASES/$VERSION"
[[ "$VERSION" =~ ^[A-Za-z0-9._-]+$ ]] || die "bad version '$VERSION'"
[ -f "$REPO/requirements/api.txt" ] || die "run from a gpuThSlv checkout (requirements/api.txt missing)"

log "installing version $VERSION into $RELEASE"

# -- system user + directories ------------------------------------------------
if [ "$(id -u)" -eq 0 ]; then
    if ! id "$HOLDEM_USER" >/dev/null 2>&1; then
        useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$HOLDEM_USER"
        log "created system user $HOLDEM_USER"
    fi
else
    HOLDEM_USER="$(id -un)"; HOLDEM_GROUP="$(id -gn)"
fi
mkdir -p "$RELEASES" "$DATA_DIR" "$CONF_DIR" "$RUN_DIR"

# -- release tree --------------------------------------------------------------
mkdir -p "$RELEASE"
rsync -a --delete \
    --exclude='.git/' --exclude='.venv-*/' --exclude='api-data/' \
    --exclude='ui/node_modules/' --exclude='ui/dist/' \
    --exclude='__pycache__/' --exclude='*.pyc' \
    "$REPO/" "$RELEASE/"
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
    [ -x "$API_PYTHON" ] || die "API interpreter $API_PYTHON not found (set HOLDEM_API_PYTHON)"
    [ -x "$ENGINE_PYTHON" ] || die "engine interpreter $ENGINE_PYTHON not found (set HOLDEM_ENGINE_PYTHON)"
    # The units run with ProtectHome=yes: a venv's python is a symlink into
    # its base interpreter, so a base under /home or /root is unreachable
    # from the service. Require system-visible interpreters for real
    # (non-smoke) installs.
    if [ -z "$NO_SYSTEMD" ]; then
        for py in "$API_PYTHON" "$ENGINE_PYTHON"; do
            case "$(readlink -f "$py")" in
                /home/*|/root/*|/run/user/*)
                    die "interpreter $py resolves under a home dir, which ProtectHome=yes hides; install Python to a system path (e.g. UV_PYTHON_INSTALL_DIR=/opt/holdem/python uv python install 3.9) and set HOLDEM_API_PYTHON/HOLDEM_ENGINE_PYTHON" ;;
            esac
        done
    fi
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
    "$RELEASE/.venv-engine/bin/pip" install --no-input -r "$RELEASE/requirements/engine.txt"
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
    api_key="$("$API_PYTHON" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    sess_key="$("$API_PYTHON" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    {
        echo "# holdem-solver secrets — generated by install.sh, root-only."
        echo "HOLDEM_API_KEY=$api_key"
        echo "HOLDEM_SESSION_SECRET=$sess_key"
        echo "HOLDEM_API_HOST=$API_HOST"
        echo "HOLDEM_API_PORT=$API_PORT"
        echo "HOLDEM_GPUS=$HOLDEM_GPUS"
        echo "HOLDEM_WORKER_MODE=external"
        echo "HOLDEM_SOLVER_ENGINE=real"
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
    [ "$(id -u)" -eq 0 ] || die "installing system units needs root (or HOLDEM_NO_SYSTEMD=1)"
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
    # shellcheck disable=SC2086
    systemctl restart holdem-worker@*.service holdem-api.service 2>/dev/null \
        || systemctl restart $gpu_units holdem-api.service
    log "units installed and restarted"
else
    log "HOLDEM_NO_SYSTEMD=1: skipping unit install"
fi

log "installed $VERSION"
echo "$VERSION"
