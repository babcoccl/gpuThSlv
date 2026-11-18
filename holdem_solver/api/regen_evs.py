"""Runs in ``.venv-api``.

``python -m holdem_solver.api.regen_evs [--job ID | --all]`` — regenerate
the persisted ``strategy-<id>.evs.npz`` node-EV companions.

Since Phase 10.6 the request path never writes: when ``load_node_evs``
declines a file, ``/node`` computes the table in memory instead. This
command is the offline migration that re-persists the files. It
regenerates evs files only for jobs whose strategy file still exists,
skips expired jobs, is idempotent (re-running rewrites an identical,
checksum-valid file), and writes through ``nodes.write_node_evs`` — the
same single atomic writer the job-completion exporter uses.

``--data-dir`` overrides ``HOLDEM_API_DATA_DIR`` (default ``./api-data``).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from . import nodes as api_nodes
from .store import JobStore

LOG = logging.getLogger("holdem_solver.api.regen_evs")


def regen_job(job: dict[str, Any], store: JobStore | None = None) -> bool:
    """Recompute + atomically rewrite one job's ``*.evs.npz``.

    Returns False when the job is skipped (expired by retention, no
    strategy path, or the strategy file no longer exists — e.g. a
    concurrent retention cleanup removed it). Compute failures also
    return False; the job's file (if any) is left untouched.
    """
    if job.get("strategy_expired_at"):
        return False
    path = job.get("strategy_path")
    if not path or not os.path.exists(path):
        return False
    try:
        index, iteration = api_nodes.load_index(path)
        evs, masses = api_nodes.export_node_evs(index, job["spot"])
        flat, mass = api_nodes.pack_node_evs(
            evs, masses, index.keys_list, index.actions_list, index.offsets
        )
        # Re-check before the write: a retention cleanup that expired the
        # job during the compute must not get a fresh companion file.
        if store is not None:
            current = store.get(job["id"])
            if (
                current is None
                or current.get("strategy_expired_at")
                or current.get("strategy_path") != path
            ):
                return False
        if not os.path.exists(path):
            return False
        api_nodes.write_node_evs(
            api_nodes.evs_path_for(path), index.keys_list,
            index.actions_list, index.offsets, flat, mass, iteration,
        )
    except Exception:
        LOG.warning("job %s: evs regeneration failed", job["id"], exc_info=True)
        return False
    return True


def regen(store: JobStore, job_ids: list[str] | None = None) -> dict[str, int]:
    """Regenerate evs files for ``job_ids`` or every job (``--all``).

    Expired jobs and jobs without a live strategy file are skipped.
    """
    jobs = store.all_jobs() if job_ids is None else [
        j for j in (store.get(jid) for jid in job_ids) if j is not None
    ]
    regenerated = skipped = 0
    for job in jobs:
        if regen_job(job, store):
            regenerated += 1
            LOG.info("job %s: evs.npz regenerated", job["id"])
        else:
            skipped += 1
    return {"regenerated": regenerated, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        prog="python -m holdem_solver.api.regen_evs",
        description="Regenerate persisted strategy-<id>.evs.npz files",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="job DB + strategy directory (default: HOLDEM_API_DATA_DIR or ./api-data)",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--job", action="append", default=None,
                        help="regenerate one job id (repeatable)")
    target.add_argument("--all", action="store_true",
                        help="regenerate every job with a live strategy file")
    args = parser.parse_args(argv)
    if args.job is None and not args.all:
        parser.error("pass --job ID or --all")

    data_dir = args.data_dir or os.environ.get("HOLDEM_API_DATA_DIR", "./api-data")
    store = JobStore(os.path.join(data_dir, "solves.db"))
    try:
        counts = regen(store, args.job)
    finally:
        store.close()
    print("regenerated=%d skipped=%d" % (counts["regenerated"], counts["skipped"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
