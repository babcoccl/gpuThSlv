"""Pydantic request/response models for the REST API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from holdem_solver.protocol import BET_SIZE_SETS, DEFAULT_MAX_NODES

VARIANTS = ("cfr", "cfr_plus", "dcfr", "pcfr_plus")
DTYPES = ("float32", "float64")

SOLVE_REQUEST_EXAMPLE = {
    "board": "KsJsTh7d2c",
    "range0": "QQ+, AKs",
    "range1": "TT+, AQs+, AKo",
    "pot": 20.0,
    "stack": 100.0,
    "bet_sizes": "standard",
    "variant": "cfr_plus",
    "dtype": "float32",
    "target_exploitability": 0.002,
    "max_iterations": 20000,
    "progress_every": 100,
    "priority": 0,
    "timeout_seconds": 600,
}


class SolveParams(BaseModel):
    """Solver parameters shared by ``POST /v1/solves`` and
    ``POST /v1/spots/{id}/solve`` (Phase 9)."""

    model_config = ConfigDict(extra="forbid")

    variant: Literal["cfr", "cfr_plus", "dcfr", "pcfr_plus"] = "cfr_plus"
    dtype: Literal["float32", "float64"] | None = Field(
        default=None,
        description="solver precision; default float32. float64 doubles VRAM for the same tree. "
        "Same as solver_options.dtype (give one, or the same value in both).",
    )
    solver_options: dict[str, Any] | None = None
    iterations: int | None = Field(default=None, gt=0)
    time_budget: float | None = Field(default=None, ge=0)
    target_exploitability: float | None = Field(default=None, gt=0)
    max_iterations: int | None = Field(default=None, gt=0)
    progress_every: int | None = Field(default=None, gt=0)

    # Scheduling
    priority: int = Field(default=0, ge=-100, le=100, description="higher runs first; FIFO within a priority")
    timeout_seconds: float | None = Field(
        default=None, gt=0, description="wall-clock limit from start of build; the job fails with error_code=timeout"
    )

    @model_validator(mode="after")
    def check_dtype(self) -> "SolveParams":
        inner = (self.solver_options or {}).get("dtype")
        if inner is not None and inner not in DTYPES:
            raise ValueError(f"solver_options.dtype must be one of {DTYPES}")
        if inner is not None and self.dtype is not None and inner != self.dtype:
            raise ValueError("dtype and solver_options.dtype disagree")
        return self

    @model_validator(mode="after")
    def check_stop_conditions(self) -> "SolveParams":
        modes = [self.iterations is not None, self.time_budget is not None, self.target_exploitability is not None]
        if not any(modes):
            raise ValueError(
                "at least one stop condition is required: iterations, time_budget, or target_exploitability"
            )
        if self.iterations is not None and self.time_budget is not None:
            raise ValueError("iterations and time_budget are alternative stop modes; give only one")
        if self.iterations is not None and self.target_exploitability is not None:
            raise ValueError("use max_iterations (not iterations) as the iteration cap with target_exploitability")
        if self.target_exploitability is not None and self.time_budget is None and self.max_iterations is None:
            raise ValueError(
                "target_exploitability mode has no iteration cap in the engine; "
                "also require time_budget or max_iterations"
            )
        if self.max_iterations is not None and self.target_exploitability is None:
            raise ValueError("max_iterations is only a cap for target_exploitability mode")
        return self

    def effective_dtype(self) -> str:
        return self.dtype or (self.solver_options or {}).get("dtype") or "float32"

    def solve_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {}
        for key in ("iterations", "time_budget", "target_exploitability", "progress_every"):
            value = getattr(self, key)
            if value is not None:
                params[key] = value
        return params


class SolveRequest(SolveParams):
    """Body of ``POST /v1/solves``: a ``SpotConfig`` plus solver parameters."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [SOLVE_REQUEST_EXAMPLE]})

    # SpotConfig fields
    board: list[str] | str = Field(max_length=32, description="5 river cards (or 4 for a turn spot)")
    range0: str = Field(max_length=4096)
    range1: str = Field(max_length=4096)
    pot: float = 20.0
    stack: float = 100.0
    bet_sizes: str | list[float] | dict[str, list[float]] = Field(
        default="standard", max_length=64,
        description="a preset name or pot fractions; a dict gives per-player "
        '({"0": [...], "1": [...]}) or per-street ({"turn": [...], "river": [...]}, '
        "turn spots only) lists",
    )
    all_in: bool = False
    raise_cap: int = Field(
        default=0, ge=0, le=8,
        description="max raises per street; 0 disables raises",
    )
    raise_sizes: list[list[Any]] = Field(
        default_factory=list,
        description='raise options as [kind, value]: ["x", m] raises to m times the bet '
        'faced, ["p", f] to the bet plus f times the pot after calling',
    )
    dead_cards: list[str] = Field(default_factory=list, max_length=8)
    name: str | None = Field(default=None, max_length=200)
    max_nodes: int | None = DEFAULT_MAX_NODES

    @model_validator(mode="after")
    def check_raise_sizes(self) -> "SolveRequest":
        for entry in self.raise_sizes:
            if (
                not isinstance(entry, (list, tuple))
                or len(entry) != 2
                or entry[0] not in ("x", "p")
                or not isinstance(entry[1], (int, float))
                or entry[1] <= 0
            ):
                raise ValueError(
                    'raise_sizes entries must be ["x", m] or ["p", f] with a positive number'
                )
        if self.raise_sizes and self.raise_cap == 0:
            raise ValueError("raise_sizes requires raise_cap > 0")
        return self

    def hash_body(self) -> dict[str, Any]:
        """Body for the idempotency hash; an unset dtype hashes as before Phase 5."""
        body = self.model_dump(mode="json")
        if body.get("dtype") is None:
            body.pop("dtype", None)
        # New in Phase 8.5: keep retries of pre-upgrade keys valid by
        # hashing omitted/default raise fields exactly as before.
        if body.get("raise_cap") == 0:
            body.pop("raise_cap", None)
        if not body.get("raise_sizes"):
            body.pop("raise_sizes", None)
        return body

    def spot_dict(self) -> dict[str, Any]:
        return {
            "board": self.board if isinstance(self.board, str) else list(self.board),
            "range0": self.range0,
            "range1": self.range1,
            "pot": self.pot,
            "stack": self.stack,
            "bet_sizes": self.bet_sizes,
            "all_in": self.all_in,
            "raise_cap": self.raise_cap,
            "raise_sizes": [list(size) for size in self.raise_sizes],
            "dead_cards": list(self.dead_cards),
            "name": self.name,
            "max_nodes": self.max_nodes,
        }

