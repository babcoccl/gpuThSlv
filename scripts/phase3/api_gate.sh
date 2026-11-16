#!/usr/bin/env bash
# Phase 3 gate: curl end-to-end against the real API on both GPUs.
# Usage: scripts/phase3/api_gate.sh [port]
set -euo pipefail
cd "$(dirname "$0")/../.."

PORT="${1:-8377}"
export HOLDEM_API_KEY="${HOLDEM_API_KEY:-gate-key}"
export HOLDEM_GPUS="${HOLDEM_GPUS:-0,1}"
export HOLDEM_SOLVER_ENGINE=real
export HOLDEM_API_DATA_DIR="$(mktemp -d)"
export HOLDEM_API_PORT="$PORT"
AUTH=(-H "Authorization: Bearer $HOLDEM_API_KEY")
BASE="http://127.0.0.1:$PORT"

.venv-api/bin/python -m holdem_solver.api >"$HOLDEM_API_DATA_DIR/api.log" 2>&1 &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT

for _ in $(seq 120); do
  curl -sf "$BASE/healthz" >/dev/null && break
  sleep 0.5
done
echo "== healthz"; curl -s "$BASE/healthz"; echo
echo "== gpus";   curl -s "${AUTH[@]}" "$BASE/v1/gpus" | python3 -m json.tool
echo "== auth rejected"; curl -s -o /dev/null -w "%{http_code}\n" "$BASE/v1/gpus"

post_solve() {
  curl -s "${AUTH[@]}" -H 'Content-Type: application/json' -d "$1" "$BASE/v1/solves"
}

SPOT='"board":"KsJsTh7d2c","range0":"QQ+, AKs","range1":"TT, AQ","pot":20,"stack":100,"bet_sizes":"standard"'
echo "== post solve 1"; R1=$(post_solve "{$SPOT,\"iterations\":500,\"progress_every\":100}"); echo "$R1"
echo "== post solve 2"; R2=$(post_solve "{$SPOT,\"iterations\":500,\"progress_every\":100}"); echo "$R2"
J1=$(echo "$R1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
J2=$(echo "$R2" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

wait_done() {
  for _ in $(seq 600); do
    S=$(curl -s "${AUTH[@]}" "$BASE/v1/solves/$1")
    ST=$(echo "$S" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
    case "$ST" in done|cancelled|failed) echo "$S"; return;; esac
    sleep 1
  done
  echo '{"status":"timeout"}'
}
echo "== solve 1 final"; wait_done "$J1" | python3 -m json.tool
echo "== solve 2 final"; wait_done "$J2" | python3 -m json.tool
echo "== gpus after solves"; curl -s "${AUTH[@]}" "$BASE/v1/gpus" | python3 -m json.tool
echo "== strategy (first infosets)"; curl -s "${AUTH[@]}" "$BASE/v1/solves/$J1/strategy" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({k:v for k,v in d.items() if k!="infosets"})); print(json.dumps(d["infosets"][:4], indent=1))'

echo "== cancel a slow job"
R3=$(post_solve "{$SPOT,\"time_budget\":600}"); echo "$R3"
J3=$(echo "$R3" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
sleep 3
curl -s "${AUTH[@]}" -X DELETE "$BASE/v1/solves/$J3" | python3 -m json.tool
wait_done "$J3" | python3 -m json.tool

echo "== 422 checks"
post_solve "{$SPOT}" | python3 -m json.tool
post_solve "{$SPOT,\"target_exploitability\":0.01}" | python3 -m json.tool
post_solve "{$SPOT,\"iterations\":10,\"max_nodes\":10}" | python3 -m json.tool
echo "GATE OK"
