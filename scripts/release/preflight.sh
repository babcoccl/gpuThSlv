# Preflight checks for install.sh — source after common.sh, don't run.
# `install.sh --check` prints PASS/FAIL/SKIP per item and changes nothing;
# install.sh runs the same checks first and aborts on any FAIL.
#
# Test hooks (tests/test_api_phase11_1.py): HOLDEM_PYTHON_ROOT,
# HOLDEM_SYSTEM_PYTHON_DIRS, HOLDEM_FORBIDDEN_PREFIXES, HOLDEM_GPUCFR_DIR,
# HOLDEM_NVIDIA_SMI, HOLDEM_REQUIRED_TOOLS, HOLDEM_SYSTEMD_UNIT_DIR,
# HOLDEM_USER_UNIT_DIRS, HOLDEM_CHECK_PROCS=0, HOLDEM_DEVICE_GROUPS,
# HOLDEM_MIN_INSTALL_FREE_BYTES.

NVIDIA_SMI="${HOLDEM_NVIDIA_SMI:-nvidia-smi}"
# Stub installs (tests) skip the UI build, so they need no npm/node.
if [ -n "$STUB_VENVS" ]; then _tools="rsync"; else _tools="rsync npm node curl"; fi
REQUIRED_TOOLS="${HOLDEM_REQUIRED_TOOLS:-$_tools}"
USER_UNIT_DIRS="${HOLDEM_USER_UNIT_DIRS:-$(ls -d /home/*/.config/systemd/user /root/.config/systemd/user 2>/dev/null | tr '\n' ' ' || true)}"
CHECK_PROCS="${HOLDEM_CHECK_PROCS:-1}"
ALLOW_COEXIST="${HOLDEM_ALLOW_COEXIST:-}"
if [ "$INSTALL_ENGINE" = fake ]; then _min_free=$((2 << 30)); else _min_free=$((10 << 30)); fi
MIN_INSTALL_FREE="${HOLDEM_MIN_INSTALL_FREE_BYTES:-$_min_free}"

PY_INSTALL_HINT="sudo env UV_PYTHON_INSTALL_DIR=$PYTHON_ROOT $(command -v uv || echo uv) python install --no-bin"

_CHECK_FAILS=0
check_item() { # check_item PASS|FAIL|SKIP <name> <detail>
    printf '%-4s  %-22s %s\n' "$1" "$2" "$3"
    [ "$1" = FAIL ] && _CHECK_FAILS=$((_CHECK_FAILS + 1))
    return 0
}

_forbidden() { # _forbidden <path> -> 0 if under a forbidden prefix
    local p="$1" pre
    for pre in $FORBIDDEN_PREFIXES; do
        case "$p/" in "${pre%/}"/*) return 0 ;; esac
    done
    return 1
}

# discover_python api|engine -> prints the chosen path (may be empty).
discover_python() {
    local kind="$1" explicit name glob d c
    if [ "$kind" = api ]; then
        explicit="$API_PYTHON"; name="python${API_PY_VERSION%.*}"; glob="cpython-$API_PY_VERSION-*"
    else
        explicit="$ENGINE_PYTHON"; name="python$ENGINE_PY_SERIES"; glob="cpython-$ENGINE_PY_SERIES.*"
    fi
    if [ -n "$explicit" ]; then echo "$explicit"; return; fi
    if [ -d "$PYTHON_ROOT" ]; then
        # uv layout: $PYTHON_ROOT/cpython-<ver>-<platform>/bin/python<maj.min>;
        # newest patch first, freethreaded builds excluded.
        c="$(find "$PYTHON_ROOT" -maxdepth 3 -path "$PYTHON_ROOT/$glob/bin/$name" ! -path '*freethreaded*' 2>/dev/null | sort -V | tail -1)"
        [ -z "$c" ] && c="$(find "$PYTHON_ROOT" -maxdepth 4 -path "*/bin/$name" ! -path '*freethreaded*' 2>/dev/null | sort -V | tail -1)"
        if [ -n "$c" ]; then echo "$c"; return; fi
    fi
    for d in $SYSTEM_PY_DIRS; do
        _forbidden "$d" && continue
        [ -x "$d/$name" ] && { echo "$d/$name"; return; }
    done
    return 0
}

