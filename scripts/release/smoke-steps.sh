# Shared acceptance steps for smoke.sh (no systemd, service.sh) and
# acceptance-root.sh (real system units). Source after common.sh, don't run.
#
# Caller provides: HERE, WORK (scratch dir), ENGINE (real|fake), TIER and
#   svc_stop_api   stop the API only (restore drill)
#   svc_start_api  start the API again after restore.sh
#   svc_diag       dump logs when a health check fails

step() { printf '\n=== %s ===\n' "$*"; }

acceptance_header() { # acceptance_header <script> <tier> <engine> <how>
    echo "# $1"
    echo "# tier:   $2"
    echo "# engine: $3"
    echo "# how:    $4"
    echo "# host:   $(uname -srm); $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-?}")"
    echo "# date:   $(date -Is)"
    echo "# git:    $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo nogit)$(git -C "$REPO" diff --quiet 2>/dev/null || echo ' (dirty)')"
    if [ "$3" = fake ]; then
        echo "# NOTE:   fake engine — GPU behaviour (NVML, gpu_cfr, /dev/nvidia*) NOT exercised"
    fi
}

BASE="http://$API_HOST:$API_PORT"
auth() { echo "Authorization: Bearer $(api_key)"; }
if [ "$ENGINE" = fake ]; then
    # Real-shaped tree keys so /node works; slow enough that two concurrent
    # solves land on different workers.
    SOLVER_OPTS=',"solver_options":{"fake_real_keys":true,"fake_iter_seconds":0.04}'
else
    SOLVER_OPTS=''
fi

solve() { # solve <body-without-closing-brace> -> job id
    curl -fsS -H "$(auth)" -H 'Content-Type: application/json' -X POST "$BASE/v1/solves" \
        -d "$1$SOLVER_OPTS}" | jq -r .id
}
wait_done() {
    local st
    for _ in $(seq 300); do
        st=$(curl -fsS -H "$(auth)" "$BASE/v1/solves/$1" | jq -r .status)
        case "$st" in done) return 0;; failed|cancelled) return 1;; esac
        sleep 2
    done
    return 1
}

smoke_health() {
    wait_healthy 240 || { svc_diag; die "install not healthy"; }
    curl -fsS "$BASE/healthz" | jq -c .
    curl -fsS "$BASE/healthz" | grep -q '"status":"ok"' || die "healthz not ok"
    curl -fsS "$BASE/version" | jq -c .
    curl -fsS "$BASE/version" | grep -q '"version"' || die "/version missing"
    echo "install healthy"
}

smoke_login() {
    step "login + cookie"
    curl -fsS -c "$WORK/cookies.txt" -H 'Content-Type: application/json' \
        -d "{\"key\":\"$(api_key)\"}" "$BASE/v1/auth/login" | grep -q csrf || die "login failed"
    grep -q holdem_session "$WORK/cookies.txt" && grep -q '#HttpOnly' "$WORK/cookies.txt" \
        || die "session cookie missing/!HttpOnly"
    echo "login ok, HttpOnly cookie set"
}

smoke_estimate_solves() {
    step "estimate + solves on GPU 0 and GPU 1"
    curl -fsS -H "$(auth)" -H 'Content-Type: application/json' -X POST "$BASE/v1/estimate" \
        -d '{"spot":{"board":"KsJsTh7d2c","range0":"QQ+, AKs","range1":"TT, AQ","pot":20,"stack":100,"bet_sizes":"standard"}}' \
        | jq -e '.num_nodes > 0' || die "estimate failed"
    echo "estimate ok"
    JOB=$(solve '{"board":"KsJsTh7d2c","range0":"QQ+, AKs","range1":"TT, AQ","pot":20,"stack":100,"bet_sizes":"standard","iterations":50,"progress_every":10')
    JOB2=$(solve '{"board":"AhKhQdJcTs","range0":"AA, KK","range1":"QQ, JJ","pot":20,"stack":100,"bet_sizes":"standard","iterations":50,"progress_every":10')
    wait_done "$JOB" || die "job $JOB failed"
    wait_done "$JOB2" || die "job $JOB2 failed"
    local used want
    used=$(for j in "$JOB" "$JOB2"; do curl -fsS -H "$(auth)" "$BASE/v1/solves/$j" | jq -r .gpu; done | sort -u | tr '\n' ' ')
    want="$(echo "$HOLDEM_GPUS" | tr ',' '\n' | sort -u | tr '\n' ' ')"
    echo "solves done; workers used: $used (configured: $want)"
    [ "$used" = "$want" ] || die "expected both workers ($want) to run a solve, got: $used"
}

