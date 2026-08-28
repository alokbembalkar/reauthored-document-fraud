#!/usr/bin/env python3
"""
Step 12 — re-score the wild corpus under alternative producer-token policies.

The pilot (step 11) put every one of its 18 score>=3 flags on an Adobe
production tool: Acrobat Distiller, Acrobat PDFWriter, Adobe Acrobat Paper
Capture. None of those is a desktop authoring tool. They are PostScript
converters, print drivers and scanner OCR — exactly the server-side rendering
class §7.2 names as the leading false-positive risk.

The token list in step 4 was inherited from the forger's own tool list in
step 2, so it was never tested against producers the forger does not write.
`acrobat` is the token doing the damage: it was meant to catch "Adobe Acrobat
Pro DC" (a desktop editor) and instead catches Distiller (a pipeline).

This script re-scores from wild_features.csv alone. No PDF is re-read, so a
policy can be tested in seconds. Signals B is untouched by token choice; only
A and C move, and score = 2*A + 2*B + C as in step 4.

Usage:  python3 rescore_tokens_step12.py
"""
import csv
import io
import math
from pathlib import Path

RESULTS = Path.home() / "Desktop" / "Research" / "results"
FEATURES = RESULTS / "wild_features.csv"
OUT = RESULTS / "wild_rescored_policies.csv"

# --- policies ---------------------------------------------------------------
# P0 is the list as published in step 4, verbatim.
P0 = ["microsoft", "word", "libreoffice", "canva", "skia", "chromium",
      "ilovepdf", "print to pdf", "pdf-xchange", "acrobat", "foxit",
      "nitro", "photoshop", "google docs", "wps", "openoffice"]

# Producers that convert, print or scan rather than author. Checked first and
# absolutely: a hit here means "not desktop", whatever else the string says.
CONVERTERS = ["distiller", "pdfwriter", "paper capture", "pdf library",
              "ghostscript", "corel pdf engine", "oracle pdf", "etymon",
              "quartz pdfcontext", "pdfcreator", "fop", "itext", "jasper",
              "bi publisher", "crystal reports", "docutech", "xerox"]

# Tools a human sits in front of and types a document into.
AUTHORING = ["microsoft word", "microsoft® word", "libreoffice", "openoffice",
             "wps", "google docs", "canva", "skia", "chromium", "photoshop",
             "pages", "indesign", "publisher", "quarkxpress", "print to pdf"]

# Desktop tools that open an existing PDF and modify it. Relevant to shape 1,
# not shape 2, and the edit-trace layer already covers shape 1.
EDITORS = ["pdf-xchange", "foxit", "nitro", "acrobat pro", "acrobat dc",
           "ilovepdf", "smallpdf", "sejda"]

POLICIES = {
    "P0_published":        lambda p: any(t in p for t in P0),
    "P1_converter_excl":   lambda p: (not any(c in p for c in CONVERTERS)
                                      and any(t in p for t in P0)),
    "P2_authoring_only":   lambda p: (not any(c in p for c in CONVERTERS)
                                      and any(t in p for t in AUTHORING)),
    "P3_author_or_editor": lambda p: (not any(c in p for c in CONVERTERS)
                                      and any(t in p for t in AUTHORING + EDITORS)),
}

# What the synthetic forgers actually write, from step 2 and step 6. Used to
# price the sensitivity cost of narrowing the list.
REAUTHORED_PRODUCERS = ["Microsoft® Word for Microsoft 365", "LibreOffice 7.5",
                        "Canva", "Skia/PDF m120", "iLovePDF",
                        "Microsoft: Print To PDF"]
INPLACE_PRODUCERS = ["PDF-XChange Editor", "iLovePDF", "Foxit PhantomPDF",
                     "Adobe Acrobat Pro DC", "Nitro PDF"]


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (100 * max(0.0, (c - h) / d), 100 * min(1.0, (c + h) / d))


def load():
    raw = FEATURES.read_text(errors="replace").replace("\x00", "")
    return [r for r in csv.DictReader(io.StringIO(raw)) if r["status"] == "ok"]


def rescore(rows, is_desktop):
    """Recompute A, C and score. B is independent of the token policy."""
    out = []
    for r in rows:
        prod = (r["producer"] + " " + r["creator"]).lower()
        desktop = is_desktop(prod)
        inst = r["institutional"] == "True"
        unsigned = r["unsigned"] == "True"
        a = 1 if (inst and desktop) else 0
        b = 1 if r["sig_B"] not in ("0", "") else 0   # column stores points, not a flag
        c = 1 if (inst and unsigned and desktop) else 0
        out.append({"file": r["file"], "producer": r["producer"],
                    "institutional": inst, "desktop": desktop,
                    "sig_A": a, "sig_B": b, "sig_C": c,
                    "score": 2 * a + 2 * b + c})
    return out


def main():
    rows = load()
    n = len(rows)
    n_inst = sum(1 for r in rows if r["institutional"] == "True")
    print(f"wild corpus: {n} parsed documents, {n_inst} with an institutional cue\n")

    hdr = f"{'policy':22s} {'desktop%':>9s} {'score>=3':>18s} {'score>=2':>18s} {'>=3 | inst':>12s}"
    print(hdr)
    print("-" * len(hdr))

    all_rows = {}
    for name, fn in POLICIES.items():
        sc = rescore(rows, fn)
        all_rows[name] = sc
        d = sum(1 for r in sc if r["desktop"])
        k3 = sum(1 for r in sc if r["score"] >= 3)
        k2 = sum(1 for r in sc if r["score"] >= 2)
        ki = sum(1 for r in sc if r["score"] >= 3 and r["institutional"])
        lo3, hi3 = wilson(k3, n)
        lo2, hi2 = wilson(k2, n)
        print(f"{name:22s} {100*d/n:8.1f}% "
              f"{k3:4d}/{n} {100*k3/n:5.2f}% "
              f"{k2:5d}/{n} {100*k2/n:5.2f}% "
              f"{ki:6d}/{n_inst}")
        print(f"{'':22s} {'':9s}   [{lo3:.2f}, {hi3:.2f}]%    [{lo2:.2f}, {hi2:.2f}]%")

    print("\nsensitivity cost on the synthetic forged arms")
    print("(does the policy still call the forger's own tools 'desktop'?)")
    for name, fn in POLICIES.items():
        ra = sum(1 for p in REAUTHORED_PRODUCERS if fn(p.lower()))
        ip = sum(1 for p in INPLACE_PRODUCERS if fn(p.lower()))
        print(f"  {name:22s} re-authored {ra}/{len(REAUTHORED_PRODUCERS)} tools  "
              f"| in-place {ip}/{len(INPLACE_PRODUCERS)} tools")

    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "producer", "institutional"]
                   + [f"score_{p}" for p in POLICIES])
        base = all_rows["P0_published"]
        for i, r in enumerate(base):
            w.writerow([r["file"], r["producer"], r["institutional"]]
                       + [all_rows[p][i]["score"] for p in POLICIES])
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
