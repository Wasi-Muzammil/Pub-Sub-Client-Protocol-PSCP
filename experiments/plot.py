"""results/raw/*.csv -> mean ± std table (stdout, Markdown) + one error-bar chart per experiment/unit.

Each run is first reduced to its mean; the reported mean and spread are across runs.
    python experiments/plot.py [--raw results/raw] [--out report/figures]
"""
import argparse
import csv
import glob
import os
import statistics
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

QOS_COLORS = {"0": "#2a78d6", "1": "#eb6834"}  # categorical slots 1-2, fixed order
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def load(raw_dir):
    groups = defaultdict(lambda: defaultdict(list))  # (exp, unit) -> (cond, qos, run) -> values
    for path in glob.glob(os.path.join(raw_dir, "*.csv")):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                groups[(r["experiment"], r["unit"])][(r["condition"], r["qos"], r["run"])].append(float(r["value"]))
    return groups


def summarise(runs):
    """(cond, qos) -> (mean, std, n_runs) over per-run means."""
    per = defaultdict(list)
    for (cond, qos, _run), vals in runs.items():
        per[(cond, qos)].append(statistics.fmean(vals))
    return {k: (statistics.fmean(v), statistics.stdev(v) if len(v) > 1 else 0.0, len(v))
            for k, v in sorted(per.items())}


def plot(exp, unit, stats, out_dir):
    conds = sorted({c for c, _ in stats})
    qoses = sorted({q for _, q in stats})
    width = 0.6 / len(qoses)
    fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=200)
    ax.set_xlim(-0.6 if len(conds) > 1 else -1, len(conds) - (0.4 if len(conds) > 1 else 0))
    for i, q in enumerate(qoses):
        xs, ys, es = [], [], []
        for j, c in enumerate(conds):
            if (c, q) in stats:
                m, s, _ = stats[(c, q)]
                xs.append(j + (i - (len(qoses) - 1) / 2) * width)
                ys.append(m)
                es.append(s)
        ax.bar(xs, ys, width * 0.92, yerr=es, color=QOS_COLORS.get(q, "#6250d6"), label=f"QoS {q}",
               error_kw={"ecolor": INK, "elinewidth": 1, "capsize": 3}, zorder=2)
        for x, y, e in zip(xs, ys, es):
            ax.annotate(f"{y:.3g}", (x, y + e), textcoords="offset points", xytext=(0, 3),
                        ha="center", fontsize=7, color=MUTED)
    ax.set_xticks(range(len(conds)), conds, fontsize=8, color=INK)
    ax.set_ylabel(unit, color=MUTED)
    ax.set_title(f"{exp} (mean ± std over runs)", fontsize=10, color=INK, loc="left")
    ax.grid(axis="y", color=GRID, linewidth=0.6, zorder=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=8)
    if len(qoses) > 1:
        ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    safe = unit.replace("/", "per").replace("%", "pct").replace(" ", "_")
    path = os.path.join(out_dir, f"{exp}_{safe}.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def main():
    root = os.path.join(os.path.dirname(__file__), "..")
    p = argparse.ArgumentParser()
    p.add_argument("--raw", default=os.path.join(root, "results", "raw"))
    p.add_argument("--out", default=os.path.join(root, "report", "figures"))
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for (exp, unit), runs in sorted(load(a.raw).items()):
        stats = summarise(runs)
        print(f"\n### {exp} ({unit})\n\n| condition | QoS | runs | mean | std |\n|---|---|---|---|---|")
        for (cond, qos), (m, s, n) in stats.items():
            print(f"| {cond} | {qos} | {n} | {m:.3f} | {s:.3f} |")
        print(f"\nfigure: {plot(exp, unit, stats, a.out)}")


if __name__ == "__main__":
    main()
