#!/usr/bin/env bash
# Start/stop the API + one worker per GPU directly (no systemd) — used by
# smoke.sh in throwaway prefixes and as HOLDEM_RESTART_CMD for upgrade.sh
# when HOLDEM_NO_SYSTEMD=1. Pids go to $RUN_DIR/pids/.
#
#   scripts/release/service.sh start|stop|restart|status
source "$(dirname "$0")/common.sh"

PID_DIR="$RUN_DIR/pids"
LOG_DIR_RUN="$RUN_DIR/logs"
mkdir -p "$RUN_DIR" "$PID_DIR" "$LOG_DIR_RUN"
chmod 0700 "$RUN_DIR"
[ -O "$(dirname "$RUN_DIR")" ] && chmod go-w "$(dirname "$RUN_DIR")" || true

start() {
    set -a; . "$ENV_FILE"; set +a
    export HOLDEM_API_DATA_DIR="$DATA_DIR" HOLDEM_SOLVER_RUNTIME_DIR="$RUN_DIR"
    export PYTHONPATH="$CURRENT_LINK"
    # Same as the units' WorkingDirectory: `python -m` puts the cwd first on
    # sys.path, so launching from a checkout would run the checkout's code
    # instead of the release's (found in Phase 11.1).
    cd "$CURRENT_LINK"
    IFS=',' read -ra gpu_ids <<< "$HOLDEM_GPUS"
    for g in "${gpu_ids[@]}"; do
        if [ -f "$PID_DIR/worker-$g.pid" ] && kill -0 "$(cat "$PID_DIR/worker-$g.pid")" 2>/dev/null; then
            continue
        fi
        CUDA_VISIBLE_DEVICES="$g" setsid "$CURRENT_LINK/.venv-engine/bin/python" \
            -m holdem_solver.engine_worker --engine "${HOLDEM_SOLVER_ENGINE:-real}" --gpu "$g" \
            >>"$LOG_DIR_RUN/worker-$g.log" 2>&1 &
        echo $! > "$PID_DIR/worker-$g.pid"
    done
    if [ -f "$PID_DIR/api.pid" ] && kill -0 "$(cat "$PID_DIR/api.pid")" 2>/dev/null; then
        return 0
    fi
    setsid "$CURRENT_LINK/.venv-api/bin/python" -m holdem_solver.api \
        >>"$LOG_DIR_RUN/api.log" 2>&1 &
    echo $! > "$PID_DIR/api.pid"
}

stop() {
    for f in "$PID_DIR"/*.pid; do
        [ -f "$f" ] || continue
        kill "$(cat "$f")" 2>/dev/null || true
    done
    # uvicorn drains open connections (e.g. SSE) before exiting; give it a
    # moment, then escalate so a stale process can never fake a health check.
    for _ in $(seq 1 15); do
        alive=0
        for f in "$PID_DIR"/*.pid; do
            [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null && alive=1
        done
        [ "$alive" = 0 ] && break
        sleep 1
    done
    for f in "$PID_DIR"/*.pid; do
        [ -f "$f" ] || continue
        kill -9 "$(cat "$f")" 2>/dev/null || true
        rm -f "$f"
    done
}

case "${1:?usage: service.sh start|stop|restart|status}" in
    start) start ;;
    stop) stop ;;
    restart) stop; start ;;
    status)
        health_ok && { echo healthy; exit 0; }
        echo unhealthy; exit 1 ;;
esac
