"""Record exploitability-vs-time for a full-range HUNL river solve (Phase 3).

Runs through EngineClient on one GPU and writes JSON lines to stdout:
one line per progress eval, plus a final result line.
"""

import argparse
import json
import sys

from holdem_solver.client import EngineClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--iterations", type=int, default=4000)
    parser.add_argument("--progress-every", type=int, default=250)
    args = parser.parse_args()

    spot = {
        "board": "KsJsTh7d2c",
        "range0": "*",
        "range1": "*",
        "pot": 20.0,
        "stack": 100.0,
        "bet_sizes": "pot",
        "all_in": False,
        "dead_cards": [],
        "name": "river-full-pot",
        "max_nodes": None,
    }
    with EngineClient(args.gpu) as client:
        built = client.build(spot)
        print(json.dumps({"event": "built", "nodes": built.num_nodes, "infosets": built.num_infosets,
                          "estimated_vram_bytes": built.estimated_vram_bytes,
                          "build_seconds": built.build_seconds}), flush=True)
        result = client.solve(
            iterations=args.iterations,
            progress_every=args.progress_every,
            on_progress=lambda p: print(
                json.dumps(
                    {
                        "event": "progress",
                        "iteration": p.iteration,
                        "iterations_run": p.iterations_run,
                        "elapsed_seconds": p.elapsed_seconds,
                        "exploitability": p.exploitability,
                    }
                ),
                flush=True,
            ),
        )
        print(json.dumps({"event": "done", **{f.name: getattr(result, f.name) for f in result.__dataclass_fields__.values() if f.name != "vram"}, "vram": result.vram}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
