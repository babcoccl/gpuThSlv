"""SQLite job store for ``POST /v1/solves`` jobs: the persistent queue.

One process owns the file; a single lock serializes all access. Every status
change is a guarded transition (``... WHERE status IN (...)``), so a job that
is already terminal cannot be revived by a late runner update, and cancel vs
claim races resolve to exactly one winner.

On startup ``recover_after_restart`` keeps queued jobs queued (they are
dispatched again) and fails building/solving ones with a reason: their worker
state died with the previous API process, and they are never replayed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    gpu INTEGER,
    spot TEXT NOT NULL,
    params TEXT NOT NULL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    elapsed_seconds REAL,
    build_seconds REAL,
    iteration INTEGER,
    iterations_run INTEGER,
    exploitability REAL,
    num_nodes INTEGER,
    num_infosets INTEGER,
    estimated_vram_bytes INTEGER,
    measured_vram TEXT,
    target_reached INTEGER,
    profile_value_p0 REAL,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    strategy_path TEXT,
    exploitability_history TEXT
)
"""

# Phase 4 columns, added to older databases by ``_migrate``.
_COLUMNS = {
    "priority": "INTEGER NOT NULL DEFAULT 0",
    "timeout_seconds": "REAL",
    "idempotency_key": "TEXT",
    "request_hash": "TEXT",
    "error_code": "TEXT",
    "predicted_vram_bytes": "INTEGER",
    "peak_vram_bytes": "INTEGER",
    "dtype": "TEXT",
    "strategy_expired_at": "REAL",
}

ACTIVE = ("queued", "building", "solving")
RUNNING = ("building", "solving")
TERMINAL = ("done", "cancelled", "failed")
_JSON_FIELDS = ("measured_vram", "exploitability_history")


class QueueFullError(Exception):
    def __init__(self, depth: int, limit: int) -> None:
        super().__init__(f"queue is full ({depth} queued, max_queue_depth={limit})")
        self.depth = depth
        self.limit = limit


class IdempotencyConflictError(Exception):
    """The key was already used with a different request body."""


def request_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _in(values: tuple[str, ...]) -> str:
    return "(" + ",".join("'%s'" % v for v in values) + ")"


