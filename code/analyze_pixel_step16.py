#!/usr/bin/env python3
"""
Step 16 — turn the four-arm V5 scores into the numbers §3.2 needs.

Arms (built by step 14, scored by step 15):
    A original     genuine scan, untouched
    B edited       localized tamper, residue present
    C reauthored   re-render of the tampered page
    D reauth_ctrl  re-render of the genuine page

Two comparisons carry the section, and the order matters:

  B vs A  positive control. If the model cannot rank a residue-bearing tamper
          above its own untouched original, it has no demonstrated power on
          this corpus and every other AUC here is uninterpretable as evidence
          about tampering. This is reported FIRST and gates the rest.

  C vs D  the claim. Same fraudulent content, residue destroyed by re-rendering,
          against a genuine page put through the identical renderer. Only
          meaningful if B-vs-A passes.

C vs A is deliberately NOT the headline: it conflates falsification with
re-rendering, and D exists precisely to separate them.

Usage:
    python3 analyze_pixel_step16.py
    python3 analyze_pixel_step16.py --thr 0.40 --boot 10000
"""
import argparse
import csv
import math
import random
from collections import defaultdict
from pathlib import Path

RES = Path.home() / "Desktop" / "Research" / "results"
SCORES = RES / "pixel_scores_v5.csv"
MANIFEST = RES / "pixel_corpus_manifest.csv"
OUT = RES / "pixel_summary_v5.csv"
ARMS = ["original", "edited", "reauthored", "reauth_ctrl"]
LABEL = {"original": "A original", "edited": "B edited",
         "reauthored": "C reauthored", "reauth_ctrl": "D reauth_ctrl"}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (100 * (c - m) / d, 100 * (c + m) / d)


def auc(pos, neg):
    """Mann-Whitney U with midranks. Ties matter here: the peak score is 0.0
    for a large fraction of pages, so ignoring them would inflate the AUC."""
    a = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    r = [0.0] * len(a)
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and a[j + 1][0] == a[i][0]:
            j += 1
        for k in range(i, j + 1):
            r[k] = (i + j) / 2 + 1
        i = j + 1
    s = sum(r[k] for k in range(len(a)) if a[k][1] == 1)
    n1, n0 = len(pos), len(neg)
    return (s - n1 * (n1 + 1) / 2) / (n1 * n0)


def boot_ci(pairs, B, seed=0):
    """Paired bootstrap: resample documents, not scores. The four arms of one
    document share a source page, so resampling arms independently would
    understate the interval."""
    rng = random.Random(seed)
    n = len(pairs)
    out = []
    for _ in range(B):
        idx = [rng.randrange(n) for _ in range(n)]
        out.append(auc([pairs[i][0] for i in idx], [pairs[i][1] for i in idx]))
    out.sort()
    return out[int(0.025 * B)], out[int(0.975 * B)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thr", type=float, default=0.40, help="production operating point")
    ap.add_argument("--boot", type=int, default=5000)
    args = ap.parse_args()

    by = defaultdict(dict)
    for r in csv.DictReader(open(SCORES)):
        by[r["file"]][r["arm"]] = float(r["peak"])
    full = {k: v for k, v in by.items() if len(v) == 4}
    print(f"scored documents: {len(by)}   complete quadruples: {len(full)}")
    if len(full) < len(by):
        print(f"  ({len(by) - len(full)} partial, excluded so every arm has the same pages)")

    genre = {r["file"]: r["genre"] for r in csv.DictReader(open(MANIFEST))}

    rows = []
    print(f"\nper arm (operating point {args.thr}):")
    for a in ARMS:
        v = sorted(full[k][a] for k in full)
        n = len(v)
        k = sum(x >= args.thr for x in v)
        lo, hi = wilson(k, n)
        print(f"  {LABEL[a]:16s} median {v[n//2]:.4f}  mean {sum(v)/n:.4f}  "
              f"fires {k}/{n} = {100*k/n:5.1f}% [{lo:.1f}, {hi:.1f}]")
        rows.append(["arm", LABEL[a], n, k, f"{100*k/n:.2f}", f"{lo:.2f}", f"{hi:.2f}", ""])

    comps = [("POSITIVE CONTROL  B vs A", "edited", "original"),
             ("THE CLAIM         C vs D", "reauthored", "reauth_ctrl"),
             ("(confounded)      C vs A", "reauthored", "original")]
    print("\nAUC (paired bootstrap 95% CI):")
    for name, p, n_ in comps:
        pairs = [(full[k][p], full[k][n_]) for k in full]
        a = auc([x[0] for x in pairs], [x[1] for x in pairs])
        lo, hi = boot_ci(pairs, args.boot)
        up = sum(x[0] > x[1] for x in pairs)
        dn = sum(x[0] < x[1] for x in pairs)
        print(f"  {name}: AUC {a:.3f} [{lo:.3f}, {hi:.3f}]   "
              f"higher {up}, lower {dn}, tied {len(pairs)-up-dn}")
        rows.append(["auc", name, len(pairs), "", f"{a:.4f}", f"{lo:.4f}", f"{hi:.4f}", ""])

    print("\nper genre, B-vs-A AUC (does the control fail everywhere or only on some genres?):")
    g = defaultdict(list)
    for k in full:
        g[genre.get(k, "?")].append(k)
    for gn, keys in sorted(g.items(), key=lambda x: -len(x[1])):
        if len(keys) < 20:
            continue
        pairs = [(full[k]["edited"], full[k]["original"]) for k in keys]
        a = auc([x[0] for x in pairs], [x[1] for x in pairs])
        fa = sum(full[k]["original"] >= args.thr for k in keys)
        print(f"  {gn:20s} n={len(keys):4d}  B-vs-A AUC {a:.3f}   "
              f"arm-A false fires {fa}/{len(keys)} = {100*fa/len(keys):.1f}%")
        rows.append(["genre", gn, len(keys), fa, f"{a:.4f}", "", "", ""])

    # The obvious objection to a dead positive control is that the tampers are
    # too small to see. Bin by tamper area and report the control within each
    # bin: if the model had any sensitivity, the largest bin would show it.
    print("\nB-vs-A AUC by tamper area (is the control failing only on small edits?):")
    tam = {r["file"]: float(r["tamper_px_pct"])
           for r in csv.DictReader(open(MANIFEST)) if r["status"] == "ok"}
    have = [k for k in full if k in tam]
    have.sort(key=lambda k: tam[k])
    q = len(have) // 4
    bins = [("Q1 smallest", have[:q]), ("Q2", have[q:2*q]),
            ("Q3", have[2*q:3*q]), ("Q4 largest", have[3*q:])]
    for name, keys in bins:
        if not keys:
            continue
        pairs = [(full[k]["edited"], full[k]["original"]) for k in keys]
        a = auc([x[0] for x in pairs], [x[1] for x in pairs])
        print(f"  {name:12s} n={len(keys):4d}  tamper {tam[keys[0]]:6.3f}-{tam[keys[-1]]:6.3f}% px   "
              f"B-vs-A AUC {a:.3f}")
        rows.append(["tamper_bin", name, len(keys), "", f"{a:.4f}",
                     f"{tam[keys[0]]:.4f}", f"{tam[keys[-1]]:.4f}", "pct px tampered"])

    with open(OUT, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["kind", "name", "n", "k", "value", "ci_lo", "ci_hi", "note"])
        w.writerows(rows)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