class SpotCreate(BaseModel):
    """``POST /v1/spots`` body (Phase 9)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=200)
    spot: dict[str, Any]
    tags: list[str] = Field(default_factory=list, max_length=32)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def check_tags(self) -> "SpotCreate":
        for tag in self.tags:
            if len(tag) > 64:
                raise ValueError("tags must be at most 64 chars")
        return self


class SpotUpdate(BaseModel):
    """``PATCH /v1/spots/{id}`` body (Phase 9); omitted fields are unchanged."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    spot: dict[str, Any] | None = None
    tags: list[str] | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)


class SpotRecord(BaseModel):
    id: str
    name: str
    spot: dict[str, Any]
    tags: list[str]
    notes: str
    created_at: float
    updated_at: float


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(max_length=512)


class LoginResponse(BaseModel):
    expires_at: float
    csrf: str


class PinResponse(BaseModel):
    id: str
    pinned: bool


class TreeChild(BaseModel):
    edge: str
    kind: Literal["player", "chance", "terminal"]
    outcome: str | None = None
    history: list[str] | None = None
    player: int | None = None
    actions: list[HandGridAction] | None = None


class TreeNodeResponse(BaseModel):
    id: str
    iteration: int
    history: list[str]
    street: str
    pot: float
    stacks: dict[str, float]
    board: list[str]
    is_chance: bool
    player: int | None = None
    actions: list[HandGridAction] = Field(default_factory=list)
    dealt_cards: list[str] | None = None
    children: list[TreeChild] = Field(default_factory=list)


