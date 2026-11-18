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
* ``HOLDEM_WORKER_MODE``: ``spawn`` (default; the API starts its workers)
  or ``external`` (attach to per-GPU workers run by systemd/compose).
* ``HOLDEM_MAX_BODY_BYTES``: request bodies over this get 413 (64 KiB).
* ``HOLDEM_POST_RATE_PER_MINUTE`` / ``HOLDEM_POST_BURST``: token bucket per
  client for ``POST /v1/solves`` (120/min, burst 30); 429 + Retry-After.
  ``0`` disables it.
* ``HOLDEM_SESSION_TTL_SECONDS``: browser session cookie lifetime (12 h).
  ``HOLDEM_SESSION_SECONDS`` is the deprecated fallback. Sessions are
  stateless HMAC tokens: logout clears the cookie client-side but cannot
  revoke a stolen token before expiry — rotate the signing material or
  shorten the TTL to bound the risk.
* ``HOLDEM_SESSION_SECRET``: signing secret for session cookies (default
  derived from ``HOLDEM_API_KEY``; set it to keep sessions valid across a
  key rotation).
* ``HOLDEM_LOGIN_RATE_PER_MINUTE`` / ``HOLDEM_LOGIN_BURST``: login attempts
  per client (10/min, burst 5); ``0`` disables.
* ``HOLDEM_CORS_ORIGINS``: comma-separated allowed origins; unset or empty
  disables CORS entirely (default).
* ``HOLDEM_MAX_SPOTS``: saved-spot limit for ``/v1/spots`` (512).
* ``HOLDEM_NODE_EVAL_MAX_WORK``: max combo-node visits a ``/node`` request
  may schedule in the pure-Python EV walk (5,000,000).
* ``HOLDEM_SSE_BUFFER``: per-job SSE replay buffer size (256 events).
* ``HOLDEM_SSE_POLL_SECONDS``: job-state poll interval inside an SSE stream
  (0.5 s); ``HOLDEM_SSE_HEARTBEAT_SECONDS``: keep-alive comment interval
  (15 s); ``HOLDEM_SSE_RETRY_MS``: the ``retry:`` field sent to clients
  (2000 ms).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Iterator, Literal

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from holdem_solver.client import RemoteEngineError
from holdem_solver.protocol import SpotConfig
from holdem_solver.protocol.evwalk import parse_history

from . import gpus as gpu_stats
from . import examples
from . import nodes as api_nodes
from . import security
from .metrics import CONTENT_TYPE as METRICS_CONTENT_TYPE
from .metrics import Metrics
from .models import (
    EstimateRequest,
    EstimateResponse,
    GpuInfo,
    HandGridResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    NodeValuesResponse,
    PinResponse,
    SolveAccepted,
    SolveParams,
    SolveRequest,
    SolveStatus,
    SpotCreate,
    SpotRecord,
    SpotUpdate,
    StatusResponse,
    StrategyResponse,
    TreeNodeResponse,
)
from .scheduler import Scheduler, SchedulerConfig
from .store import IdempotencyConflictError, JobStore, QueueFullError, request_hash
from .strategy import hand_grid, map_strategy, spot_blocked, spot_ranges

LOG = logging.getLogger("holdem_solver.api")

DEFAULT_MAX_VRAM_BYTES = 14 * 1024**3
DEFAULT_MAX_QUEUE_DEPTH = 64
DEFAULT_MAX_SPOTS = 512
SSE_BUFFER = 256
SSE_POLL_SECONDS = 0.5
SSE_HEARTBEAT_SECONDS = 15.0
SSE_RETRY_MS = 2000