class JobStore:
    def __init__(self, path: str) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(_SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        have = {row["name"] for row in self._db.execute("PRAGMA table_info(jobs)")}
        for name, decl in _COLUMNS.items():
            if name not in have:
                self._db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")
        self._db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency ON jobs(idempotency_key) "
            "WHERE idempotency_key IS NOT NULL"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, priority, created_at)")

    def close(self) -> None:
        self._db.close()

    # -- restart -----------------------------------------------------------

    def recover_after_restart(self) -> list[str]:
        """Fail jobs that were running when the previous API process died."""
        now = time.time()
        with self._lock, self._db:
            rows = self._db.execute(f"SELECT * FROM jobs WHERE status IN {_IN_RUNNING}").fetchall()
            for row in rows:
                at = "" if row["iteration"] is None else f" at iteration {row['iteration']}"
                error = (
                    f"API restarted while the job was {row['status']} on gpu {row['gpu']}{at}; "
                    "its worker state was lost and jobs are not replayed; resubmit it"
                )
                self._db.execute(
                    "UPDATE jobs SET status='failed', error=?, error_code='api_restart', finished_at=? WHERE id=?",
                    (error, now, row["id"]),
                )
        return [row["id"] for row in rows]

    # -- create / idempotency ---------------------------------------------

    def find_idempotent(self, key: str, body_hash: str) -> dict[str, Any] | None:
        with self._lock:
            return self._find_idempotent_locked(key, body_hash)

    def _find_idempotent_locked(self, key: str, body_hash: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["request_hash"] != body_hash:
            raise IdempotencyConflictError(
                f"Idempotency-Key {key!r} was already used for job {row['id']} with a different request body"
            )
        return self._decode(row)

    def create(
        self,
        spot: dict[str, Any],
        params: dict[str, Any],
        *,
        priority: int = 0,
        timeout_seconds: float | None = None,
        idempotency_key: str | None = None,
        body_hash: str | None = None,
        max_queue_depth: int | None = None,
        **fields: Any,
    ) -> tuple[dict[str, Any], bool]:
        """Insert a queued job. Returns ``(job, replayed)``.

        The idempotency lookup and the queue-depth check happen under the
        same lock as the insert, so concurrent retries of one key create one
        job and the depth limit cannot be overshot.
        """
        job_id = uuid.uuid4().hex[:12]
        with self._lock, self._db:
            if idempotency_key is not None:
                existing = self._find_idempotent_locked(idempotency_key, body_hash or "")
                if existing is not None:
                    return existing, True
            if max_queue_depth is not None:
                depth = self._db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0]
                if depth >= max_queue_depth:
                    raise QueueFullError(depth, max_queue_depth)
            cols = {
                "id": job_id,
                "status": "queued",
                "spot": json.dumps(spot),
                "params": json.dumps(params),
                "created_at": time.time(),
                "priority": int(priority),
                "timeout_seconds": timeout_seconds,
                "idempotency_key": idempotency_key,
                "request_hash": body_hash,
                **fields,
            }
            self._db.execute(
                f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [self._encode(k, v) for k, v in cols.items()],
            )
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._decode(row), False

    # -- reads -------------------------------------------------------------

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._decode(row) if row is not None else None

    def queued(self) -> list[dict[str, Any]]:
        """Queued jobs in dispatch order: highest priority first, then FIFO."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY priority DESC, created_at, rowid"
            ).fetchall()
        return [self._decode(row) for row in rows]

    def next_queued(self) -> dict[str, Any] | None:
        jobs = self.queued()
        return jobs[0] if jobs else None

    def queue_depth(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0]

    def running_on(self, gpu: int | None) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT * FROM jobs WHERE gpu IS ? AND status IN {_IN_RUNNING} LIMIT 1", (gpu,)
            ).fetchone()
        return self._decode(row) if row is not None else None

    def all_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM jobs ORDER BY created_at").fetchall()
        return [self._decode(row) for row in rows]

    def vram_samples(self, dtype: str, min_estimate: int = 1, limit: int = 50) -> list[tuple[int, int]]:
        """``(estimated, measured peak)`` bytes of the latest ``limit`` finished
        solves whose estimate is at least ``min_estimate``."""
        with self._lock:
            rows = self._db.execute(
                "SELECT estimated_vram_bytes, peak_vram_bytes FROM jobs WHERE dtype IS ? "
                "AND estimated_vram_bytes >= ? AND peak_vram_bytes > 0 "
                "ORDER BY finished_at DESC LIMIT ?",
                (dtype, max(1, int(min_estimate)), limit),
            ).fetchall()
        return [(int(r[0]), int(r[1])) for r in rows]

    # -- writes ------------------------------------------------------------

    def update(self, job_id: str, **fields: Any) -> bool:
        """Update non-status fields of an active job; no-op once it is terminal."""
        if not fields:
            return False
        assert "status" not in fields, "use claim/transition/finish for status changes"
        return self._update(job_id, ACTIVE, fields)

    def transition(self, job_id: str, to: str, from_states: tuple[str, ...], **fields: Any) -> bool:
        return self._update(job_id, from_states, {"status": to, **fields})

    def finish(self, job_id: str, status: str, **fields: Any) -> bool:
        """Move an active job to a terminal state; false if it already was terminal."""
        assert status in TERMINAL
        fields.setdefault("finished_at", time.time())
        return self._update(job_id, ACTIVE, {"status": status, **fields})

    def _update(self, job_id: str, from_states: tuple[str, ...], fields: dict[str, Any]) -> bool:
        cols = [f"{key}=?" for key in fields]
        values = [self._encode(key, value) for key, value in fields.items()]
        values.append(job_id)
        with self._lock, self._db:
            cur = self._db.execute(
                f"UPDATE jobs SET {', '.join(cols)} WHERE id=? AND status IN {_in(from_states)}", values
            )
            return cur.rowcount == 1

    def set_fields(self, job_id: str, **fields: Any) -> None:
        """Unguarded update for bookkeeping on terminal jobs (retention)."""
        cols = [f"{key}=?" for key in fields]
        values = [self._encode(key, value) for key, value in fields.items()] + [job_id]
        with self._lock, self._db:
            self._db.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id=?", values)

    def claim(self, job_id: str, gpu: int | None) -> bool:
        """queued -> building on ``gpu``; false if it was cancelled or claimed meanwhile."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE jobs SET gpu=?, status='building', started_at=? "
                "WHERE id=? AND status='queued' AND cancel_requested=0",
                (gpu, time.time(), job_id),
            )
            return cur.rowcount == 1

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        """Atomically cancel: a queued job becomes ``cancelled`` now; a running
        one gets ``cancel_requested`` for its runner; a terminal one is left
        alone. Idempotent, so any number of concurrent DELETEs agree.
        Returns the row, or None if the id is unknown."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE jobs SET status='cancelled', cancel_requested=1, finished_at=? "
                "WHERE id=? AND status='queued'",
                (time.time(), job_id),
            )
            self._db.execute(
                f"UPDATE jobs SET cancel_requested=1 WHERE id=? AND status IN {_IN_RUNNING}", (job_id,)
            )
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._decode(row) if row is not None else None

    request_cancel = cancel

    def expired_strategies(self, cutoff: float) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM jobs WHERE status IN {_in(TERMINAL)} AND strategy_path IS NOT NULL "
                "AND finished_at < ?",
                (cutoff,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def strategy_paths(self) -> set[str]:
        with self._lock:
            rows = self._db.execute("SELECT strategy_path FROM jobs WHERE strategy_path IS NOT NULL").fetchall()
        return {row[0] for row in rows}

    # -- codec -------------------------------------------------------------

    @staticmethod
    def _encode(key: str, value: Any) -> Any:
        if key in _JSON_FIELDS and value is not None:
            return json.dumps(value)
        return value

    def _decode(self, row: sqlite3.Row) -> dict[str, Any]:
        job = dict(row)
        job["spot"] = json.loads(job["spot"])
        job["params"] = json.loads(job["params"])
        job["measured_vram"] = json.loads(job["measured_vram"]) if job["measured_vram"] else None
        job["exploitability_history"] = (
            json.loads(job["exploitability_history"]) if job["exploitability_history"] else []
        )
        job["target_reached"] = None if job["target_reached"] is None else bool(job["target_reached"])
        job["cancel_requested"] = bool(job["cancel_requested"])
        return job


_IN_RUNNING = _in(RUNNING)
