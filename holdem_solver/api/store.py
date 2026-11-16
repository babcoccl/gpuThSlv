"""SQLite job store for ``POST /v1/solves`` jobs.

One process owns the file; a single lock serializes all access. On startup,
``fail_in_flight`` marks anything left queued/building/solving as failed —
those jobs' worker state is gone with the previous process anyway.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Iterator

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

_IN_FLIGHT = ("queued", "building", "solving")


class JobStore:
    def __init__(self, path: str) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._db:
            self._db.execute(_SCHEMA)
        self.fail_in_flight("api restarted before the job finished")

    def close(self) -> None:
        self._db.close()

    def fail_in_flight(self, error: str) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE jobs SET status='failed', error=?, finished_at=? WHERE status IN ('queued','building','solving')",
                (error, time.time()),
            )
            return cur.rowcount

    def create(self, spot: dict[str, Any], params: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO jobs (id, status, gpu, spot, params, created_at) VALUES (?,?,?,?,?,?)",
                (job_id, "queued", None, json.dumps(spot), json.dumps(params), time.time()),
            )
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._decode(row) if row is not None else None

    def update(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = []
        values = []
        for key, value in fields.items():
            cols.append(f"{key}=?")
            if key in ("measured_vram", "exploitability_history"):
                value = json.dumps(value)
            values.append(value)
        values.append(job_id)
        with self._lock, self._db:
            self._db.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id=?", values)

    def next_queued(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
        return self._decode(row) if row is not None else None

    def claim(self, job_id: str, gpu: int) -> bool:
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE jobs SET gpu=?, status='building', started_at=? WHERE id=? AND status='queued'",
                (gpu, time.time(), job_id),
            )
            return cur.rowcount == 1

    def request_cancel(self, job_id: str) -> dict[str, Any] | None:
        """Flag a job for cancellation; returns its row or None if unknown."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE jobs SET cancel_requested=1 WHERE id=? AND status IN ('queued','building','solving')",
                (job_id,),
            )
            if cur.rowcount == 1:
                row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                return self._decode(row)
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._decode(row) if row is not None else None

    def running_on(self, gpu: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM jobs WHERE gpu=? AND status IN ('building','solving') LIMIT 1", (gpu,)
            ).fetchone()
        return self._decode(row) if row is not None else None

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
