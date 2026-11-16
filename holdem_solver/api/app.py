"""FastAPI app for the solver REST API.

Configuration is by environment variable:

* ``HOLDEM_API_KEY``: bearer key required on every endpoint except
  ``/healthz`` (``Authorization: Bearer`` or ``X-API-Key``). Unset = no auth.
* ``HOLDEM_GPUS``: comma-separated GPU ids (default ``0,1``).
* ``HOLDEM_SOLVER_ENGINE``: ``real`` (default) or ``fake``.
* ``HOLDEM_API_DATA_DIR``: job DB and strategy files (default ``./api-data``).
* ``HOLDEM_API_MAX_VRAM_BYTES``: VRAM budget for the predicted peak
  (default 14 GiB); also capped by each GPU's capacity minus the reserve.
* ``HOLDEM_MAX_QUEUE_DEPTH``: queued jobs before POST returns 429 (default 64).
* ``HOLDEM_JOB_TIMEOUT_SECONDS``: default per-job timeout (default none).
* ``HOLDEM_HEARTBEAT_SECONDS`` / ``HOLDEM_HUNG_WORKER_SECONDS``: watchdog ping
  interval and the no-answer time before a worker is killed (5 / 120).
* ``HOLDEM_CANCEL_GRACE_SECONDS``: after a timeout cancel, seconds before the
  worker is killed (30).
* ``HOLDEM_STRATEGY_RETENTION_SECONDS``: strategy .npz lifetime after the job
  finishes (default 7 days; ``0`` or empty disables cleanup).
* ``HOLDEM_VRAM_RESERVE_BYTES``: per-GPU headroom left free (512 MiB).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from holdem_solver.client import RemoteEngineError
from holdem_solver.protocol import SpotConfig

from . import gpus as gpu_stats
from .models import GpuInfo, HealthResponse, SolveAccepted, SolveRequest, SolveStatus, StrategyResponse
from .scheduler import Scheduler, SchedulerConfig
from .store import IdempotencyConflictError, JobStore, QueueFullError, request_hash
from .strategy import map_strategy

LOG = logging.getLogger("holdem_solver.api")

DEFAULT_MAX_VRAM_BYTES = 14 * 1024**3
DEFAULT_MAX_QUEUE_DEPTH = 64

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
        error_code=job.get("error_code"),
        priority=job.get("priority") or 0,
        timeout_seconds=job.get("timeout_seconds"),
        predicted_vram_bytes=job.get("predicted_vram_bytes"),
        peak_vram_bytes=job.get("peak_vram_bytes"),
        strategy_available=bool(job["strategy_path"]) and os.path.exists(job["strategy_path"]),
        strategy_expired_at=job.get("strategy_expired_at"),
    )


def _engine_error_detail(exc: RemoteEngineError) -> str:
    return str(exc)


def _queue_full(exc: QueueFullError) -> HTTPException:
    return HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "30"})


def _env_float(name: str, default: float | None) -> float | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    if not value or float(value) <= 0:
        return None
    return float(value)


def _config_from_env() -> SchedulerConfig:
    base = SchedulerConfig()
    return SchedulerConfig(
        heartbeat_seconds=_env_float("HOLDEM_HEARTBEAT_SECONDS", base.heartbeat_seconds),
        hung_worker_seconds=_env_float("HOLDEM_HUNG_WORKER_SECONDS", base.hung_worker_seconds),
        default_timeout_seconds=_env_float("HOLDEM_JOB_TIMEOUT_SECONDS", None),
        cancel_grace_seconds=_env_float("HOLDEM_CANCEL_GRACE_SECONDS", base.cancel_grace_seconds),
        retention_seconds=_env_float("HOLDEM_STRATEGY_RETENTION_SECONDS", base.retention_seconds),
        vram_reserve_bytes=int(os.environ.get("HOLDEM_VRAM_RESERVE_BYTES", base.vram_reserve_bytes)),
    )


def create_app(
    *,
    gpus: list[int | None] | None = None,
    engine: str | None = None,
    api_key: str | None = None,
    data_dir: str | None = None,
    max_vram_bytes: int | None = None,
    client_kwargs: dict[int | None, dict[str, Any]] | None = None,
    max_queue_depth: int | None = None,
    scheduler_config: SchedulerConfig | None = None,
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

    if max_queue_depth is None:
        max_queue_depth = int(os.environ.get("HOLDEM_MAX_QUEUE_DEPTH", DEFAULT_MAX_QUEUE_DEPTH))
    if scheduler_config is None:
        scheduler_config = _config_from_env()

    os.makedirs(data_dir, exist_ok=True)
    store = JobStore(os.path.join(data_dir, "solves.db"))
    scheduler = Scheduler(
        gpus, store, data_dir, engine=engine, client_kwargs=client_kwargs, config=scheduler_config
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        failed = store.recover_after_restart()
        if failed:
            LOG.warning("API restart: failed %d interrupted jobs (not replayed): %s", len(failed), failed)
        queued = store.queue_depth()
        if queued:
            LOG.info("API restart: %d queued jobs kept in the persistent queue", queued)
        scheduler.start()
        yield
        scheduler.stop()
        store.close()

    app = FastAPI(title="holdem-solver", version="0.4.0", lifespan=lifespan)
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
    def post_solve(
        request: SolveRequest,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
    ) -> SolveAccepted:
        body_hash = request_hash(request.model_dump(mode="json"))
        if idempotency_key is not None:
            # Retries of an accepted request are answered before any engine
            # work or queue-depth check: the job already exists.
            try:
                existing = store.find_idempotent(idempotency_key, body_hash)
            except IdempotencyConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if existing is not None:
                response.headers["Idempotent-Replayed"] = "true"
                return SolveAccepted(
                    id=existing["id"], status=existing["status"], replayed=True,
                    predicted_vram_bytes=existing.get("predicted_vram_bytes"),
                )
        depth = store.queue_depth()
        if depth >= max_queue_depth:
            raise _queue_full(QueueFullError(depth, max_queue_depth))

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
        factor, source, samples = scheduler.vram.factor(dtype)
        predicted = int(est_vram * factor)
        limit = max_vram_bytes
        gpu_limit = scheduler.max_usable_vram()
        if gpu_limit is not None:
            limit = min(limit, gpu_limit)
        if predicted > limit:
            gib = 1024**3
            basis = (
                f"x{factor:.3f} from {samples} measured peak(s)" if source == "measured" else f"x{factor:.2f} static"
            )
            raise HTTPException(
                status_code=422,
                detail=f"predicted peak VRAM {predicted / gib:.2f} GiB (estimate {est_vram / gib:.2f} GiB "
                f"{basis}) exceeds the {limit / gib:.2f} GiB per-GPU budget; narrow the spot",
            )

        params = request.solve_params()
        params["variant"] = request.variant
        if request.solver_options:
            params["solver_options"] = request.solver_options
        if request.max_iterations is not None:
            params["max_iterations"] = request.max_iterations
        try:
            job, replayed = store.create(
                spot,
                params,
                priority=request.priority,
                timeout_seconds=request.timeout_seconds,
                idempotency_key=idempotency_key,
                body_hash=body_hash,
                max_queue_depth=max_queue_depth,
                num_nodes=num_nodes,
                estimated_vram_bytes=est_vram,
                predicted_vram_bytes=predicted,
                dtype=dtype,
            )
        except QueueFullError as exc:
            raise _queue_full(exc) from exc
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if replayed:
            response.headers["Idempotent-Replayed"] = "true"
        scheduler.wake()
        LOG.info(
            "job %s queued: %s nodes, priority %s, predicted %.2f GiB",
            job["id"], num_nodes, request.priority, predicted / 1024**3,
        )
        return SolveAccepted(
            id=job["id"], status=job["status"], replayed=replayed, queue_depth=depth + (0 if replayed else 1),
            predicted_vram_bytes=predicted,
        )

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
        if job.get("strategy_expired_at"):
            raise HTTPException(status_code=410, detail="the strategy was deleted by the retention policy")
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
        job = store.cancel(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job id")
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
