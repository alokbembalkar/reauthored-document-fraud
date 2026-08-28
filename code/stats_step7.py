#!/usr/bin/env python3
"""
stats_step7.py — Paper 1, STEP 7: statistical rigor for §5.

Addresses the "too clean / no CIs / no curves" review head-on. Runs the
clean-room provenance engine (same logic as step 4) over ALL corpus arms —
genuine, forged_reauthored, forged_inplace, and the graded careful-attacker
levels from step 6 — and produces:

  1. WILSON 95% confidence intervals for every detection / false-positive
     rate (a 75/75 detection is reported as 100% [95.2, 100]%, not "100%").
  2. ROC curve + AUC with BOOTSTRAP 95% CI over the continuous provenance
     score (genuine vs re-authored).
  3. The EMPIRICAL Δt DISTRIBUTION figure promised in §4.1: printed-date vs
     file-creation gap for genuine documents vs forgeries (log-scale).
  4. THRESHOLD SENSITIVITY: detection and FP rate as the date-gap cut sweeps
     5..365 days — shows the 45-day choice sits on a wide plateau, i.e. the
     result is not an artifact of the threshold.
  5. The CAREFUL-ATTACKER DEGRADATION CURVE: detection rate at attacker
     levels L0..L3 with CIs — the honest operating curve of §7.1.

Outputs:
  results/stats_summary.csv           all rates + Wilson CIs
  results/degradation_curve.csv       per-level detection + CI
  figures/fig8_roc.png                ROC + AUC (bootstrap CI)
  figures/fig9_dt_distribution.png    empirical Δt distributions
  figures/fig10_threshold_sweep.png   sensitivity of the 45-day cut
  figures/fig11_degradation.png       attacker-strength degradation curve

RUN (venv active, from the Research folder; steps 1, 2 and 6 done):
    python3 code/stats_step7.py
"""

import csv
import math
import random
import re
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
RESULTS = ROOT / "results"
FIGS = ROOT / "figures"

DATE_GAP_DEFAULT = 45
DESKTOP_TOKENS = ["microsoft", "word", "libreoffice", "canva", "skia", "chromium",
                  "ilovepdf", "print to pdf", "pdf-xchange", "acrobat", "foxit",
                  "nitro", "photoshop", "google docs", "wps", "openoffice"]
GENRE_CUES = {"invoice": ["tax invoice", "gstin", "invoice no"],
              "letter": ["relieving letter", "relieved", "employed with"],
              "medical": ["histopathology", "microscopic examination", "impression"]}


# ---------------- provenance engine (identical logic to step 4) -------------

def inspect(path: Path, gap_threshold=DATE_GAP_DEFAULT):
    raw = path.read_bytes()
    r = PdfReader(path)
    text = ""
    for pg in r.pages:
        try:
            text += (pg.extract_text() or "").lower()
        except Exception:
            pass
    prod = (r.metadata.producer or "") if r.metadata else ""
    crea = (r.metadata.creator or "") if r.metadata else ""
    created = r.metadata.creation_date if r.metadata else None

    institutional = any(c in text for cues in GENRE_CUES.values() for c in cues)
    desktop = any(t in f"{prod} {crea}".lower() for t in DESKTOP_TOKENS)
    unsigned = b"/ByteRange" not in raw

    m = re.search(r"\b(\d{2})-(\d{2})-(\d{4})\b", text)
    gap = None
    if m and created:
        try:
            printed = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            gap = abs((printed - created.replace(tzinfo=None)).days)
        except ValueError:
            pass
    score = ((2 if institutional and desktop else 0)
             + (2 if (gap is not None and gap > gap_threshold) else 0)
             + (1 if (institutional and unsigned and desktop) else 0))
    return {"score": score, "gap": gap, "flag": score >= 3,
            "institutional": institutional, "desktop": desktop,
            "unsigned": unsigned}


def score_at(d, gap_threshold):
    """Recompute the provenance score for a stored inspection under a
    different date-gap threshold (for the sensitivity sweep)."""
    return ((2 if d["institutional"] and d["desktop"] else 0)
            + (2 if (d["gap"] is not None and d["gap"] > gap_threshold) else 0)
            + (1 if (d["institutional"] and d["unsigned"] and d["desktop"]) else 0))


# ---------------- statistics helpers ---------------------------------------