smoke_node() {
    step "/node on the finished job"
    curl -fsS -H "$(auth)" "$BASE/v1/solves/$JOB/node" | jq -e 'type == "object"' >/dev/null \
        || die "/node failed for $JOB"
    echo "/node ok"
}

smoke_sse() {
    step "SSE reconnect"
    local job3 last
    job3=$(solve '{"board":"KsJsTh7d2c","range0":"QQ+, AKs","range1":"TT, AQ","pot":20,"stack":100,"bet_sizes":"standard","iterations":200,"progress_every":5')
    timeout 20 curl -fsSN -H "$(auth)" "$BASE/v1/solves/$job3/events" > "$WORK/sse1.txt" 2>/dev/null || true
    last=$(grep '^id:' "$WORK/sse1.txt" | tail -1 | cut -d' ' -f2 | tr -d '\r')
    [ -n "$last" ] || die "no SSE id on first connect"
    timeout 20 curl -fsSN -H "$(auth)" -H "Last-Event-ID: $last" \
        "$BASE/v1/solves/$job3/events" > "$WORK/sse2.txt" 2>/dev/null || true
    grep -q 'event: snapshot' "$WORK/sse2.txt" || die "reconnect missing snapshot"
    echo "SSE reconnect ok (last id $last)"
}

smoke_ui() {
    step "UI"
    curl -fsS "$BASE/ui/" | grep -qi '<html' || die "/ui/ did not serve the SPA"
    echo "ui ok"
}

smoke_restore() {
    step "restore drill"
    local bk
    bk="$("$HERE/backup.sh" | tail -1)"
    svc_stop_api
    rm -f "$DATA_DIR/solves.db" "$DATA_DIR/solves.db-wal" "$DATA_DIR/solves.db-shm"
    "$HERE/restore.sh" "$bk"
    svc_start_api
    wait_healthy 120 || { svc_diag; die "not healthy after restore"; }
    curl -fsS -H "$(auth)" "$BASE/v1/solves/$JOB/node" | jq -e . >/dev/null \
        || die "restored job $JOB not served"
    echo "restored job $JOB still served"
}

smoke_upgrade() {
    step "upgrade"
    V2="$1"
    "$HERE/upgrade.sh" "$V2" || die "upgrade failed"
    curl -fsS -H "$(auth)" "$BASE/v1/solves/$JOB" | jq -e '.id' >/dev/null || die "old job lost"
    [ "$(readlink -f "$CURRENT_LINK")" = "$(readlink -f "$RELEASES/$V2")" ] || die "current not on $V2"
    echo "upgrade to $V2 healthy, old job served"
}

smoke_rollback() {
    step "rollback on failed health check"
    local v3="$1" rc
    set +e
    HOLDEM_BREAK_RELEASE=holdem_solver/api/app.py "$HERE/upgrade.sh" "$v3"
    rc=$?
    set -e
    [ "$rc" -ne 0 ] || die "expected upgrade to $v3 to fail and roll back"
    [ "$(readlink -f "$CURRENT_LINK")" = "$(readlink -f "$RELEASES/$V2")" ] \
        && echo "rolled back to $V2" || die "current not on previous release"
    wait_healthy 120 || die "stack unhealthy after rollback"
    curl -fsS -H "$(auth)" "$BASE/v1/solves/$JOB/node" | jq -e . >/dev/null || die "old job lost after rollback"
    echo "stack healthy on rolled-back release, old job served"
}
