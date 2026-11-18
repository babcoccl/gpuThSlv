#!/usr/bin/env bash
# Contract check: src/api/openapi.json and src/api/schema.d.ts must be in sync
# with the FastAPI app's openapi(). Regenerates both and fails on any diff.
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/gen-api.sh
if ! git diff --exit-code -- src/api/openapi.json src/api/schema.d.ts; then
  echo "FAIL: generated API files are stale — run npm run gen:api" >&2
  exit 1
fi
echo "check-schema: generated files in sync"
