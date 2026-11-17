"""Phase 5 soak: mixed real jobs against a running API for --seconds, then
check that no job is stuck. Writes one JSON line per event to stdout.

    .venv-api/bin/python scripts/phase5/soak.py --url http://127.0.0.1:8000 \
        --key "$HOLDEM_API_KEY" --seconds 3600 > scripts/phase5/logs/soak.jsonl

Mix per round (both GPUs kept busy): converge-to-target solves of several
sizes in float32/float64, a cancel mid-solve, a too-short timeout, an
idempotent replay, priorities, and strategy fetches in both views.
"""

import argparse
import json
import random
import time
import urllib.error
import urllib.request

BOARD = "KsJsTh7d2c"
NARROW = "QQ+, AKs"
MID = "TT+, AQs+, AKo, KQs"
WIDE = "77+, ATs+, KTs+, QTs+, JTs, AJo+, KQo"
BROAD = "22+, A2s+, K9s+, Q9s+, J9s+, T9s, 98s, ATo+, KTo+, QJo"
TERMINAL = ("done", "cancelled", "failed")


def emit(**record):
    record["ts"] = round(time.time(), 3)
    print(json.dumps(record), flush=True)


class Api:
    def __init__(self, url, key):
        self.url, self.key = url.rstrip("/"), key

    def call(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.key:
            req.add_header("Authorization", "Bearer " + self.key)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")


def spot(r0, r1, bets, **extra):
    return dict({"board": BOARD, "range0": r0, "range1": r1, "pot": 20.0, "stack": 100.0,
                 "bet_sizes": bets, "progress_every": 100, "timeout_seconds": 900}, **extra)


def round_jobs(n, rng):
    dtype = "float64" if n % 2 else "float32"
    jobs = [
        ("converge-s", spot(MID, MID, "standard", target_exploitability=1e-3, max_iterations=60000,
                            dtype=dtype, progress_every=500)),
        ("converge-m", spot(WIDE, WIDE, "standard", target_exploitability=1e-3, max_iterations=20000, dtype=dtype)),
        ("converge-l", spot(BROAD, BROAD, "standard", target_exploitability=2e-3, max_iterations=20000,
                            dtype=dtype, priority=5)),
        ("fixed-xs", spot(NARROW, NARROW, "pot", iterations=5000, dtype=dtype)),
        ("cancel", spot(BROAD, BROAD, "standard", iterations=200000, progress_every=1000)),
        ("timeout", spot(BROAD, BROAD, "standard", iterations=2000000, timeout_seconds=8, progress_every=1000)),
        ("replay", spot(MID, MID, "pot", iterations=3000, dtype=dtype)),
    ]
    if n % 6 == 0:
        jobs.append(("xl-full", spot("*", "*", "pot", target_exploitability=2e-3, max_iterations=4000,
                                     progress_every=200, dtype="float32", priority=-5)))
    rng.shuffle(jobs)
    return jobs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--key", default=None)
    parser.add_argument("--seconds", type=float, default=3600)
    parser.add_argument("--grace", type=float, default=900, help="max wait for the queue to drain at the end")
    args = parser.parse_args()
    api, rng = Api(args.url, args.key), random.Random(5)
    start, jobs, n = time.time(), {}, 0
    emit(event="start", seconds=args.seconds)
    while time.time() - start < args.seconds:
        n += 1
        for kind, payload in round_jobs(n, rng):
            headers = {"Idempotency-Key": "soak-%d-%d" % (start, n)} if kind == "replay" else None
            code, resp = api.call("POST", "/v1/solves", payload, headers)
            if code == 429 and "Retry-After" not in str(resp):
                time.sleep(2)
            if code != 202:
                emit(event="submit_rejected", kind=kind, code=code, detail=resp)
                continue
            jobs[resp["id"]] = {"kind": kind, "submitted": time.time()}
            if kind == "replay":
                code2, again = api.call("POST", "/v1/solves", payload, headers)
                emit(event="replay", ok=code2 == 202 and again["id"] == resp["id"] and again["replayed"])
        # Drive this round: cancel the long job once it runs, fetch strategies.
        round_ids = [j for j, info in jobs.items() if not info.get("final")]
        while round_ids and time.time() - start < args.seconds + args.grace:
            for job_id in list(round_ids):
                code, st = api.call("GET", "/v1/solves/" + job_id)
                info = jobs[job_id]
                if info["kind"] == "cancel" and st["status"] == "solving" and not info.get("cancel_sent"):
                    api.call("DELETE", "/v1/solves/" + job_id)
                    info["cancel_sent"] = True
                if st["status"] in TERMINAL:
                    info["final"] = st
                    round_ids.remove(job_id)
                    grid_ok = None
                    if st["strategy_available"]:
                        c1, _ = api.call("GET", "/v1/solves/%s/strategy" % job_id)
                        c2, grid = api.call("GET", "/v1/solves/%s/strategy?view=hand_grid&player=0&history=" % job_id)
                        grid_ok = c1 == 200 and c2 == 200 and all(len(nd["grid"]) == 169 for nd in grid["nodes"])
                    emit(event="job", kind=info["kind"], id=job_id, status=st["status"], gpu=st["gpu"],
                         error_code=st["error_code"], dtype=st["dtype"], num_nodes=st["num_nodes"],
                         build_seconds=st["build_seconds"], elapsed_seconds=st["elapsed_seconds"],
                         iterations_run=st["iterations_run"], exploitability=st["exploitability"],
                         exploitability_iteration=st["exploitability_iteration"],
                         target_reached=st["target_reached"], peak_vram_bytes=st["peak_vram_bytes"],
                         wall_seconds=round(time.time() - info["submitted"], 2), strategy_views_ok=grid_ok)
            time.sleep(0.5)
    stuck = []
    for job_id, info in jobs.items():
        if not info.get("final"):
            code, st = api.call("GET", "/v1/solves/" + job_id)
            stuck.append({"id": job_id, "kind": info["kind"], "status": st.get("status")})
    final = [info["final"] for info in jobs.values() if info.get("final")]
    by = {}
    for st in final:
        key = "%s/%s" % (st["status"], st["error_code"] or "-")
        by[key] = by.get(key, 0) + 1
    unexpected = [
        {"id": st["id"], "kind": info["kind"], "status": st["status"], "error_code": st["error_code"]}
        for info in jobs.values() for st in [info.get("final")] if st
        and not ((info["kind"] == "cancel" and st["status"] == "cancelled")
                 or (info["kind"] == "timeout" and st["error_code"] == "timeout")
                 or (info["kind"] not in ("cancel", "timeout") and st["status"] == "done"))
    ]
    code, metrics = api.call("GET", "/metrics")
    emit(event="summary", rounds=n, jobs=len(jobs), outcomes=by, stuck=stuck, unexpected=unexpected,
         duration_seconds=round(time.time() - start, 1), metrics_ok=code == 200,
         metrics=[l for l in str(metrics).splitlines() if l.startswith(("holdem_jobs", "holdem_worker_restarts",
                                                                          "holdem_vram_factor", "holdem_queue"))])
    return 1 if stuck or unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