ACTIVE_STATES = ("queued", "building", "solving")
TERMINAL_STATES = ("done", "cancelled", "failed")


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
        exploitability_iteration=job.get("exploitability_iteration"),
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
        dtype=job.get("dtype") or "float32",
        priority=job.get("priority") or 0,
        timeout_seconds=job.get("timeout_seconds"),
        predicted_vram_bytes=job.get("predicted_vram_bytes"),
        peak_vram_bytes=job.get("peak_vram_bytes"),
        strategy_available=bool(job["strategy_path"]) and os.path.exists(job["strategy_path"]),
        strategy_expired_at=job.get("strategy_expired_at"),
        pinned=bool(job.get("pinned")),
        spot_name=job["spot"].get("name"),
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
        worker_mode=os.environ.get("HOLDEM_WORKER_MODE", base.worker_mode).strip() or base.worker_mode,
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
    max_body_bytes: int | None = None,
    post_rate_per_minute: float | None = None,
    post_burst: int | None = None,
    session_seconds: int | None = None,
    session_secret: str | None = None,
    login_rate_per_minute: float | None = None,
    login_burst: int | None = None,
    cors_origins: list[str] | None = None,
    max_spots: int | None = None,
    node_eval_max_work: int | None = None,
    sse_buffer_size: int | None = None,
    sse_poll_seconds: float | None = None,
    sse_heartbeat_seconds: float | None = None,
    sse_retry_ms: int | None = None,
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
    if max_body_bytes is None:
        max_body_bytes = int(os.environ.get("HOLDEM_MAX_BODY_BYTES", security.DEFAULT_MAX_BODY_BYTES))
    if post_rate_per_minute is None:
        post_rate_per_minute = float(
            os.environ.get("HOLDEM_POST_RATE_PER_MINUTE", security.DEFAULT_POST_RATE_PER_MINUTE)
        )
    if post_burst is None:
        post_burst = int(os.environ.get("HOLDEM_POST_BURST", security.DEFAULT_POST_BURST))
    limiter = security.RateLimiter(post_rate_per_minute, post_burst) if post_rate_per_minute > 0 else None
    if session_seconds is None:
        session_seconds = int(
            os.environ.get(
                "HOLDEM_SESSION_TTL_SECONDS",
                os.environ.get(
                    "HOLDEM_SESSION_SECONDS", security.DEFAULT_SESSION_SECONDS
                ),
            )
        )
    if session_secret is None:
        session_secret = os.environ.get("HOLDEM_SESSION_SECRET") or None
    if login_rate_per_minute is None:
        login_rate_per_minute = float(
            os.environ.get("HOLDEM_LOGIN_RATE_PER_MINUTE", security.DEFAULT_LOGIN_RATE_PER_MINUTE)
        )
    if login_burst is None:
        login_burst = int(os.environ.get("HOLDEM_LOGIN_BURST", security.DEFAULT_LOGIN_BURST))
    if cors_origins is None:
        cors_origins = [
            part.strip() for part in os.environ.get("HOLDEM_CORS_ORIGINS", "").split(",") if part.strip()
        ]
    if max_spots is None:
        max_spots = int(os.environ.get("HOLDEM_MAX_SPOTS", DEFAULT_MAX_SPOTS))
    if node_eval_max_work is None:
        node_eval_max_work = int(
            os.environ.get("HOLDEM_NODE_EVAL_MAX_WORK", api_nodes.DEFAULT_MAX_EVAL_WORK)
        )
    index_cache_jobs = int(os.environ.get("HOLDEM_INDEX_CACHE_JOBS", "4"))
    values_cache_entries = int(os.environ.get("HOLDEM_NODE_VALUES_CACHE", "256"))
    if sse_buffer_size is None:
        sse_buffer_size = int(os.environ.get("HOLDEM_SSE_BUFFER", SSE_BUFFER))
    if sse_poll_seconds is None:
        sse_poll_seconds = float(os.environ.get("HOLDEM_SSE_POLL_SECONDS", SSE_POLL_SECONDS))
    if sse_heartbeat_seconds is None:
        sse_heartbeat_seconds = float(
            os.environ.get("HOLDEM_SSE_HEARTBEAT_SECONDS", SSE_HEARTBEAT_SECONDS)
        )
    if sse_retry_ms is None:
        sse_retry_ms = int(os.environ.get("HOLDEM_SSE_RETRY_MS", SSE_RETRY_MS))
    signer = (
        security.SessionSigner(api_key, secret=session_secret, ttl_seconds=session_seconds)
        if api_key
        else None
    )
    login_limiter = (
        security.RateLimiter(login_rate_per_minute, login_burst) if login_rate_per_minute > 0 else None
    )
    if api_key is None:
        LOG.warning("HOLDEM_API_KEY is unset: authentication is disabled (development only)")

    os.makedirs(data_dir, exist_ok=True)
    store = JobStore(os.path.join(data_dir, "solves.db"))
    metrics = Metrics()
    scheduler = Scheduler(
        gpus, store, data_dir, engine=engine, client_kwargs=client_kwargs, config=scheduler_config,
        metrics=metrics,
    )
    metrics.attach(store, scheduler)

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

    app = FastAPI(title="holdem-solver", version="0.5.0", lifespan=lifespan, description=examples.DESCRIPTION)
    app.add_middleware(security.BodySizeLimit, max_bytes=max_body_bytes)
    if cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.state.store = store
    app.state.scheduler = scheduler
    app.state.metrics = metrics
    app.state.sse_buffers = {}  # job_id -> deque[(seq, payload)]
    app.state.sse_lock = threading.Lock()
    # Per-process token in event ids (``<boot>-<seq>``): a Last-Event-ID from
    # before an API restart can never alias this buffer's seq numbers.
    import secrets as _secrets

    app.state.sse_boot = _secrets.token_hex(4)
    auth = security.auth_dependency(api_key, signer)
    csrf = security.csrf_dependency
    rate_limit = security.rate_limit_dependency(limiter)

    @app.exception_handler(RequestValidationError)
    async def validation_422(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})

    @app.get("/healthz", response_model=HealthResponse, responses=examples.HEALTHZ)
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get(
        "/metrics", dependencies=[Depends(auth)], response_class=Response, responses=examples.METRICS,
        summary="Prometheus metrics (same auth as /v1)",
    )
    def get_metrics() -> Response:
        return Response(metrics.render(), media_type=METRICS_CONTENT_TYPE)

    @app.get("/v1/gpus", response_model=list[GpuInfo], dependencies=[Depends(auth)], responses=examples.GPUS)
    def list_gpus() -> list[GpuInfo]:
        infos = []
        for gpu in scheduler.gpus:
            info = scheduler.gpu_status(gpu)
            info["pynvml"] = gpu_stats.gpu_stats(gpu) if gpu is not None else None
            infos.append(GpuInfo(**info))
        return infos

    def _vram_limit() -> int:
        limit = max_vram_bytes
        gpu_limit = scheduler.max_usable_vram()
        if gpu_limit is not None:
            limit = min(limit, gpu_limit)
        return limit

    def _predicted(spot: dict[str, Any], dtype: str) -> tuple[int, int, int]:
        """``(num_nodes, estimated_vram, predicted_peak_vram)`` for a spot.

        Engine errors become 422s (a spot the engine rejects) or 503s (no
        worker), exactly as in ``_admit``."""
        try:
            estimate = scheduler.estimate(spot, dtype=dtype)
        except RemoteEngineError as exc:
            raise HTTPException(status_code=422, detail=_engine_error_detail(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"engine unavailable: {exc}") from exc
        factor, _source, _samples = scheduler.vram.factor(dtype)
        return (
            int(estimate["num_nodes"]),
            int(estimate["estimated_vram_bytes"]),
            int(estimate["estimated_vram_bytes"] * factor),
        )

    def _admit(
        spot: dict[str, Any],
        request: SolveParams,
        response: Response,
        idempotency_key: str | None,
        body_hash: str,
    ) -> SolveAccepted:
        """Shared admission for ``POST /v1/solves`` and ``POST /v1/spots/{id}/solve``."""
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

        try:
            config = SpotConfig.from_dict(spot)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"bad spot: {exc}") from exc

        dtype = request.effective_dtype()
        num_nodes, est_vram, predicted = _predicted(spot, dtype)
        if config.max_nodes is not None and num_nodes > config.max_nodes:
            raise HTTPException(
                status_code=422,
                detail=f"spot needs {num_nodes} nodes, over max_nodes={config.max_nodes}; "
                "narrow the ranges or bet sizes, or raise max_nodes",
            )
        limit = _vram_limit()
        if predicted > limit:
            gib = 1024**3
            factor, source, samples = scheduler.vram.factor(dtype)
            basis = (
                f"x{factor:.3f} from {samples} measured peak(s)" if source == "measured" else f"x{factor:.2f} seed ({samples} of {scheduler.vram.min_samples} samples)"
            )
            raise HTTPException(
                status_code=422,
                detail=f"predicted peak VRAM {predicted / gib:.2f} GiB (estimate {est_vram / gib:.2f} GiB "
                f"{basis}) exceeds the {limit / gib:.2f} GiB per-GPU budget; narrow the spot",
            )

        params = request.solve_params()
        params["variant"] = request.variant
        solver_options = dict(request.solver_options or {})
        if request.dtype is not None:
            solver_options["dtype"] = request.dtype
        if solver_options:
            params["solver_options"] = solver_options
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

    @app.post(
        "/v1/solves", status_code=202, response_model=SolveAccepted,
        dependencies=[Depends(auth), Depends(csrf), Depends(rate_limit)],
        responses=examples.POST_SOLVE,
    )
    def post_solve(
        request: SolveRequest,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
    ) -> SolveAccepted:
        return _admit(
            request.spot_dict(), request, response, idempotency_key,
            request_hash(request.hash_body()),
        )

    @app.post(
        "/v1/estimate", response_model=EstimateResponse,
        dependencies=[Depends(auth), Depends(csrf), Depends(rate_limit)],
        responses=examples.ESTIMATE,
        summary="Dry-run admission: node count and VRAM prediction for a spot",
    )
    def estimate(request: EstimateRequest) -> EstimateResponse:
        try:
            config = SpotConfig.from_dict(request.spot.spot_dict())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"bad spot: {exc}") from exc
        num_nodes, est_vram, predicted = _predicted(request.spot.spot_dict(), request.dtype)
        limit = _vram_limit()
        summary = config.resolve().summary()
        return EstimateResponse(
            num_nodes=num_nodes,
            estimated_vram_bytes=est_vram,
            predicted_vram_bytes=predicted,
            vram_limit_bytes=limit,
            over_vram_budget=predicted > limit,
            street=summary["street"],
            num_hands=summary["num_hands"],
            num_removed=summary["num_removed"],
            num_deals=summary["num_deals"],
            weighted=summary["weighted"],
            bet_fractions=summary["bet_fractions"],
        )

    @app.get(
        "/v1/solves", response_model=list[SolveStatus], dependencies=[Depends(auth)],
        responses=examples.LIST_SOLVES, summary="Every job, newest first",
    )
    def list_solves(
        status: Literal["queued", "building", "solving", "done", "cancelled", "failed"] | None = Query(
            None, description="keep only jobs in this state"
        ),
    ) -> list[SolveStatus]:
        jobs = store.all_jobs()
        if status is not None:
            jobs = [job for job in jobs if job["status"] == status]
        return [_status(job) for job in reversed(jobs)]

    @app.get("/v1/solves/{job_id}", response_model=SolveStatus, dependencies=[Depends(auth)], responses=examples.GET_SOLVE)
    def get_solve(job_id: str) -> SolveStatus:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job id")
        return _status(job)

    @app.get(
        "/v1/solves/{job_id}/strategy", response_model=StrategyResponse | HandGridResponse,
        dependencies=[Depends(auth)], responses=examples.GET_STRATEGY,
    )
    def get_strategy(
        job_id: str,
        view: Literal["infosets", "hand_grid"] = Query(
            "infosets", description="`infosets`: one row per infoset (default). `hand_grid`: per decision point, "
            "the 169 hand classes with reach-weighted action frequencies."
        ),
        player: int | None = Query(None, ge=0, le=1, description="hand_grid only: keep one player's decisions"),
        history: str | None = Query(
            None, max_length=500, description="hand_grid only: keep one decision point, e.g. `check,b10` ('' = root)"
        ),
    ) -> StrategyResponse | HandGridResponse:
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
        if view == "hand_grid":
            wanted = None if history is None else [part for part in history.split(",") if part.strip()]
            return HandGridResponse(
                id=job_id,
                **hand_grid(
                    mapped,
                    player=player,
                    history=wanted,
                    ranges=spot_ranges(job["spot"]),
                    blocked=spot_blocked(job["spot"]),
                ),
            )
        return StrategyResponse(id=job_id, **mapped)

    @app.delete(
        "/v1/solves/{job_id}", response_model=SolveStatus,
        dependencies=[Depends(auth), Depends(csrf)], responses=examples.DELETE_SOLVE
    )
    def delete_solve(job_id: str) -> SolveStatus:
        job = store.cancel(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job id")
        scheduler.wake()
        return _status(job)

    # -- Phase 9: browser auth --------------------------------------------------

    @app.post(
        "/v1/auth/login", response_model=LoginResponse,
        dependencies=[Depends(security.rate_limit_dependency(login_limiter, ip_only=True))],
        responses=examples.LOGIN,
        summary="Exchange the API key for an HttpOnly session cookie + CSRF token",
    )
    def login(request: Request, body: LoginRequest, response: Response) -> LoginResponse:
        if signer is None:
            raise HTTPException(status_code=503, detail="auth is disabled (HOLDEM_API_KEY unset); no login needed")
        if not security.key_matches(body.key, api_key or ""):
            raise HTTPException(status_code=401, detail="invalid API key")
        token, expires_at = signer.issue()
        csrf_value = security.csrf_token()
        secure = request.url.scheme == "https" or os.environ.get(
            "HOLDEM_SECURE_COOKIES", ""
        ).lower() in ("1", "true")
        response.set_cookie(
            security.SESSION_COOKIE, token, max_age=signer.ttl_seconds,
            httponly=True, samesite="strict", path="/", secure=secure,
        )
        response.set_cookie(
            security.CSRF_COOKIE, csrf_value, max_age=signer.ttl_seconds,
            httponly=False, samesite="strict", path="/", secure=secure,
        )
        return LoginResponse(expires_at=expires_at, csrf=csrf_value)

    @app.post(
        "/v1/auth/logout", response_model=StatusResponse,
        dependencies=[Depends(auth)], responses=examples.LOGOUT,
    )
    def logout(response: Response) -> StatusResponse:
        response.delete_cookie(security.SESSION_COOKIE, path="/")
        response.delete_cookie(security.CSRF_COOKIE, path="/")
        return StatusResponse(status="logged_out")

    # -- Phase 9: tree navigation + per-node values ------------------------------

    # Per-job LRU of loaded strategy indexes + persisted node EVs, and a
    # per-(job, history, combos) LRU of computed /node responses. Both are
    # bounded; the index cache is keyed by job id and validated against the
    # job's current strategy_path.
    index_cache: OrderedDict[str, tuple[str, Any, int, tuple | None]] = OrderedDict()
    values_cache: OrderedDict[tuple[str, str | None, bool], dict[str, Any]] = OrderedDict()
    cache_lock = threading.Lock()

    def _strategy_index(job_id: str) -> tuple[Any, int, dict[str, Any], tuple | None]:
        """Load the exported strategy as a TreeIndex, with the same guards as /strategy."""
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
        with cache_lock:
            hit = index_cache.get(job_id)
            if hit is not None and hit[0] == path:
                index_cache.move_to_end(job_id)  # OrderedDict LRU refresh
                return hit[1], hit[2], job, hit[3]
        index, iteration = api_nodes.load_index(path)
        evs_pair = api_nodes.load_node_evs(
            path[:-len(".npz")] + ".evs.npz",
            getattr(index, "keys_list", []),
        )
        with cache_lock:
            index_cache[job_id] = (path, index, iteration, evs_pair)
            index_cache.move_to_end(job_id)
            while len(index_cache) > index_cache_jobs:
                index_cache.popitem(last=False)
        return index, iteration, job, evs_pair

    @app.get(
        "/v1/solves/{job_id}/tree", response_model=TreeNodeResponse,
        dependencies=[Depends(auth)], responses=examples.GET_TREE,
        summary="One tree node: player/street/pot/stacks/board/actions/children; no history parsing needed",
    )
    def get_tree(
        job_id: str,
        history: str | None = Query(
            None, max_length=500,
            description="comma- or pipe-separated history; '' = root",
        ),
    ) -> TreeNodeResponse:
        index, iteration, job, _evs = _strategy_index(job_id)
        try:
            hist = parse_history(history)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if index.edge_kind(hist) == "terminal":
            raise HTTPException(status_code=404, detail="no node at this history (terminal or unknown)")
        node = api_nodes.tree_node(index, job["spot"], hist)
        return TreeNodeResponse(id=job_id, iteration=iteration, **node)

    @app.get(
        "/v1/solves/{job_id}/node", response_model=NodeValuesResponse,
        dependencies=[Depends(auth)], responses=examples.GET_NODE,
        summary="Per-node values: reach-weighted frequencies, per-action EVs, hand EV, equity, ranges",
    )
    def get_node(
        job_id: str,
        history: str | None = Query(
            None, max_length=500,
            description="comma- or pipe-separated history; '' = root",
        ),
        combos: bool = Query(False, description="per combo instead of per hand class"),
    ) -> NodeValuesResponse:
        index, iteration, job, evs_pair = _strategy_index(job_id)
        try:
            hist = parse_history(history)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        kind = index.edge_kind(hist)
        if kind == "chance":
            raise HTTPException(status_code=422, detail="this history is a chance node; use /tree for dealt cards")
        if kind == "terminal":
            raise HTTPException(status_code=404, detail="no player node at this history")
        vkey = (job_id, history, combos)
        with cache_lock:
            cached = values_cache.get(vkey)
            if cached is not None:
                values_cache.move_to_end(vkey)
                return NodeValuesResponse(id=job_id, iteration=iteration, **cached)
        try:
            values = api_nodes.node_values(
                index, job["spot"], hist, combos=combos, max_work=node_eval_max_work,
                node_evs=None if evs_pair is None else evs_pair[0],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with cache_lock:
            values_cache[vkey] = values
            values_cache.move_to_end(vkey)
            while len(values_cache) > values_cache_entries:
                values_cache.popitem(last=False)
        return NodeValuesResponse(id=job_id, iteration=iteration, **values)

    # -- Phase 9: live progress (SSE) --------------------------------------------

    def _sse_snapshot(job: dict[str, Any]) -> dict[str, Any]:
        # A running job with cancel_requested stays "building"/"solving" in
        # the store until the runner's finish() lands, but the DELETE that
        # requested it already reported "cancelled". A client connecting in
        # that window must see the effective (cancelled) status, not the
        # stale active one.
        status = job["status"]
        if status in ACTIVE_STATES and job.get("cancel_requested"):
            status = "cancelled"
        return {
            "status": status,
            "iteration": job["iteration"],
            "iterations_run": job["iterations_run"],
            "exploitability": job["exploitability"],
            "exploitability_iteration": job.get("exploitability_iteration"),
        }

    def _sse_record(
        job_id: str, payload: dict[str, Any], last_seq: int | None = None
    ) -> tuple[int, list[tuple[int, dict[str, Any]]]]:
        """Append ``payload`` to the job's replay buffer; return
        ``(event_seq, replay)`` where ``replay`` is the buffered events after
        ``last_seq``.

        Replay eligibility is judged BEFORE the append: a ``Last-Event-ID``
        that is still inside the buffer window at connect time stays valid
        even when the append itself evicts it from a full buffer."""
        with app.state.sse_lock:
            buf = app.state.sse_buffers.setdefault(job_id, deque(maxlen=sse_buffer_size))
            # Replay only when the id is still inside the buffer window;
            # evicted (buf[0] > id), future (id > buf[-1]), empty buffer,
            # malformed, or pre-restart ids all fall through to live.
            replay = []
            if last_seq is not None and buf and buf[0][0] <= last_seq <= buf[-1][0]:
                replay = [entry for entry in buf if entry[0] > last_seq]
            if buf and buf[-1][1] == payload:
                return buf[-1][0], replay
            seq = (buf[-1][0] if buf else 0) + 1
            buf.append((seq, payload))
            return seq, replay

    @app.get(
        "/v1/solves/{job_id}/events", dependencies=[Depends(auth)],
        responses=examples.GET_EVENTS,
        summary="Server-sent progress events; Last-Event-ID resumes a disconnected stream",
    )
    def get_events(
        job_id: str,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job id")

        boot = app.state.sse_boot

        def frame(seq: int, event: str, payload: dict[str, Any]) -> str:
            return "id: %s-%d\nevent: %s\ndata: %s\n\n" % (boot, seq, event, json.dumps(payload))

        def parse_last_event_id() -> int | None:
            """Valid ``<boot>-<seq>`` -> seq; anything else -> None (treated as
            stale: snapshot + live, no replay)."""
            if last_event_id is None:
                return None
            head, sep, tail = last_event_id.rpartition("-")
            if not sep or head != boot:
                return None
            try:
                return int(tail)
            except ValueError:
                return None

        heartbeat_ticks = max(1, int(round(sse_heartbeat_seconds / sse_poll_seconds)))

        def stream() -> Iterator[str]:
            yield "retry: %d\n\n" % sse_retry_ms
            job_now = store.get(job_id)
            if job_now is None:
                yield frame(0, "gone", {"status": "gone"})
                return
            payload = _sse_snapshot(job_now)
            # Replay is captured in the same lock as the snapshot append so
            # the append cannot evict a still-valid Last-Event-ID.
            seq, replay = _sse_record(job_id, payload, parse_last_event_id())
            # Every connection — new or reconnecting — starts with a snapshot
            # of the current job state; the snapshot replaces client state.
            yield frame(seq, "snapshot", payload)

            # Finished jobs: snapshot then the terminal done event, no replay.
            # The payload's effective status decides: a cancel_requested job
            # reads "cancelled" and ends the stream right after the snapshot.
            if payload["status"] in TERMINAL_STATES:
                yield frame(seq, "done", payload)
                return

            for bseq, bpayload in replay:
                yield frame(bseq, "progress", bpayload)

            last_payload = payload
            ticks = 0
            while True:
                time.sleep(sse_poll_seconds)
                ticks += 1
                job_now = store.get(job_id)
                if job_now is None:
                    yield frame(0, "gone", {"status": "gone"})
                    return
                payload = _sse_snapshot(job_now)
                seq, _replay = _sse_record(job_id, payload)
                if payload != last_payload:
                    yield frame(seq, "progress", payload)
                    last_payload = payload
                elif ticks % heartbeat_ticks == 0:
                    yield ": keep-alive\n\n"
                if payload["status"] in TERMINAL_STATES:
                    yield frame(seq, "done", payload)
                    return

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # -- Phase 9: pin/unpin ------------------------------------------------------

    @app.post(
        "/v1/solves/{job_id}/pin", response_model=PinResponse,
        dependencies=[Depends(auth), Depends(csrf)], responses=examples.PIN,
        summary="Pin a solve's strategy so retention never deletes it",
    )
    def pin_solve(job_id: str) -> PinResponse:
        job = store.set_pinned(job_id, True)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job id")
        return PinResponse(id=job_id, pinned=True)

    @app.post(
        "/v1/solves/{job_id}/unpin", response_model=PinResponse,
        dependencies=[Depends(auth), Depends(csrf)], responses=examples.PIN,
        summary="Unpin a solve; retention applies again",
    )
    def unpin_solve(job_id: str) -> PinResponse:
        job = store.set_pinned(job_id, False)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job id")
        return PinResponse(id=job_id, pinned=False)

    # -- Phase 9: saved spots -----------------------------------------------------

    def _validate_spot_dict(spot: dict[str, Any]) -> None:
        try:
            SpotConfig.from_dict(spot)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"bad spot: {exc}") from exc

    @app.post(
        "/v1/spots", status_code=201, response_model=SpotRecord,
        dependencies=[Depends(auth), Depends(csrf)], responses=examples.SPOT,
    )
    def create_spot(request: SpotCreate) -> SpotRecord:
        spot_dict = request.spot.spot_dict()
        _validate_spot_dict(spot_dict)
        try:
            spot = store.create_spot(request.name, spot_dict, request.tags, request.notes, max_spots=max_spots)
        except QueueFullError as exc:
            raise HTTPException(
                status_code=429, detail=str(exc), headers={"Retry-After": "30"}
            ) from exc
        return SpotRecord(**spot)

    @app.get("/v1/spots", response_model=list[SpotRecord], dependencies=[Depends(auth)], responses=examples.SPOT_LIST)
    def list_spots(tag: str | None = Query(None, max_length=64)) -> list[SpotRecord]:
        return [SpotRecord(**spot) for spot in store.list_spots(tag=tag)]

    @app.get("/v1/spots/{spot_id}", response_model=SpotRecord, dependencies=[Depends(auth)], responses=examples.SPOT_LIST)
    def get_spot(spot_id: str) -> SpotRecord:
        spot = store.get_spot(spot_id)
        if spot is None:
            raise HTTPException(status_code=404, detail="unknown spot id")
        return SpotRecord(**spot)

    @app.patch(
        "/v1/spots/{spot_id}", response_model=SpotRecord,
        dependencies=[Depends(auth), Depends(csrf)], responses=examples.SPOT_LIST,
    )
    def update_spot(spot_id: str, request: SpotUpdate) -> SpotRecord:
        spot_dict = request.spot.spot_dict() if request.spot is not None else None
        if spot_dict is not None:
            _validate_spot_dict(spot_dict)
        spot = store.update_spot(
            spot_id,
            name=request.name,
            spot=spot_dict,
            tags=request.tags,
            notes=request.notes,
        )
        if spot is None:
            raise HTTPException(status_code=404, detail="unknown spot id")
        return SpotRecord(**spot)

    @app.delete(
        "/v1/spots/{spot_id}", response_model=StatusResponse,
        dependencies=[Depends(auth), Depends(csrf)], responses=examples.DELETE_SPOT,
    )
    def delete_spot(spot_id: str) -> StatusResponse:
        if not store.delete_spot(spot_id):
            raise HTTPException(status_code=404, detail="unknown spot id")
        return StatusResponse(status="deleted")

    @app.post(
        "/v1/spots/{spot_id}/duplicate", status_code=201, response_model=SpotRecord,
        dependencies=[Depends(auth), Depends(csrf)], responses=examples.SPOT,
    )
    def duplicate_spot(
        spot_id: str,
        name: str | None = Query(None, max_length=200, description="default '<name> (copy)'"),
    ) -> SpotRecord:
        spot = store.get_spot(spot_id)
        if spot is None:
            raise HTTPException(status_code=404, detail="unknown spot id")
        copy_name = name or (spot["name"] + " (copy)")
        if len(copy_name) > 200:
            raise HTTPException(status_code=422, detail="name must be at most 200 chars; pass ?name=")
        try:
            copy = store.create_spot(
                copy_name,
                spot["spot"], spot["tags"], spot["notes"], max_spots=max_spots,
            )
        except QueueFullError as exc:
            raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "30"}) from exc
        return SpotRecord(**copy)

    # -- Phase 10: web UI (static SPA) -----------------------------------------

    ui_dir = os.environ.get("HOLDEM_UI_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "ui", "dist",
    )
    if os.path.isdir(ui_dir):

        class _SPAFiles(StaticFiles):
            """StaticFiles with an index.html fallback so client-side routes
            like ``/ui/jobs`` load the SPA."""

            async def get_response(self, path: str, scope: dict[str, Any]) -> Response:
                try:
                    return await super().get_response(path, scope)
                except StarletteHTTPException as exc:
                    if exc.status_code == 404:
                        return await super().get_response("index.html", scope)
                    raise

        @app.get("/", include_in_schema=False)
        def index() -> RedirectResponse:
            return RedirectResponse("/ui/")

        app.mount("/ui", _SPAFiles(directory=ui_dir, html=True), name="ui")
    else:
        LOG.info("no UI build at %s; /ui is not served (run `npm run build` in ui/)", ui_dir)

    @app.post(
        "/v1/spots/{spot_id}/solve", status_code=202, response_model=SolveAccepted,
        dependencies=[Depends(auth), Depends(csrf), Depends(rate_limit)],
        responses=examples.POST_SOLVE,
        summary="Queue a solve of the saved spot (same admission as POST /v1/solves)",
    )
    def solve_spot(
        spot_id: str,
        request: SolveParams,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
    ) -> SolveAccepted:
        spot = store.get_spot(spot_id)
        if spot is None:
            raise HTTPException(status_code=404, detail="unknown spot id")
        body_hash = request_hash({"spot_id": spot_id, "params": request.model_dump(mode="json")})
        return _admit(spot["spot"], request, response, idempotency_key, body_hash)

    return app


def main() -> None:
    import uvicorn

    port = int(os.environ.get("HOLDEM_API_PORT", "8000"))
    host = os.environ.get("HOLDEM_API_HOST", "127.0.0.1")
    uvicorn.run("holdem_solver.api.app:create_app", factory=True, host=host, port=port)


if __name__ == "__main__":
    main()
