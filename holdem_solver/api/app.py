"""FastAPI app for the solver REST API.

Configuration is by environment variable:

* ``HOLDEM_API_KEY``: bearer key required on every endpoint except
  ``/healthz`` (``Authorization: Bearer`` or ``X-API-Key``). Unset = no auth.
* ``HOLDEM_GPUS``: comma-separated GPU ids (default ``0,1``).
* ``HOLDEM_SOLVER_ENGINE``: ``real`` (default) or ``fake``.
* ``HOLDEM_API_DATA_DIR``: job DB and strategy files (default ``./api-data``).
* ``HOLDEM_API_MAX_VRAM_BYTES``: VRAM budget for the 1.15x gate
  (default 14 GiB).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from holdem_solver.client import RemoteEngineError
from holdem_solver.protocol import SpotConfig

from . import gpus as gpu_stats
from .models import GpuInfo, HealthResponse, SolveAccepted, SolveRequest, SolveStatus, StrategyResponse
from .scheduler import Scheduler
from .store import JobStore
from .strategy import map_strategy

LOG = logging.getLogger("holdem_solver.api")

DEFAULT_MAX_VRAM_BYTES = 14 * 1024**3
VRAM_SAFETY_FACTOR = 1.15

ACTIVE_STATES = ("queued", "building", "solving")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("HOLDEM_API_LOG_LEVEL", "INFO").upper())


def _parse_gpus(text: str) -> list[int | None]:
    text = text.strip()
    if not text:
        return [None]
    return [int(part) for part in text.split(",") if part.strip()]


def _auth_dependency(api_key: str | None):
    async def require(request: Request) -> None:
        if api_key is None:
            return
        header = request.headers.get("authorization", "")
        token = header.removeprefix("Bearer ") if header.startswith("Bearer ") else None
        token = token or request.headers.get("x-api-key")
        if token != api_key:
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    return require


def _status(job: dict[str, Any]) -> SolveStatus:
    params = job["params"]
    return SolveStatus(
        id=job["id"],
        status=job["status"],
        gpu=job["gpu"],
        created_at=job["created_at"],
        started_at=job["started_at"],
        finished_at=job["finished_at"],
        elapsed_seconds=job["elapsed_seconds"],
        build_seconds=job["build_seconds"],
        iteration=job["iteration"],
        iterations_run=job["iterations_run"],
        exploitability=job["exploitability"],
        exploitability_history=job["exploitability_history"],
        num_nodes=job["num_nodes"],
        num_infosets=job["num_infosets"],
        estimated_vram_bytes=job["estimated_vram_bytes"],
        measured_vram=job["measured_vram"],
        target_exploitability=params.get("target_exploitability"),
        target_reached=job["target_reached"],
        profile_value_p0=job["profile_value_p0"],
        error=job["error"],
    )


def _engine_error_detail(exc: RemoteEngineError) -> str:
    return str(exc)


def create_app(
    *,
    gpus: list[int | None] | None = None,
    engine: str | None = None,
    api_key: str | None = None,
    data_dir: str | None = None,
    max_vram_bytes: int | None = None,
    client_kwargs: dict[int | None, dict[str, Any]] | None = None,
) -> FastAPI:
    """Build the app; keyword args override the environment (tests use them)."""
    configure_logging()
    if gpus is None:
        gpus = _parse_gpus(os.environ.get("HOLDEM_GPUS", "0,1"))
    if engine is None:
        engine = os.environ.get("HOLDEM_SOLVER_ENGINE", "real")
    if api_key is None:
        api_key = os.environ.get("HOLDEM_API_KEY") or None
    if data_dir is None:
        data_dir = os.environ.get("HOLDEM_API_DATA_DIR", "./api-data")
    if max_vram_bytes is None:
        max_vram_bytes = int(os.environ.get("HOLDEM_API_MAX_VRAM_BYTES", DEFAULT_MAX_VRAM_BYTES))

    os.makedirs(data_dir, exist_ok=True)
    store = JobStore(os.path.join(data_dir, "solves.db"))
    scheduler = Scheduler(gpus, store, data_dir, engine=engine, client_kwargs=client_kwargs)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        failed = store.fail_in_flight("api restarted before the job finished")
        if failed:
            LOG.warning("marked %d in-flight jobs failed on startup", failed)
        scheduler.start()
        yield
        scheduler.stop()
        store.close()

    app = FastAPI(title="holdem-solver", version="0.3.0", lifespan=lifespan)
    app.state.store = store
    app.state.scheduler = scheduler
    auth = _auth_dependency(api_key)

    @app.exception_handler(RequestValidationError)
    async def validation_422(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/v1/gpus", response_model=list[GpuInfo], dependencies=[Depends(auth)])
    def list_gpus() -> list[GpuInfo]:
        infos = []
        for gpu in scheduler.gpus:
            info = scheduler.gpu_status(gpu)
            info["pynvml"] = gpu_stats.gpu_stats(gpu) if gpu is not None else None
            infos.append(GpuInfo(**info))
        return infos

    @app.post("/v1/solves", status_code=202, response_model=SolveAccepted, dependencies=[Depends(auth)])
    def post_solve(request: SolveRequest) -> SolveAccepted:
        spot = request.spot_dict()
        try:
            config = SpotConfig.from_dict(spot)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"bad spot: {exc}") from exc

        dtype = (request.solver_options or {}).get("dtype", "float32")
        try:
            estimate = scheduler.estimate(spot, dtype=dtype)
        except RemoteEngineError as exc:
            raise HTTPException(status_code=422, detail=_engine_error_detail(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"engine unavailable: {exc}") from exc

        num_nodes = int(estimate["num_nodes"])
        if config.max_nodes is not None and num_nodes > config.max_nodes:
            raise HTTPException(
                status_code=422,
                detail=f"spot needs {num_nodes} nodes, over max_nodes={config.max_nodes}; "
                "narrow the ranges or bet sizes, or raise max_nodes",
            )
        est_vram = int(estimate["estimated_vram_bytes"])
        if est_vram * VRAM_SAFETY_FACTOR > max_vram_bytes:
            gib = 1024**3
            raise HTTPException(
                status_code=422,
                detail=f"estimated VRAM {est_vram / gib:.1f} GiB x{VRAM_SAFETY_FACTOR} "
                f"exceeds the {max_vram_bytes / gib:.0f} GiB budget; narrow the spot",
            )

        params = request.solve_params()
        params["variant"] = request.variant
        if request.solver_options:
            params["solver_options"] = request.solver_options
        if request.max_iterations is not None:
            params["max_iterations"] = request.max_iterations
        job_id = store.create(spot, params)
        scheduler.wake()
        LOG.info("job %s queued: %s nodes, est %.1f GiB", job_id, num_nodes, est_vram / 1024**3)
        return SolveAccepted(id=job_id)

    @app.get("/v1/solves/{job_id}", response_model=SolveStatus, dependencies=[Depends(auth)])
    def get_solve(job_id: str) -> SolveStatus:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job id")
        return _status(job)

    @app.get("/v1/solves/{job_id}/strategy", response_model=StrategyResponse, dependencies=[Depends(auth)])
    def get_strategy(job_id: str) -> StrategyResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job id")
        if job["status"] in ACTIVE_STATES:
            raise HTTPException(status_code=409, detail=f"job is still {job['status']}")
        path = job["strategy_path"]
        if not path or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="no strategy was exported for this job")
        data = np.load(path, allow_pickle=False)
        keys = [key.decode("utf-8") for key in data["keys"].tolist()]
        actions = [
            [name for name in row.decode("utf-8").split(",") if name] for row in data["actions"].tolist()
        ]
        mapped = map_strategy(keys, actions, data["offsets"], data["values"], int(data["iteration"]))
        return StrategyResponse(id=job_id, **mapped)

    @app.delete("/v1/solves/{job_id}", response_model=SolveStatus, dependencies=[Depends(auth)])
    def delete_solve(job_id: str) -> SolveStatus:
        job = store.request_cancel(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job id")
        if job["status"] == "queued":
            store.update(job_id, status="cancelled", finished_at=time.time())
            job = store.get(job_id)
        scheduler.wake()
        return _status(job)

    return app


def main() -> None:
    import uvicorn

    port = int(os.environ.get("HOLDEM_API_PORT", "8000"))
    host = os.environ.get("HOLDEM_API_HOST", "127.0.0.1")
    uvicorn.run("holdem_solver.api.app:create_app", factory=True, host=host, port=port)


if __name__ == "__main__":
    main()