class NodeHandRow(BaseModel):
    hand_class: str | None = None
    combo: str | None = None
    combos: int | None = None
    reach: float
    weight: float
    frequencies: dict[str, float | None]
    action_evs: dict[str, float | None]
    ev: float | None
    equity: float | None


class RangeEntry(BaseModel):
    hand_class: str | None = None
    combo: str | None = None
    combos: int | None = None
    reach: float
    share: float


class NodeValuesResponse(BaseModel):
    id: str
    iteration: int
    history: list[str]
    street: str
    player: int
    pot: float
    stacks: dict[str, float]
    board: list[str]
    actions: list[HandGridAction]
    ranges: dict[str, list[RangeEntry]]
    hands: list[NodeHandRow]


class SolveAccepted(BaseModel):
    id: str
    status: str = "queued"
    replayed: bool = False
    queue_depth: int | None = None
    predicted_vram_bytes: int | None = None


class SolveStatus(BaseModel):
    id: str
    status: Literal["queued", "building", "solving", "done", "cancelled", "failed"]
    gpu: int | None = None
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    elapsed_seconds: float | None = None
    build_seconds: float | None = None
    iteration: int | None = None
    iterations_run: int | None = None
    exploitability: float | None = Field(
        default=None, description="latest measured exploitability of the average strategy"
    )
    exploitability_iteration: int | None = Field(
        default=None, description="solver iteration at which `exploitability` was measured"
    )
    exploitability_history: list[dict[str, Any]] = Field(default_factory=list)
    dtype: str = Field(default="float32", description="solver precision: float32 or float64")
    num_nodes: int | None = None
    num_infosets: int | None = None
    estimated_vram_bytes: int | None = None
    measured_vram: dict[str, int] | None = None
    target_exploitability: float | None = None
    target_reached: bool | None = None
    profile_value_p0: float | None = None
    error: str | None = None
    error_code: str | None = None
    priority: int = 0
    timeout_seconds: float | None = None
    predicted_vram_bytes: int | None = None
    peak_vram_bytes: int | None = None
    strategy_available: bool = False
    strategy_expired_at: float | None = None


class StrategyAction(BaseModel):
    name: str
    label: str
    frequency: float


class InfosetStrategy(BaseModel):
    key: str
    player: int | None
    hand: str | None
    history: list[str] | None
    actions: list[StrategyAction]


class StrategyResponse(BaseModel):
    id: str
    view: Literal["infosets"] = "infosets"
    iteration: int
    num_infosets: int
    infosets: list[InfosetStrategy]
    unmapped_keys: int


class HandGridCell(BaseModel):
    hand_class: str
    combos: int = Field(description="combos of this class with positive reach weight at the node")
    reach: float = Field(description="total reach weight of the class at the node")
    frequencies: dict[str, float]


class HandGridAction(BaseModel):
    name: str
    label: str


class HandGridNode(BaseModel):
    player: int
    history: list[str]
    actions: list[HandGridAction]
    combos: int
    grid: list[HandGridCell] = Field(description="169 classes in 13x13 chart order (row-major, AA first)")


class HandGridResponse(BaseModel):
    id: str
    view: Literal["hand_grid"] = "hand_grid"
    iteration: int
    num_infosets: int
    nodes: list[HandGridNode]


class GpuInfo(BaseModel):
    gpu: int
    state: str
    device: str | None = None
    worker_pid: int | None = None
    worker_vram: dict[str, int] = Field(default_factory=dict)
    pynvml: dict[str, Any] | None = None
    job_id: str | None = None
    healthy: bool = True
    restarts: int = 0
    vram_capacity_bytes: int | None = None
    heartbeat_age_seconds: float | None = None
    heartbeat_latency_seconds: float | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
