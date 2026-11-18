#!/usr/bin/env bash
# Run both venv test suites and verify every tests/test_*.py file is
# collected by at least one of them.
#
#   scripts/run_all_tests.sh [--quick]
#
# --quick runs only --collect-only for the coverage check (no test run).
set -uo pipefail
cd "$(dirname "$0")/.."

API=.venv-api/bin/python
ENGINE=.venv-engine/bin/python

collected_files() {
    "$1" -m pytest --collect-only -q tests 2>/dev/null | grep '::' | cut -d: -f1 | sort -u
}

if [[ "${1:-}" != "--quick" ]]; then
    echo "=== engine suite (.venv-engine, Python 3.9) ==="
    "$ENGINE" -m pytest -q -rs tests
    engine_rc=$?
    # Workers spawned by engine tests exit via their parent watchdog; let them
    # release per-GPU socket locks before the API suite runs.
    for _ in $(seq 50); do pgrep -f holdem_solver.engine_worker >/dev/null || break; sleep 0.2; done
    echo "=== api suite (.venv-api, Python 3.15) ==="
    "$API" -m pytest -q -rs -m "gpu or not gpu" tests
    api_rc=$?
else
    engine_rc=0
    api_rc=0
fi

echo "=== collection coverage ==="
api_files=$(collected_files "$API")
engine_files=$(collected_files "$ENGINE")
missing=$(comm -23 <(ls tests/test_*.py | sort) <(printf '%s\n%s\n' "$api_files" "$engine_files" | sort -u))
if [[ -n "$missing" ]]; then
    echo "FAIL: test files collected by neither venv:"
    echo "$missing"
    exit 1
fi
echo "every tests/test_*.py is collected by at least one venv"

echo "=== ui contract checks (ui/) ==="
ui_rc=0
if [[ "${1:-}" != "--quick" ]]; then
    (cd ui && npx tsc -b && npx eslint . && node scripts/check-bodies.mjs && node scripts/check-no-fetch.mjs && bash scripts/check-schema.sh) || ui_rc=1
else
    (cd ui && npx tsc -b && npx eslint .) || ui_rc=1
fi
if [[ $ui_rc -ne 0 ]]; then
    echo "FAIL: ui contract checks failed"
fi

if [[ ${engine_rc:-0} -ne 0 || ${api_rc:-0} -ne 0 || $ui_rc -ne 0 ]]; then
    echo "FAIL: a suite failed (engine=$engine_rc api=$api_rc ui=$ui_rc)"
    exit 1
fi
echo "PASS"
