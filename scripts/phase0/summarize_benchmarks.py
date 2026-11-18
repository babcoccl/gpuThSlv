#!/usr/bin/env python3
"""Runs in ``.venv-engine`` and ``.venv-api``.

Summarize Phase 0 iter_bench JSONL logs into benchmark tables."""

import argparse
import json
import statistics
from pathlib import Path


def mode_from_argv(argv):
    if "--cuda-graph" in argv:
        return "cuda_graph"
    if "--no-cuda-graph" in argv:
        return "eager"
    return "unknown"


def mode_from_bench(row):
    return "cuda_graph" if row.get("cuda_graph_active") else "eager"


def read_statuses(path):
    statuses = {}
    if not path.exists():
        return statuses
    for line in path.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 6:
            start, end, gpu, game, mode, status = parts[:6]
            statuses[(gpu, game, mode)] = {"start": start, "end": end, "status": status}
    return statuses


def mib(value):
    return round(value / (1024 * 1024), 3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", default=str(Path(__file__).resolve().parent / "logs"))
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-csv", default=None)
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    rows = []
    memory = {}

    for gpu in ("0", "1"):
        bench_path = log_dir / f"iter_bench_gpu{gpu}.jsonl"
        status_path = log_dir / f"iter_bench_status_gpu{gpu}.tsv"
        statuses = read_statuses(status_path)
        if not bench_path.exists():
            continue
        for line in bench_path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if "memory_probe" in row:
                probe = row["memory_probe"]
                argv = probe.get("argv", [])
                game = None
                for i, arg in enumerate(argv):
                    if arg == "--game" and i + 1 < len(argv):
                        game = argv[i + 1]
                key = (gpu, game, mode_from_argv(argv))
                memory[key] = probe
                continue
            game = row.get("game")
            mode = mode_from_bench(row)
            key = (gpu, game, mode)
            rows.append({"key": key, "row": row, "status": statuses.get((gpu, game, "graph" if mode == "cuda_graph" else "eager"))})

    grouped = {}
    for item in rows:
        grouped.setdefault(item["key"], []).append(item)

    summary = []
    for key in sorted(grouped):
        gpu, game, mode = key
        items = grouped[key]
        timings = [item["row"]["ms_per_iter"] for item in items]
        reps = [item["row"].get("rep") for item in items]
        status = items[0]["status"] or {}
        probe = memory.get(key, {})
        summary.append({
            "gpu": int(gpu),
            "game": game,
            "mode": mode,
            "status": status.get("status"),
            "start": status.get("start"),
            "end": status.get("end"),
            "reps": reps,
            "ms_per_iter_values": timings,
            "ms_per_iter_median": statistics.median(timings),
            "ms_per_iter_min": min(timings),
            "ms_per_iter_max": max(timings),
            "num_nodes": items[0]["row"].get("num_nodes"),
            "num_exec_blocks": items[0]["row"].get("num_exec_blocks"),
            "cuda_graph_active": items[0]["row"].get("cuda_graph_active"),
            "peak_memory_allocated_bytes": probe.get("peak_memory_allocated_bytes"),
            "peak_memory_allocated_mib": mib(probe["peak_memory_allocated_bytes"]) if probe.get("peak_memory_allocated_bytes") is not None else None,
            "peak_memory_reserved_bytes": probe.get("peak_memory_reserved_bytes"),
            "peak_memory_reserved_mib": mib(probe["peak_memory_reserved_bytes"]) if probe.get("peak_memory_reserved_bytes") is not None else None,
        })

    if args.output_json:
        Path(args.output_json).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if args.output_csv:
        fields = [
            "gpu", "game", "mode", "status", "ms_per_iter_median", "ms_per_iter_min", "ms_per_iter_max",
            "peak_memory_allocated_mib", "peak_memory_reserved_mib", "num_nodes", "num_exec_blocks",
        ]
        lines = [",".join(fields)]
        for row in summary:
            lines.append(",".join(str(row.get(field, "")) for field in fields))
        Path(args.output_csv).write_text("\n".join(lines) + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
