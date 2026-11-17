"""OpenAPI description and one response example per endpoint."""

from __future__ import annotations

from .models import SOLVE_REQUEST_EXAMPLE  # noqa: F401  (request example lives on the model)

DESCRIPTION = """
River (and turn) heads-up NLHE solves on the GPU-CFR engine.

* Limits: no raises (bet/check/call/fold only), no flop or preflop trees,
  one bet-size list shared by every node.
* `dtype` (`float32` default, or `float64`) sets solver precision; float64
  doubles VRAM for the same tree.
* `GET /v1/solves/{id}/strategy` returns solver output only (average strategy
  frequencies of the submitted spot); it does not expose other jobs, files or
  server state.
* Auth: `Authorization: Bearer <key>` or `X-API-Key` on every endpoint except
  `/healthz` when `HOLDEM_API_KEY` is set. `POST /v1/auth/login` exchanges
  the key for an HttpOnly session cookie plus a CSRF token (send it back as
  `X-CSRF-Token` on mutating requests). CORS is disabled unless
  `HOLDEM_CORS_ORIGINS` is set.
* `GET /v1/solves/{id}/tree` and `/node` navigate the solved tree and return
  reach-weighted frequencies, per-action EVs, hand EV, equity, and ranges;
  `/events` streams live progress over server-sent events; `/v1/spots`
  stores named reusable spots.
"""


def _json(example, status=200, description="Successful Response"):
    return {status: {"description": description, "content": {"application/json": {"example": example}}}}


HEALTHZ = _json({"status": "ok"})

METRICS = {
    200: {
        "description": "Prometheus text exposition format",
        "content": {
            "text/plain": {
                "example": (
                    '# HELP holdem_queue_depth Queued jobs waiting for a GPU.\n'
                    '# TYPE holdem_queue_depth gauge\nholdem_queue_depth 2.0\n'
                    'holdem_jobs{status="done"} 41.0\n'
                    'holdem_jobs_failed{error_code="cuda_oom"} 1.0\n'
                    'holdem_vram_factor{dtype="float32",source="seed"} 1.6\n'
                    'holdem_iterations_per_second{gpu="0"} 118.4\n'
                    'holdem_gpu_utilization_ratio{gpu="0"} 0.97\n'
                    'holdem_worker_restarts_total{gpu="1"} 1.0\n'
                    'holdem_build_duration_seconds_bucket{dtype="float32",gpu="0",le="1.0"} 12.0\n'
                )
            }
        },
    }
}

GPUS = _json(
    [
        {
            "gpu": 0, "state": "unbuilt", "device": "NVIDIA GeForce RTX 5060 Ti", "worker_pid": 41230,
            "worker_vram": {"allocated_bytes": 0, "reserved_bytes": 0},
            "pynvml": {"memory_used_bytes": 512753664, "memory_total_bytes": 17103323136,
                       "utilization_gpu_pct": 0, "utilization_memory_pct": 0},
            "job_id": None, "healthy": True, "restarts": 0, "vram_capacity_bytes": 17103323136,
            "heartbeat_age_seconds": 1.2, "heartbeat_latency_seconds": 0.0011,
        }
    ]
)

POST_SOLVE = _json(
    {"id": "3f9c2a7b1d04", "status": "queued", "replayed": False, "queue_depth": 1,
     "predicted_vram_bytes": 156237414},
    status=202, description="Accepted and queued",
)

_STATUS = {
    "id": "3f9c2a7b1d04", "status": "solving", "gpu": 1, "dtype": "float32",
    "created_at": 1789000000.1, "started_at": 1789000000.4, "finished_at": None,
    "elapsed_seconds": 12.6, "build_seconds": 2.9, "iteration": 2400, "iterations_run": 2400,
    "exploitability": 0.00231, "exploitability_iteration": 2400,
    "exploitability_history": [{"iteration": 2300, "exploitability": 0.00247, "elapsed_seconds": 9.4},
                               {"iteration": 2400, "exploitability": 0.00231, "elapsed_seconds": 9.7}],
    "num_nodes": 153422, "num_infosets": 3608, "estimated_vram_bytes": 65719296,
    "measured_vram": {"peak_reserved_bytes": 102760448}, "target_exploitability": 0.002,
    "target_reached": None, "profile_value_p0": None, "error": None, "error_code": None, "priority": 0,
    "timeout_seconds": 600.0, "predicted_vram_bytes": 105150873, "peak_vram_bytes": None,
    "strategy_available": False, "strategy_expired_at": None, "pinned": False,
    "spot_name": "dry-river-polar",
}

GET_SOLVE = _json(_STATUS)

LIST_SOLVES = _json([_STATUS])

ESTIMATE = _json(
    {
        "num_nodes": 153422, "estimated_vram_bytes": 65719296,
        "predicted_vram_bytes": 105150873, "vram_limit_bytes": 15032385536,
        "over_vram_budget": False, "street": "river", "num_hands": [40, 62],
        "num_removed": [2, 0], "num_deals": 2401, "weighted": False,
        "bet_fractions": [0.5, 1.0],
    }
)

DELETE_SOLVE = _json(dict(_STATUS, status="cancelled", finished_at=1789000013.2))

