#!/usr/bin/env bash
# Phase 9 endpoint demo: real solves on both GPUs, curl output tee'd to
# scripts/phase9/logs/. Run with the engine workers' runtime/shm dirs set:
#   HOLDEM_SOLVER_RUNTIME_DIR=/tmp/hs-gate HOLDEM_SOLVER_SHM_DIR=/dev/shm/hs-gate
#   HOLDEM_API_KEY=demo bash scripts/phase9/curl_demo.sh
set -uo pipefail
cd "$(dirname "$0")/../.."

LOGS=scripts/phase9/logs
mkdir -p "$LOGS"
BASE=http://127.0.0.1:8009
KEY=${HOLDEM_API_KEY:-demo}
AUTH=(-H "Authorization: Bearer $KEY")

# Solve one cap-0 river spot on each GPU (priority ordering pins gpu0 first).
SPOT_A='{"board":"KsJsTh7d2c","range0":"QQ+, AKs","range1":"TT, AQ","pot":20.0,"stack":100.0,"bet_sizes":"standard","iterations":400,"progress_every":50}'
SPOT_B='{"board":"Ks7h2d5c9s","range0":"AKo:0.5,QQ:0.75,22","range1":"AKo:0.25,JJ:0.5,TT","pot":20.0,"stack":100.0,"bet_sizes":[0.5],"raise_cap":0,"iterations":400,"progress_every":50}'

log() {  # log <name> <cmd...>
  local name=$1; shift
  { echo "\$ $*"; "$@" 2>&1; echo; } | tee -a "$LOGS/$name.txt"
}

log solves_a curl -sS -X POST "$BASE/v1/solves" "${AUTH[@]}" -H 'content-type: application/json' -d "$SPOT_A"
log solves_b curl -sS -X POST "$BASE/v1/solves" "${AUTH[@]}" -H 'content-type: application/json' -d "$SPOT_B"

IDA=$(python3 -c "import json;print(json.load(open('$LOGS/solves_a.txt'))[0] if False else '')" 2>/dev/null)
IDA=$(grep -o '"id": *"[^"]*"' "$LOGS/solves_a.txt" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
IDB=$(grep -o '"id": *"[^"]*"' "$LOGS/solves_b.txt" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
echo "job ids: $IDA $IDB" | tee "$LOGS/jobs.txt"

# SSE while solving (5 s cap)
log events_sse timeout 5 curl -sS -N "$BASE/v1/solves/$IDA/events" "${AUTH[@]}"

poll() {
  local id=$1
  for _ in $(seq 1 120); do
    s=$(curl -sS "$BASE/v1/solves/$id" "${AUTH[@]}" | grep -o '"status": *"[^"]*"' | head -1)
    case "$s" in *done*|*failed*|*cancelled*) return 0;; esac
    sleep 1
  done
  return 1
}
poll "$IDA"; poll "$IDB"

log status_a curl -sS "$BASE/v1/solves/$IDA" "${AUTH[@]}"
log status_b curl -sS "$BASE/v1/solves/$IDB" "${AUTH[@]}"
log gpus curl -sS "$BASE/v1/gpus" "${AUTH[@]}"

log tree_root curl -sS "$BASE/v1/solves/$IDA/tree" "${AUTH[@]}"
log tree_check curl -sS "$BASE/v1/solves/$IDA/tree?history=check" "${AUTH[@]}"
log tree_bet curl -sS "$BASE/v1/solves/$IDA/tree?history=check,b10" "${AUTH[@]}"
log node_root curl -sS "$BASE/v1/solves/$IDA/node" "${AUTH[@]}"
log node_combos curl -sS "$BASE/v1/solves/$IDA/node?combos=true" "${AUTH[@]}"
log node_bet curl -sS "$BASE/v1/solves/$IDA/node?history=check,b10" "${AUTH[@]}"

log tree_b curl -sS "$BASE/v1/solves/$IDB/tree" "${AUTH[@]}"
log node_b curl -sS "$BASE/v1/solves/$IDB/node" "${AUTH[@]}"

# reconnect-safe SSE replay
log events_reconnect timeout 5 curl -sS -N "$BASE/v1/solves/$IDA/events" "${AUTH[@]}" -H 'Last-Event-ID: 1'

# spots CRUD + solve
log spot_create curl -sS -X POST "$BASE/v1/spots" "${AUTH[@]}" -H 'content-type: application/json' -d '{"name":"dry-river","spot":{"board":"KsJsTh7d2c","range0":"QQ+, AKs","range1":"TT, AQ","pot":20.0,"stack":100.0,"bet_sizes":"standard"},"tags":["river","dry"],"notes":"phase 9 demo"}'
SID=$(grep -o '"id": *"[^"]*"' "$LOGS/spot_create.txt" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
log spot_get curl -sS "$BASE/v1/spots/$SID" "${AUTH[@]}"
log spot_list curl -sS "$BASE/v1/spots?tag=dry" "${AUTH[@]}"
log spot_patch curl -sS -X PATCH "$BASE/v1/spots/$SID" "${AUTH[@]}" -H 'content-type: application/json' -d '{"notes":"updated note"}'
log spot_duplicate curl -sS -X POST "$BASE/v1/spots/$SID/duplicate" "${AUTH[@]}"
log spot_solve curl -sS -X POST "$BASE/v1/spots/$SID/solve" "${AUTH[@]}" -H 'content-type: application/json' -d '{"iterations":50}'
log spot_delete curl -sS -X DELETE "$BASE/v1/spots/$SID" "${AUTH[@]}"

# pin/unpin + retention exemption
log pin curl -sS -X POST "$BASE/v1/solves/$IDA/pin" "${AUTH[@]}"
log unpin curl -sS -X POST "$BASE/v1/solves/$IDA/unpin" "${AUTH[@]}"

# browser auth: login -> session cookie + CSRF
log login curl -sS -i -X POST "$BASE/v1/auth/login" -H 'content-type: application/json' -d "{\"key\":\"$KEY\"}"
COOKIE_JAR="$LOGS/cookies.txt"
log login_jar curl -sS -c "$COOKIE_JAR" -X POST "$BASE/v1/auth/login" -H 'content-type: application/json' -d "{\"key\":\"$KEY\"}"
CSRF=$(grep -o '"csrf": *"[^"]*"' "$LOGS/login_jar.txt" | sed 's/.*"\([^"]*\)"$/\1/')
log cookie_read curl -sS -b "$COOKIE_JAR" "$BASE/v1/solves/$IDA"
log cookie_no_csrf curl -sS -b "$COOKIE_JAR" -X POST "$BASE/v1/spots" -H 'content-type: application/json' -d '{"name":"x","spot":{"board":"KsJsTh7d2c","range0":"AA","range1":"KK","pot":20.0,"stack":100.0,"bet_sizes":"standard"}}'
log cookie_csrf curl -sS -b "$COOKIE_JAR" -X POST "$BASE/v1/spots" -H 'content-type: application/json' -H "x-csrf-token: $CSRF" -d '{"name":"x","spot":{"board":"KsJsTh7d2c","range0":"AA","range1":"KK","pot":20.0,"stack":100.0,"bet_sizes":"standard"}}'
log cors_probe curl -sS -i "$BASE/healthz" -H 'Origin: https://example.com'
echo "done" | tee "$LOGS/done.txt"