def wilson(k, n, z=1.96):
    """Wilson score 95% CI for a proportion, returned as (lo, hi) in %."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z*z/n
    centre = (p + z*z/(2*n)) / denom
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denom
    return (100*max(0, centre-half), 100*min(1, centre+half))


def auc(pos, neg):
    """Mann-Whitney AUC via score histograms (scores are small ints 0..5,
    so this is O(len + 36) instead of O(len(pos)*len(neg)))."""
    from collections import Counter
    cp, cn = Counter(pos), Counter(neg)
    wins = ties = 0
    for pv, pk in cp.items():
        for nv, nk in cn.items():
            if pv > nv: wins += pk*nk
            elif pv == nv: ties += pk*nk
    return (wins + 0.5*ties) / (len(pos)*len(neg))


def bootstrap_auc(pos, neg, iters=1000, seed=42):
    rng = random.Random(seed)
    vals = []
    for _ in range(iters):
        ps = [pos[rng.randrange(len(pos))] for _ in range(len(pos))]
        ns = [neg[rng.randrange(len(neg))] for _ in range(len(neg))]
        vals.append(auc(ps, ns))
    vals.sort()
    return vals[int(0.025*iters)], vals[int(0.975*iters)]


# ---------------- main ------------------------------------------------------

def collect(folder):
    out = []
    base = CORPUS / folder
    for pdf in sorted(base.rglob("*.pdf")):
        out.append(inspect(pdf))
    return out


def main():
    RESULTS.mkdir(exist_ok=True); FIGS.mkdir(exist_ok=True)

    print("Scanning corpus arms (the genuine arm is 3,000 docs — a few minutes)...")
    arms = {"genuine": collect("genuine"),
            "forged_inplace": collect("forged_inplace"),
            "forged_reauthored": collect("forged_reauthored")}
    careful_dir = CORPUS / "forged_careful"
    levels = []
    if careful_dir.exists():
        for lvl in sorted(p.name for p in careful_dir.iterdir() if p.is_dir()):
            arms[f"careful/{lvl}"] = collect(f"forged_careful/{lvl}")
            levels.append(lvl)

    # 1. summary with Wilson CIs
    rows = []
    for name, docs in arms.items():
        n = len(docs); k = sum(d["flag"] for d in docs)
        lo, hi = wilson(k, n)
        rows.append({"arm": name, "n": n, "flagged": k,
                     "rate_%": round(100*k/n, 1) if n else 0,
                     "wilson_lo_%": round(lo, 1), "wilson_hi_%": round(hi, 1)})
        print(f"  {name:28s} {k:5d}/{n:<5d} = {100*k/max(n,1):5.1f}%  "
              f"[{lo:5.1f}, {hi:5.1f}]%")
    with open(RESULTS / "stats_summary.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    # 2. ROC + AUC (genuine vs re-authored), bootstrap CI
    pos = [d["score"] for d in arms["forged_reauthored"]]
    neg = [d["score"] for d in arms["genuine"]]
    a = auc(pos, neg); lo_a, hi_a = bootstrap_auc(pos, neg)
    ths = sorted(set(pos+neg))
    tpr = [sum(p >= t for p in pos)/len(pos) for t in ths]
    fpr = [sum(q >= t for q in neg)/len(neg) for t in ths]
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot([1]+fpr+[0], [1]+tpr+[0], marker="o", color="#205081")
    ax.plot([0, 1], [0, 1], ls="--", color="#999")
    ax.set_xlabel("false-positive rate (genuine)"); ax.set_ylabel("detection rate (re-authored)")
    ax.set_title(f"Figure 8.  ROC — provenance score\nAUC = {a:.3f} "
                 f"(bootstrap 95% CI [{lo_a:.3f}, {hi_a:.3f}])")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(FIGS / "fig8_roc.png", dpi=200)

    # 3. Δt distribution
    g_gap = [d["gap"] for d in arms["genuine"] if d["gap"] is not None]
    f_gap = [d["gap"] for d in arms["forged_reauthored"] if d["gap"] is not None]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    bins = list(range(0, 320, 10))
    ax.hist(g_gap, bins=bins, alpha=0.7, color="#27ae60", label="genuine")
    ax.hist(f_gap, bins=bins, alpha=0.7, color="#c0392b", label="re-authored")
    ax.axvline(DATE_GAP_DEFAULT, color="#555", ls="--")
    ax.annotate("45-day cut", (DATE_GAP_DEFAULT+4, ax.get_ylim()[1]*0.6), fontsize=9)
    ax.set_yscale("log"); ax.set_xlabel("|printed date − file creation| (days)")
    ax.set_ylabel("documents"); ax.legend()
    ax.set_title("Figure 9.  Empirical Δt: genuine concentrates at 0; re-authored is far out")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(FIGS / "fig9_dt_distribution.png", dpi=200)

    # 4. threshold sensitivity sweep
    sweeps = list(range(5, 370, 10))
    det, fp = [], []
    for t in sweeps:
        det.append(100*sum(score_at(d, t) >= 3 for d in arms["forged_reauthored"])/len(pos))
        fp.append(100*sum(score_at(d, t) >= 3 for d in arms["genuine"])/len(neg))
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(sweeps, det, color="#c0392b", marker=".", label="detection (re-authored)")
    ax.plot(sweeps, fp, color="#27ae60", marker=".", label="false positives (genuine)")
    ax.axvline(DATE_GAP_DEFAULT, color="#555", ls="--")
    ax.set_xlabel("date-gap threshold (days)"); ax.set_ylabel("rate (%)")
    ax.set_ylim(-3, 105); ax.legend()
    ax.set_title("Figure 10.  Threshold sensitivity — the 45-day cut sits on a wide plateau")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(FIGS / "fig10_threshold_sweep.png", dpi=200)

    # 5. degradation over attacker spoofs — ordered by ATTACKER EFFORT, and
    #    at BOTH operating points (deployed >=3 and sensitive >=2).
    #    KEY FINDING this exposes: producer-spoofing ALONE (the cheapest
    #    spoof) defeats the >=3 compound, because date-gap evidence (+2)
    #    sits below the flag bar. The >=2 point closes that hole at zero
    #    measured FP cost on this corpus (real-world cost: the server-side
    #    rendering-library class, manuscript section 7.2).
    ABL_ORDER = [("L0_naive", "no spoof\n(naive)"),
                 ("L2_date_spoof", "date\nspoofed"),
                 ("L1_producer_spoof", "producer\nspoofed"),
                 ("L3_full_coherent", "both\nspoofed")]
    present = [(k, lbl) for k, lbl in ABL_ORDER if f"careful/{k}" in arms]
    if present:
        curve_rows = []
        xs = list(range(len(present)))
        series = {3: {"ys": [], "los": [], "his": []},
                  2: {"ys": [], "los": [], "his": []}}
        for k, lbl in present:
            docs = arms[f"careful/{k}"]
            n = len(docs)
            row = {"level": k, "n": n}
            for thr in (3, 2):
                det = sum(score_at(d, DATE_GAP_DEFAULT) >= thr for d in docs)
                lo, hi = wilson(det, n)
                rate = 100*det/n
                series[thr]["ys"].append(rate)
                series[thr]["los"].append(rate-lo)
                series[thr]["his"].append(hi-rate)
                row[f"rate_thr{thr}_%"] = round(rate, 1)
                row[f"wilson_thr{thr}"] = f"[{lo:.1f},{hi:.1f}]"
            curve_rows.append(row)
        # FP cost of the sensitive operating point on genuine
        fp2 = sum(score_at(d, DATE_GAP_DEFAULT) >= 2 for d in arms["genuine"])
        with open(RESULTS / "degradation_curve.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(curve_rows[0].keys()))
            w.writeheader(); w.writerows(curve_rows)

        fig, ax = plt.subplots(figsize=(7.5, 4.8))
        ax.errorbar(xs, series[3]["ys"], yerr=[series[3]["los"], series[3]["his"]],
                    marker="o", capsize=4, lw=2, color="#8e44ad",
                    label="deployed operating point (score ≥ 3)")
        ax.errorbar(xs, series[2]["ys"], yerr=[series[2]["los"], series[2]["his"]],
                    marker="s", capsize=4, lw=2, ls="--", color="#2980b9",
                    label=f"sensitive point (score ≥ 2) · genuine FP {fp2}/{len(arms['genuine'])}")
        ax.set_xticks(xs); ax.set_xticklabels([lbl for _, lbl in present], fontsize=9)
        ax.set_ylabel("provenance detection rate (%)"); ax.set_ylim(-4, 106)
        ax.set_title("Figure 11.  Detection vs attacker spoof (Wilson 95% CIs)\n"
                     "producer-spoofing alone defeats the ≥3 compound; the ≥2 point closes it;\n"
                     "the fully-coherent attacker defeats both — the signature layer is the floor")
        ax.legend(fontsize=8, loc="center left")
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout(); fig.savefig(FIGS / "fig11_degradation.png", dpi=200)
        print(f"\nSensitive operating point (>=2): genuine FP {fp2}/{len(arms['genuine'])}")

    print(f"\nROC AUC (genuine vs re-authored): {a:.3f}  [{lo_a:.3f}, {hi_a:.3f}]")
    print(f"Wrote: results/stats_summary.csv, results/degradation_curve.csv,")
    print(f"       figures/fig8..fig11 (ROC, Δt, threshold sweep, degradation)")


if __name__ == "__main__":
    main()
