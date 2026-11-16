#!/usr/bin/env bash
# Phase 0 iter_bench matrix: 3 games x {CUDA graph, eager} x 2 GPUs.
# Each GPU is handled by one sequential worker so the two GPUs run concurrently.
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENGINE_PYTHON="$ROOT_DIR/.venv-engine/bin/python"
WRAPPER="$ROOT_DIR/scripts/phase0/run_iter_bench_with_memory.py"
LOG_DIR="$ROOT_DIR/scripts/phase0/logs"
mkdir -p "$LOG_DIR"

export PYTHONPATH="$ROOT_DIR/third_party/GPU-CFR"
export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=8

GAMES=(kuhn_poker hunl_river hunl_turn)
ITERS="${ITERS:-1000}"
WARMUP="${WARMUP:-50}"
REPS="${REPS:-3}"

run_one() {
    local gpu="$1"
    local game="$2"
    local flag="$3"
    local mode="$4"
    local bench_log="$LOG_DIR/iter_bench_gpu${gpu}.jsonl"
    local stderr_log="$LOG_DIR/iter_bench_gpu${gpu}.stderr.log"
    local status_log="$LOG_DIR/iter_bench_status_gpu${gpu}.tsv"
    local start end status

    start="$(date -Is)"
    echo "start gpu=$gpu game=$game mode=$mode flag=$flag iters=$ITERS warmup=$WARMUP reps=$REPS" >> "$status_log"
    CUDA_VISIBLE_DEVICES="$gpu" "$ENGINE_PYTHON" "$WRAPPER" \
        --game "$game" \
        --device cuda \
        --variant cfr_plus \
        --iters "$ITERS" \
        --warmup "$WARMUP" \
        --reps "$REPS" \
        "$flag" >> "$bench_log" 2>> "$stderr_log"
    status=$?
    end="$(date -Is)"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$start" "$end" "$gpu" "$game" "$mode" "$status" >> "$status_log"
}

run_gpu() {
    local gpu="$1"
    local game mode flag
    for game in "${GAMES[@]}"; do
        for mode in graph eager; do
            if [ "$mode" = graph ]; then
                flag="--cuda-graph"
            else
                flag="--no-cuda-graph"
            fi
            run_one "$gpu" "$game" "$flag" "$mode"
        done
    done
}

: > "$LOG_DIR/iter_bench_gpu0.jsonl"
: > "$LOG_DIR/iter_bench_gpu1.jsonl"
: > "$LOG_DIR/iter_bench_gpu0.stderr.log"
: > "$LOG_DIR/iter_bench_gpu1.stderr.log"
: > "$LOG_DIR/iter_bench_status_gpu0.tsv"
: > "$LOG_DIR/iter_bench_status_gpu1.tsv"

run_gpu 0 &
pid0=$!
run_gpu 1 &
pid1=$!

wait "$pid0"
status0=$?
wait "$pid1"
status1=$?

printf 'matrix_done gpu0_worker_status=%s gpu1_worker_status=%s iters=%s warmup=%s reps=%s\n' \
    "$status0" "$status1" "$ITERS" "$WARMUP" "$REPS" >> "$LOG_DIR/iter_bench_matrix.log"

if [ "$status0" -ne 0 ] || [ "$status1" -ne 0 ]; then
    exit 1
fi
exit 0
