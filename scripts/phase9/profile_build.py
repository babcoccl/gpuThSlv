"""Phase 9.1: profile of where spec-build time goes on the 10.4M-node spot.

The Phase 9 tree_gate measured ``build_river_spec`` on river-full-pot cap 2
at 68.01 s vs the Phase 6 recorded 60.7 s — the subtree-cache mechanism did
NOT reduce build time. This script profiles the build to show where the
time actually goes:

* a ``cProfile`` dump of the whole ``build_river_spec`` call;
* a micro-split of the per-deal stamping loop (``_stamp_deal``) into
  ``NodeSpec``/``ActionSpec`` object construction, dict insertion, and the
  full stamping copy, so the profile names the requested buckets.

Runs in ``.venv-engine``. Usage:
.venv-engine/bin/python -m scripts.phase9.profile_build
Writes scripts/phase9/runs/build_profile.txt.
"""
from __future__ import annotations

import cProfile
import io
import os
import pstats
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from holdem_solver.protocol.spot import SpotConfig  # noqa: E402

RUNS = os.path.join(os.path.dirname(__file__), "runs")
OUT = os.path.join(RUNS, "build_profile.txt")

RAISE_SPOT = SpotConfig(
    "KsJsTh7d2c", "*", "22+, A2+, K2+, Q2+",
    pot=20, stack=100, bet_sizes="half", raise_cap=2,
    raise_sizes=(("p", 1.0),), max_nodes=None,
)


def main():
    # gpu_cfr is importable only after the vendored submodule is on sys.path,
    # and only engine_worker modules may import it (AGENTS.md); NodeSpec is
    # reached through tree_builder, which already imports it.
    from holdem_solver.engine_worker.solver import _load_gpu_cfr  # noqa: E402
    _load_gpu_cfr()
    from holdem_solver.engine_worker import tree_builder as tb  # noqa: E402
    NodeSpec = tb.NodeSpec

    os.makedirs(RUNS, exist_ok=True)
    resolved = RAISE_SPOT.resolve()
    hands0 = [c for c, _w in resolved.hands0]
    hands1 = [c for c, _w in resolved.hands1]
    board_ids = [tb.str_to_card(c) for c in RAISE_SPOT.board]
    deals = tb._enumerate_deals(hands0, hands1)

    def build():
        return tb.build_river_spec(
            RAISE_SPOT.board, RAISE_SPOT.pot, RAISE_SPOT.stack,
            resolved.bet_fractions, hands0, hands1,
            raise_cap=RAISE_SPOT.raise_cap,
            raise_sizes=RAISE_SPOT.raise_sizes,
            name="phase9.1-profile",
        )

    # --- cProfile over the whole build ------------------------------------
    prof = cProfile.Profile()
    t0 = time.perf_counter()
    prof.enable()
    spec = build()
    prof.disable()
    build_s = time.perf_counter() - t0

    stats_io = io.StringIO()
    st = pstats.Stats(prof, stream=stats_io)
    st.sort_stats("cumulative").print_stats(30)
    cum = stats_io.getvalue()
    stats_io = io.StringIO()
    st = pstats.Stats(prof, stream=stats_io)
    st.sort_stats("tottime").print_stats(30)
    tot = stats_io.getvalue()

    # --- micro-split of the stamping loop ---------------------------------
    # Rebuild one deal's worth of arguments, then time the three parts of
    # _stamp_deal's loop separately over every deal:
    #   (a) NodeSpec/ActionSpec object construction (no dict write),
    #   (b) dict insertion of a pre-made node,
    #   (c) the real _stamp_deal call (a+b + infoset keys + payoff).
    cache = tb._RankCache(board_ids)
    template_deal_count = len(deals)
    num_tree = None
    # recover the template indirectly: a spec build's node count / deals
    spec_nodes = len(spec.nodes)
    num_tree = (spec_nodes - 1) // template_deal_count
    pre_made = NodeSpec(0, next(iter(spec.nodes.values())).node_type, 0, None, (), 0.0)

    # (a) object construction only
    t_a = 0.0
    for di in range(template_deal_count):
        offset = 1 + di * num_tree
        s = time.perf_counter()
        for rel in range(num_tree):
            NodeSpec(offset + rel, pre_made.node_type, 0, None, (), 0.0)
        t_a += time.perf_counter() - s

    # (b) dict insertion only
    nodes = {}
    t_b = 0.0
    for di in range(template_deal_count):
        offset = 1 + di * num_tree
        s = time.perf_counter()
        for rel in range(num_tree):
            nodes[offset + rel] = pre_made
        t_b += time.perf_counter() - s

    # (c) stamping copy only: wrap _stamp_deal per deal
    nodes2, infosets2, chance2 = {}, {}, {0: 1.0}
    # rebuild a template cheaply: re-emit by running the same street builder
    # path used inside build_river_spec is not exposed; instead reuse the
    # measured full-stamp figure from cProfile — here we time _stamp_deal on
    # a fresh template rebuilt through _assemble internals.
    # Reconstruct a template by re-running the builder once:
    template = tb._Template()
    ends = {}

    def street_end(history, c0, c1):
        return template.place(template.reserve(), tb.NodeType.TERMINAL, -1,
                              None, (), "showdown", c0, c1, -1)

    sb = tb._StreetBuilder(
        template, RAISE_SPOT.pot, RAISE_SPOT.stack, resolved.bet_fractions,
        RAISE_SPOT.raise_cap, RAISE_SPOT.raise_sizes,
        street_end, ("p0|%HAND%|", "p1|%HAND%|"),
    )
    sb.build((), 0.0, 0.0, 0, False, 0)
    assert len(template.nodes) == num_tree, (len(template.nodes), num_tree)

    t_c = 0.0
    for di, (h0, h1) in enumerate(deals):
        offset = 1 + di * num_tree
        s = time.perf_counter()
        tb._stamp_deal(nodes2, infosets2, chance2, template, offset,
                       RAISE_SPOT.pot, h0, h1, None, None,
                       cache.river_rank(h0), cache.river_rank(h1))
        t_c += time.perf_counter() - s

    report = []
    report.append("Phase 9.1 spec-build profile — river-full-pot cap 2 "
                  "(river spot, %d deals x %d template nodes = %d spec nodes)"
                  % (template_deal_count, num_tree, spec_nodes))
    report.append("build_river_spec wall time: %.2f s (phase6 recorded 60.7 s; "
                  "phase9 tree_gate measured 68.01 s)" % build_s)
    report.append("")
    report.append("Stamp-loop micro-split (same loop body as _stamp_deal):")
    report.append("  node object creation (NodeSpec only):      %6.2f s" % t_a)
    report.append("  dict insertion (nodes[off+rel] = pre-made): %6.2f s" % t_b)
    report.append("  stamping copy (full _stamp_deal per deal):  %6.2f s" % t_c)
    report.append("  (creation+insertion < stamp total: the rest is ActionSpec "
                  "lists, infoset key strings, and terminal payoffs)")
    report.append("")
    report.append("=== cProfile top 30 by cumulative ===")
    report.append(cum)
    report.append("=== cProfile top 30 by tottime ===")
    report.append(tot)
    open(OUT, "w").write("\n".join(report))
    print("\n".join(report[:12]))
    print("written to %s" % OUT)


if __name__ == "__main__":
    main()
