#!/usr/bin/env bash
# Phase 8 suite: weighted-range cross-validation + step-3 cross-evaluation.
# Usage: scripts/phase8/run_suite.sh [spot_name ...]
set -euo pipefail
cd "$(dirname "$0")/../.."
exec .venv-engine/bin/python -m scripts.phase8.run_suite "$@"
