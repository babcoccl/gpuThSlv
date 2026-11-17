#!/usr/bin/env bash
# Launch the holdem API on the fake engine for Playwright e2e runs.
set -euo pipefail
cd "$(dirname "$0")/../.."

export HOLDEM_SOLVER_ENGINE=fake
export HOLDEM_API_KEY="${HOLDEM_UI_TEST_KEY:-e2e-ui-key}"
export HOLDEM_API_DATA_DIR="${HOLDEM_UI_DATA_DIR:-/tmp/holdem-ui-e2e-data}"
export HOLDEM_SOLVER_RUNTIME_DIR="${HOLDEM_UI_RUNTIME_DIR:-/tmp/holdem-ui-e2e-rt}"
export HOLDEM_SOLVER_SHM_DIR="${HOLDEM_UI_SHM_DIR:-/dev/shm/holdem-ui-e2e}"
export HOLDEM_GPUS=0
export HOLDEM_API_PORT="${HOLDEM_UI_PORT:-8317}"
export HOLDEM_API_HOST=127.0.0.1

rm -rf "$HOLDEM_API_DATA_DIR" "$HOLDEM_SOLVER_RUNTIME_DIR" "$HOLDEM_SOLVER_SHM_DIR"
mkdir -p "$HOLDEM_API_DATA_DIR" "$HOLDEM_SOLVER_RUNTIME_DIR" "$HOLDEM_SOLVER_SHM_DIR"
chmod 700 "$HOLDEM_SOLVER_RUNTIME_DIR" "$HOLDEM_SOLVER_SHM_DIR"

exec .venv-api/bin/python -m holdem_solver.api