# user_can <test-flag> <path>: can the service user access the path?
user_can() {
    local flag="$1" p="$2" mode bit
    if [ "$(id -un)" = "$HOLDEM_USER" ]; then
        test "$flag" "$p"; return
    fi
    if [ "$(id -u)" -eq 0 ] && id "$HOLDEM_USER" >/dev/null 2>&1; then
        runuser -u "$HOLDEM_USER" -- test "$flag" "$p"; return
    fi
    # The user does not exist yet (or we can't switch to it): it will be
    # neither owner nor group member, so the "other" bits decide.
    mode="$(stat -Lc %a "$p" 2>/dev/null)" || return 1
    case "$flag" in -r) bit=4 ;; -x) bit=1 ;; *) return 1 ;; esac
    (( (8#$mode & bit) != 0 ))
}

# traverse_blocker <path>: prints the first component the service user can't
# traverse/execute (checks the given path and its resolved real path).
traverse_blocker() {
    local p real d
    for p in "$1" "$(readlink -f "$1")"; do
        d="$(dirname "$p")"
        local parts=() cur=""
        IFS='/' read -ra parts <<< "${d#/}"
        user_can -x / || { echo /; return; }
        for seg in "${parts[@]}"; do
            [ -z "$seg" ] && continue
            cur="$cur/$seg"
            user_can -x "$cur" || { echo "$cur"; return; }
        done
        user_can -x "$p" || { echo "$p"; return; }
    done
}

_check_interp() { # _check_interp api|engine
    local kind="$1" var path real ver want inst
    if [ "$kind" = api ]; then
        var=HOLDEM_API_PYTHON; want="$API_PY_VERSION"; inst="$PY_INSTALL_HINT $API_PY_VERSION"
    else
        var=HOLDEM_ENGINE_PYTHON; want="$ENGINE_PY_SERIES.x"; inst="$PY_INSTALL_HINT $ENGINE_PY_SERIES"
    fi
    path="$(discover_python "$kind")"
    if [ -z "$path" ] || [ ! -x "$path" ]; then
        check_item FAIL "interp.$kind" "no $want interpreter found${path:+ ($path not executable)} in \$$var, $PYTHON_ROOT or $SYSTEM_PY_DIRS; install: $inst ; or set $var=/full/path"
        return
    fi
    real="$(readlink -f "$path")"
    check_item PASS "interp.$kind" "$path -> $real"
    ver="$("$path" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)"
    local ok=""
    if [ "$kind" = api ]; then [ "$ver" = "$API_PY_VERSION" ] && ok=1
    else [ "${ver%.*}" = "$ENGINE_PY_SERIES" ] && ok=1; fi
    if [ -n "$ok" ]; then
        check_item PASS "version.$kind" "$ver"
    else
        check_item FAIL "version.$kind" "$path is ${ver:-unrunnable}, need $want; install: $inst ; then set $var"
    fi
    if [ -n "$NO_SYSTEMD" ]; then
        check_item SKIP "prefix.$kind" "HOLDEM_NO_SYSTEMD=1: no units, ProtectHome not in effect"
    elif _forbidden "$path" || _forbidden "$real"; then
        check_item FAIL "prefix.$kind" "$real is under a home dir ($FORBIDDEN_PREFIXES), hidden by ProtectHome=yes; install: $inst ; then set $var"
    else
        check_item PASS "prefix.$kind" "system path"
    fi
    local blk; blk="$(traverse_blocker "$path")"
    if [ -n "$blk" ]; then
        check_item FAIL "traverse.$kind" "$HOLDEM_USER cannot traverse/execute $blk; fix: chmod o+rx on it, or install: $inst ; then set $var"
    else
        check_item PASS "traverse.$kind" "$HOLDEM_USER can execute $real"
    fi
}

_check_gpucfr() {
    if [ "$INSTALL_ENGINE" != real ]; then
        check_item SKIP gpucfr "engine=$INSTALL_ENGINE (real engine only)"; return
    fi
    local init="git -C $REPO submodule update --init third_party/GPU-CFR"
    if [ ! -d "$GPUCFR_DIR" ]; then
        check_item FAIL gpucfr "$GPUCFR_DIR missing; run: $init"; return
    fi
    if [ ! -f "$GPUCFR_DIR/gpu_cfr/__init__.py" ] \
        || ! find "$GPUCFR_DIR/gpu_cfr" -maxdepth 1 -name '*.cpython-39-*.so' 2>/dev/null | grep -q .; then
        check_item FAIL gpucfr "$GPUCFR_DIR not initialized (no gpu_cfr/*.cpython-39-*.so); run: $init"; return
    fi
    local bad
    if [ "$(id -un)" = "$HOLDEM_USER" ]; then
        bad="$(find "$GPUCFR_DIR/gpu_cfr" \( ! -readable -o -type d ! -executable \) -print -quit 2>/dev/null || true)"
    elif [ "$(id -u)" -eq 0 ] && id "$HOLDEM_USER" >/dev/null 2>&1; then
        bad="$(runuser -u "$HOLDEM_USER" -- find "$GPUCFR_DIR/gpu_cfr" \( ! -readable -o -type d ! -executable \) -print -quit 2>/dev/null || true)"
    else
        # rsync -a preserves these modes into the root-owned release copy.
        bad="$(find "$GPUCFR_DIR/gpu_cfr" \( ! -perm -o=r -o -type d ! -perm -o=x \) -print -quit 2>/dev/null || true)"
    fi
    if [ -n "$bad" ]; then
        check_item FAIL gpucfr "$HOLDEM_USER cannot read $bad; fix: chmod -R o+rX $GPUCFR_DIR/gpu_cfr"
    else
        check_item PASS gpucfr "initialized, readable by $HOLDEM_USER"
    fi
}

_check_gpus() {
    if [ "$INSTALL_ENGINE" != real ]; then
        check_item SKIP gpus "engine=$INSTALL_ENGINE (real engine only)"; return
    fi
    if ! command -v "$NVIDIA_SMI" >/dev/null 2>&1; then
        check_item FAIL gpus "nvidia-smi not found: install the NVIDIA driver"; return
    fi
    local g missing=""
    IFS=',' read -ra _gids <<< "$HOLDEM_GPUS"
    for g in "${_gids[@]}"; do
        "$NVIDIA_SMI" -i "$g" -L 2>/dev/null | grep -q "^GPU" || missing="$missing $g"
    done
    if [ -n "$missing" ]; then
        check_item FAIL gpus "GPU(s)$missing not visible to nvidia-smi (HOLDEM_GPUS=$HOLDEM_GPUS)"
    else
        check_item PASS gpus "visible: $HOLDEM_GPUS"
    fi
}

_check_port() {
    if [ -z "$(ss -Hltn "sport = :$API_PORT" 2>/dev/null)" ]; then
        check_item PASS port "$API_HOST:$API_PORT free"
    elif [ -L "$CURRENT_LINK" ] && health_ok; then
        check_item PASS port "$API_PORT in use by the installed holdem API (upgrade/reinstall)"
    else
        check_item FAIL port "$API_PORT in use: $(ss -Hltnp "sport = :$API_PORT" 2>/dev/null | head -1 | tr -s ' ')"
    fi
}

_check_units() {
    if [ -n "$NO_SYSTEMD" ]; then
        check_item SKIP units "HOLDEM_NO_SYSTEMD=1: no units installed"; return
    fi
    local conflicts="" coexist="" f d
    for f in "$SYSTEMD_UNIT_DIR"/holdem-*.service "$SYSTEMD_UNIT_DIR"/holdem-*.target \
             /lib/systemd/system/holdem-*.service /lib/systemd/system/holdem-*.target; do
        [ -f "$f" ] || continue
        case "$(basename "$f")" in
            holdem-api.service|holdem-worker@.service|holdem-solver.target)
                grep -qx "# holdem-release-unit prefix=$PREFIX .*" "$f" && continue ;;  # ours
        esac
        conflicts="$conflicts $f"
    done
    for d in $USER_UNIT_DIRS; do
        for f in "$d"/holdem-*.service "$d"/holdem-*.target; do
            [ -f "$f" ] || continue
            # A user unit only conflicts when enabled (it would start again at
            # boot and hold the port/GPUs); disabled leftovers are listed only.
            if ls "$d"/*.wants/"$(basename "$f")" >/dev/null 2>&1; then
                coexist="$coexist $f(enabled)"
            fi
        done
    done
    if [ "$CHECK_PROCS" = 1 ]; then
        local line
        while read -r line; do
            [ -n "$line" ] || continue
            case "$line" in *"$PREFIX/current/"*|*"$RELEASES/"*) continue ;; esac
            coexist="$coexist pid:${line%% *}"
        done < <(pgrep -af 'holdem_solver\.(api|engine_worker)' 2>/dev/null | grep -v pgrep || true)
    fi
    if [ -n "$conflicts" ]; then
        check_item FAIL units "foreign system units:$conflicts"
    elif [ -n "$coexist" ] && [ -z "$ALLOW_COEXIST" ]; then
        check_item FAIL units "existing deployment running/enabled:$coexist — stop it first (RUNBOOK: Cutover) or set HOLDEM_ALLOW_COEXIST=1"
    elif [ -n "$coexist" ]; then
        check_item PASS units "coexisting (HOLDEM_ALLOW_COEXIST=1):$coexist"
    else
        check_item PASS units "no conflicting holdem units or processes"
    fi
}

_check_disk() {
    local p d free
    for p in "$PREFIX" "$DATA_DIR"; do
        d="$p"
        while [ ! -d "$d" ]; do d="$(dirname "$d")"; done
        free="$(df -PB1 "$d" | awk 'NR==2 {print $4}')"
        if [ "${free:-0}" -ge "$MIN_INSTALL_FREE" ]; then
            check_item PASS "disk" "$d: $((free >> 20)) MiB free (need $((MIN_INSTALL_FREE >> 20)) MiB)"
        else
            check_item FAIL "disk" "$d: $((free >> 20)) MiB free, need $((MIN_INSTALL_FREE >> 20)) MiB (HOLDEM_MIN_INSTALL_FREE_BYTES)"
        fi
    done
}

_check_systemd() {
    if [ -n "$NO_SYSTEMD" ]; then
        check_item SKIP systemd "HOLDEM_NO_SYSTEMD=1"; return
    fi
    local pid1 state
    pid1="$(ps -p 1 -o comm= 2>/dev/null)"
    state="$(systemctl is-system-running 2>/dev/null || true)"
    case "$pid1:$state" in
        systemd:running|systemd:degraded|systemd:starting)
            check_item PASS systemd "PID 1 systemd ($state)" ;;
        *) check_item FAIL systemd "PID 1 is '${pid1:-?}' (state '${state:-?}'); system units need systemd as PID 1 (or HOLDEM_NO_SYSTEMD=1)" ;;
    esac
}

_check_groups() { # _check_groups check|install
    if [ -n "$NO_SYSTEMD" ]; then
        check_item SKIP groups "HOLDEM_NO_SYSTEMD=1 (SupplementaryGroups only in units)"; return
    fi
    local g missing=""
    for g in $DEVICE_GROUPS; do getent group "$g" >/dev/null || missing="$missing $g"; done
    if [ -z "$missing" ]; then
        check_item PASS groups "$DEVICE_GROUPS exist"
    elif [ "$1" = install ] && [ "$(id -u)" -eq 0 ]; then
        check_item PASS groups "missing:$missing — install.sh creates them (groupadd --system)"
    else
        check_item FAIL groups "missing:$missing (holdem-worker@.service SupplementaryGroups); run: sudo groupadd --system <group> (install.sh does this as root)"
    fi
}

# run_checks check|install -> 0 when no item FAILed.
run_checks() {
    local mode="${1:-check}"
    _CHECK_FAILS=0
    echo "preflight: engine=$INSTALL_ENGINE user=$HOLDEM_USER prefix=$PREFIX port=$API_PORT systemd=$([ -n "$NO_SYSTEMD" ] && echo no || echo yes)"
    if [ -n "$STUB_VENVS" ]; then
        check_item SKIP interp "HOLDEM_STUB_VENVS=1 (tests only)"
    else
        _check_interp api
        _check_interp engine
    fi
    local t missing=""
    for t in $REQUIRED_TOOLS; do command -v "$t" >/dev/null 2>&1 || missing="$missing $t"; done
    if [ -n "$missing" ]; then
        check_item FAIL tools "not on PATH:$missing (root's PATH under sudo: use sudo env PATH=\"\$PATH\" ...)"
    else
        check_item PASS tools "$REQUIRED_TOOLS"
    fi
    _check_gpucfr
    _check_gpus
    _check_port
    _check_units
    _check_disk
    _check_systemd
    _check_groups "$mode"
    if [ "$_CHECK_FAILS" -gt 0 ]; then
        echo "preflight: FAIL ($_CHECK_FAILS item(s))"
        return 1
    fi
    echo "preflight: PASS"
}
