#!/usr/bin/env bash
# Regenerate src/api/openapi.json + schema.d.ts from the FastAPI app.
# Runs the API venv's Python; no server or workers needed (openapi() does
# not run the lifespan).
set -euo pipefail
cd "$(dirname "$0")/../.."
.venv-api/bin/python - <<'EOF'
import json
from holdem_solver.api import create_app

app = create_app(gpus=[], engine="fake", data_dir="/tmp/hs-openapi-gen")
with open("ui/src/api/openapi.json", "w") as fh:
    json.dump(app.openapi(), fh, indent=2, sort_keys=True)
EOF
cd ui
npx openapi-typescript src/api/openapi.json -o src/api/schema.d.ts
