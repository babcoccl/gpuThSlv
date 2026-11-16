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
  `/healthz` when `HOLDEM_API_KEY` is set.
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
    "strategy_available": False, "strategy_expired_at": None,
}

GET_SOLVE = _json(_STATUS)

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
