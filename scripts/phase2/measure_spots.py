"""Phase 2B measurements: build each spot through the worker on both GPUs.

Run from .venv-api:  .venv-api/bin/python scripts/phase2/measure_spots.py
Writes scripts/phase2/logs/spot_measurements.{json,md}. Every number is
measured in this run; nothing is carried over.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from holdem_solver.client import EngineClient, SpotConfig  # noqa: E402

MIB = 1024 * 1024
RIVER = "KsJsTh7d2c"
TURN = "KsJsTh7d"
BROAD = "22+, A2s+, K9s+, Q9s+, J9s+, T9s, 98s, ATo+, KTo+, QJo"
ITERATIONS = 200

SPOTS = [
    ("river-toy", SpotConfig(RIVER, "AhQh, 4c3c, 4d3d, 4h3h", "9d9h", pot=10, stack=10, bet_sizes="pot")),
    ("river-narrow", SpotConfig(RIVER, "QQ+, AK", "TT+, AQ", bet_sizes="standard")),
    ("river-broad-standard", SpotConfig(RIVER, BROAD, BROAD, bet_sizes="standard")),
    ("river-broad-wide-allin", SpotConfig(RIVER, BROAD, BROAD, bet_sizes="wide", all_in=True)),
    ("river-full-pot", SpotConfig(RIVER, "*", "22+, A2+, K2+, Q2+", bet_sizes="half", max_nodes=None)),
    ("turn-toy", SpotConfig("AsKs7s2s", "Qs3h, 5c4c, 5d4d, 5h4h", "JsTs", pot=10, stack=40, bet_sizes="pot")),
    ("turn-narrow-standard", SpotConfig(TURN, "QQ+, AKs", "TT+, AQs", bet_sizes="standard")),
    ("turn-pairs-standard", SpotConfig(TURN, "TT+, AK", "99+, AQ", bet_sizes="standard", max_nodes=None)),
    ("turn-pairs-pot-allin", SpotConfig(TURN, "TT+, AK", "99+, AQ", bet_sizes="pot", all_in=True, max_nodes=None)),
]


def measure(gpu: int) -> list[dict]:
    rows = []
    for label, spot in SPOTS:
        # A fresh worker per spot: peak VRAM then belongs to this spot alone.
        with EngineClient(gpu) as client:
            built = client.build(spot)
            result = client.solve(iterations=ITERATIONS)
            strategy = client.get_strategy(flat=True)
        row = {
            "gpu": gpu,
            "spot": label,
            "street": built.spot["street"],
            "bet_fractions": [round(f, 4) for f in built.spot["bet_fractions"]],
            "num_hands": built.spot["num_hands"],
            "num_deals": built.spot["num_deals"],
            "num_nodes": built.num_nodes,
            "num_infosets": built.num_infosets,
            "build_seconds": built.build_seconds,
            "estimated_vram_mib": built.estimated_vram_bytes / MIB,
            "peak_reserved_after_build_mib": built.vram["peak_reserved_bytes"] / MIB,
            "peak_allocated_mib": result.vram["peak_allocated_bytes"] / MIB,
            "peak_reserved_mib": result.vram["peak_reserved_bytes"] / MIB,
            "iterations": result.iterations_run,
            "iterations_per_second": result.iterations_run / result.elapsed_seconds,
            "exploitability": result.exploitability,
            "strategy_values": int(strategy.values.size),
        }
        rows.append(row)
        print(json.dumps(row), flush=True)
    return rows


def main() -> int:
    rows = [row for gpu in (0, 1) for row in measure(gpu)]
    out = ROOT / "scripts" / "phase2" / "logs"
    (out / "spot_measurements.json").write_text(json.dumps(rows, indent=1) + "\n")
    lines = [
        "| Spot | GPU | Hands (p0 x p1) | Deals | Nodes | Infosets | Build s | Est. VRAM MiB | Peak reserved MiB | Peak allocated MiB | Est. / peak | it/s |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in sorted(rows, key=lambda r: ([s for s, _ in SPOTS].index(r["spot"]), r["gpu"])):
        lines.append(
            "| %s | %d | %d x %d | %d | %d | %d | %.1f | %.1f | %.1f | %.1f | %.2f | %.0f |"
            % (
                row["spot"],
                row["gpu"],
                row["num_hands"][0],
                row["num_hands"][1],
                row["num_deals"],
                row["num_nodes"],
                row["num_infosets"],
                row["build_seconds"],
                row["estimated_vram_mib"],
                row["peak_reserved_mib"],
                row["peak_allocated_mib"],
                row["estimated_vram_mib"] / row["peak_reserved_mib"],
                row["iterations_per_second"],
            )
        )
    (out / "spot_measurements.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
