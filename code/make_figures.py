#!/usr/bin/env python3
"""
make_figures.py — Paper 1, figure generation.

Reads the result CSVs produced by steps 3 and 4 and renders the paper's
data figures (Figures 6-7) as publication-quality PNGs into figures/.

  Figure 6  the blind spot, closed: edit-trace vs provenance detection rate
            on each corpus arm (the re-authored arm is the story).
  Figure 7  provenance score distribution by arm: genuine (0), in-place (3),
            re-authored (5) — perfect separation, no overlap.

SETUP (one new library):
    source ~/research_venv/bin/activate
    pip install matplotlib

RUN (from the Research folder, after steps 3 and 4):
    cd ~/Desktop/Research
    python3 code/make_figures.py

OUTPUT: figures/fig6_blindspot_closed.png, figures/fig7_score_separation.png
"""

import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")            # no display needed
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
FIGS = ROOT / "figures"

ARM_LABEL = {"genuine": "genuine\n(n=3000)",
             "forged_inplace": "in-place\n(shape 1, n=75)",
             "forged_reauthored": "re-authored\n(shape 2, n=75)"}
ARM_ORDER = ["genuine", "forged_inplace", "forged_reauthored"]


def _rate_map(path, arm_key="arm", rate_key="flag_rate_%"):
    out = {}
    for r in csv.DictReader(open(path)):
        out[r[arm_key]] = float(r[rate_key])
    return out


def figure6():
    t1 = _rate_map(RESULTS / "table1_edittrace.csv")
    t2 = _rate_map(RESULTS / "table2_provenance.csv")
    xs = list(range(len(ARM_ORDER)))
    w = 0.38
    et = [t1.get(a, 0) for a in ARM_ORDER]
    pv = [t2.get(a, 0) for a in ARM_ORDER]

    fig, ax = plt.subplots(figsize=(8, 5))
    b1 = ax.bar([x - w/2 for x in xs], et, w, label="edit-trace forensics",
                color="#c0392b")
    b2 = ax.bar([x + w/2 for x in xs], pv, w, label="provenance coherence",
                color="#27ae60")
    ax.set_xticks(xs)
    ax.set_xticklabels([ARM_LABEL[a] for a in ARM_ORDER])
    ax.set_ylabel("flagged (%)")
    ax.set_ylim(0, 108)
    ax.set_title("Figure 6.  The blind spot, closed\n"
                 "re-authored fraud: 0% by edit-trace, 100% by provenance;\n"
                 "genuine stays at 0% (no false positives)", fontsize=11)
    ax.legend(loc="center left")
    for bars in (b1, b2):
        for b in bars:
            ax.annotate(f"{b.get_height():.0f}%",
                        (b.get_x() + b.get_width()/2, b.get_height() + 1.5),
                        ha="center", fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    out = FIGS / "fig6_blindspot_closed.png"
    fig.savefig(out, dpi=200)
    print(f"wrote {out}")


def figure7():
    rows = list(csv.DictReader(open(RESULTS / "provenance_per_file.csv")))
    scores = {a: [int(r["score"]) for r in rows if r["arm"] == a] for a in ARM_ORDER}
    colors = {"genuine": "#27ae60", "forged_inplace": "#f39c12",
              "forged_reauthored": "#c0392b"}

    fig, ax = plt.subplots(figsize=(8, 5))
    bins = [-0.5 + i for i in range(7)]     # 0..5 integer bins
    for a in ARM_ORDER:
        ax.hist(scores[a], bins=bins, alpha=0.75, color=colors[a],
                label=ARM_LABEL[a].replace("\n", " "))
    ax.axvline(2.5, color="#555", ls="--", lw=1)
    ax.annotate("flag threshold (score ≥ 3)", (2.6, ax.get_ylim()[1]*0.5),
                fontsize=9, color="#555")
    ax.set_xlabel("provenance coherence score")
    ax.set_ylabel("documents")
    ax.set_xticks([0, 1, 2, 3, 4, 5])
    ax.set_title("Figure 7.  Provenance score separates the arms with no overlap\n"
                 "genuine = 0 · in-place = 3 (review) · re-authored = 5 (conviction)")
    ax.legend()
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_yscale("log")            # 3000 vs 75 — log keeps both visible
    fig.tight_layout()
    out = FIGS / "fig7_score_separation.png"
    fig.savefig(out, dpi=200)
    print(f"wrote {out}")


def main():
    FIGS.mkdir(exist_ok=True)
    need = ["table1_edittrace.csv", "table2_provenance.csv", "provenance_per_file.csv"]
    missing = [n for n in need if not (RESULTS / n).exists()]
    if missing:
        raise SystemExit(f"Missing result files: {missing}\nRun steps 3 and 4 first.")
    figure6()
    figure7()
    print(f"\nFigures written to {FIGS}")


if __name__ == "__main__":
    main()
