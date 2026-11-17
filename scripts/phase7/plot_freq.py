"""Render frequency-difference plots from runs/suite.json into runs/plots/.

Run with .venv-api (matplotlib lives there; the script only reads JSON).
"""
from __future__ import annotations

import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

RUNS = os.path.join(os.path.dirname(__file__), "runs")
PLOTS = os.path.join(RUNS, "plots")


def main():
    os.makedirs(PLOTS, exist_ok=True)
    data = json.load(open(os.path.join(RUNS, "suite.json")))
    names = [n for n in data if "freq" in data[n]]

    # 1) per-spot mean / max abs frequency difference
    fig, ax = plt.subplots(figsize=(11, 4.5))
    means = [data[n]["freq"]["mean_abs_diff"] * 100 for n in names]
    maxs = [data[n]["freq"]["max_abs_diff"] * 100 for n in names]
    x = range(len(names))
    ax.bar([i - 0.2 for i in x], means, width=0.4, label="mean |Δfreq| (pp)")
    ax.bar([i + 0.2 for i in x], maxs, width=0.4, label="max |Δfreq| (pp)")
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, rotation=45, ha="right", fontsize=8)
    ax.axhline(5.0, color="gray", linestyle="--", linewidth=1, label="mean tol (5pp)")
    ax.axhline(40.0, color="red", linestyle="--", linewidth=1, label="max review (40pp)")
    ax.set_ylabel("percentage points")
    ax.set_title("Per-spot action-frequency differences vs TexasSolver")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "freq_diff_by_spot.png"), dpi=140)
    plt.close(fig)

    # 2) worst-node breakdown for the two most divergent spots
    for name in sorted(names, key=lambda n: -data[n]["freq"]["mean_abs_diff"])[:2]:
        nodes = [p for p in data[name]["per_node"] if "issue" not in p]
        nodes.sort(key=lambda p: -p["mean_abs_diff"])
        top = nodes[:20]
        fig, ax = plt.subplots(figsize=(11, 4))
        labels = ["|".join([str(p["node"][0])] + p["node"][1]) or "root"
                  for p in top]
        vals = [p["mean_abs_diff"] * 100 for p in top]
        mx = [p["max_abs_diff"] * 100 for p in top]
        x = range(len(top))
        ax.bar([i - 0.2 for i in x], vals, width=0.4, label="mean |Δ| (pp)")
        ax.bar([i + 0.2 for i in x], mx, width=0.4, label="max |Δ| (pp)")
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=7)
        ax.set_ylabel("percentage points")
        ax.set_title("%s — top nodes by mean frequency diff" % name)
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(os.path.join(PLOTS, "freq_diff_%s.png" % name), dpi=140)
        plt.close(fig)
    print("wrote plots to", PLOTS)


if __name__ == "__main__":
    main()