GET_STRATEGY = _json(
    {
        "view=infosets": {
            "id": "3f9c2a7b1d04", "view": "infosets", "iteration": 4000, "num_infosets": 72, "unmapped_keys": 0,
            "infosets": [
                {"key": "p0|AhAd|", "player": 0, "hand": "AhAd", "history": [],
                 "actions": [{"name": "check", "label": "check", "frequency": 0.38},
                             {"name": "b20", "label": "bet 20", "frequency": 0.62}]}
            ],
        },
        "view=hand_grid": {
            "id": "3f9c2a7b1d04", "view": "hand_grid", "iteration": 4000, "num_infosets": 72,
            "nodes": [
                {"player": 0, "history": [], "combos": 22,
                 "actions": [{"name": "check", "label": "check"}, {"name": "b20", "label": "bet 20"}],
                 "grid": [{"hand_class": "AA", "combos": 6, "frequencies": {"check": 0.41, "b20": 0.59}},
                          {"hand_class": "AKs", "combos": 4, "frequencies": {"check": 0.12, "b20": 0.88}},
                          {"hand_class": "AQs", "combos": 0, "frequencies": {}}]}
            ],
        },
    }
)

LOGIN = _json({"expires_at": 1789000000.0 + 43200, "csrf": "lV9eHk3Zx2QwT0yR8nM6pA1sD4fG7hJk"})

LOGOUT = _json({"status": "logged_out"})

PIN = _json({"id": "3f9c2a7b1d04", "pinned": True})

GET_TREE = _json(
    {
        "id": "3f9c2a7b1d04", "iteration": 4000, "history": [], "street": "river",
        "pot": 20.0, "stacks": {"p0": 100.0, "p1": 100.0},
        "board": ["Ks", "Js", "Th", "7d", "2c"], "is_chance": False, "player": 0,
        "actions": [{"name": "check", "label": "check"}, {"name": "b10", "label": "bet 10"}],
        "dealt_cards": None,
        "children": [
            {"edge": "check", "kind": "player", "player": 1, "history": ["check"],
             "actions": [{"name": "check", "label": "check"}, {"name": "b10", "label": "bet 10"}]},
            {"edge": "b10", "kind": "player", "player": 1, "history": ["b10"],
             "actions": [{"name": "fold", "label": "fold"}, {"name": "call", "label": "call"}]},
        ],
    }
)

GET_NODE = _json(
    {
        "id": "3f9c2a7b1d04", "iteration": 4000, "history": [], "street": "river",
        "player": 0, "pot": 20.0, "stacks": {"p0": 100.0, "p1": 100.0},
        "board": ["Ks", "Js", "Th", "7d", "2c"],
        "actions": [{"name": "check", "label": "check"}, {"name": "b10", "label": "bet 10"}],
        "ranges": {
            "p0": [{"hand_class": "AA", "reach": 0.55, "share": 0.31, "combos": 6}],
            "p1": [{"hand_class": "TT", "reach": 0.5, "share": 0.4, "combos": 6}],
        },
        "hands": [
            {"hand_class": "AA", "combos": 6, "reach": 0.55, "weight": 0.9,
             "frequencies": {"check": 0.41, "b10": 0.59},
             "action_evs": {"check": 5.1, "b10": 6.7}, "ev": 6.04, "equity": 0.72},
        ],
    }
)

GET_EVENTS = {
    200: {
        "description": "text/event-stream of progress events (snapshot, progress, done)",
        "content": {
            "text/event-stream": {
                "example": (
                    'id: 1\nevent: snapshot\n'
                    'data: {"status": "solving", "iteration": 2400, "iterations_run": 2400, '
                    '"exploitability": 0.00231, "exploitability_iteration": 2400}\n\n'
                    'id: 2\nevent: progress\n'
                    'data: {"status": "solving", "iteration": 2500, "iterations_run": 2500, '
                    '"exploitability": 0.00231, "exploitability_iteration": 2400}\n\n'
                    'id: 3\nevent: done\n'
                    'data: {"status": "done", "iteration": 4000, "iterations_run": 4000, '
                    '"exploitability": 0.00198, "exploitability_iteration": 4000}\n\n'
                )
            }
        },
    }
}

SPOT = _json(
    {
        "id": "7a1c4e9b2d10", "name": "dry-river-polar",
        "spot": {"board": "KsJsTh7d2c", "range0": "QQ+, AKs", "range1": "TT, AQ",
                 "pot": 20.0, "stack": 100.0, "bet_sizes": "standard"},
        "tags": ["river", "dry"], "notes": "baseline test spot",
        "created_at": 1789000000.0, "updated_at": 1789000000.0,
    },
    status=201, description="Created",
)

SPOT_LIST = _json(
    [
        {
            "id": "7a1c4e9b2d10", "name": "dry-river-polar",
            "spot": {"board": "KsJsTh7d2c", "range0": "QQ+, AKs",
                     "range1": "TT, AQ", "pot": 20.0, "stack": 100.0,
                     "bet_sizes": "standard"},
            "tags": ["river", "dry"], "notes": "baseline test spot",
            "created_at": 1789000000.0, "updated_at": 1789000000.0,
        },
    ],
)

DELETE_SPOT = _json({"status": "deleted"})
