#!/usr/bin/env python3
"""
edittrace_checks_step3.py — Paper 1, STEP 3 of the pipeline.

Runs a clean-room EDIT-TRACE forensic battery over all three arms of the
corpus and produces TABLE 1 of the paper: the blind spot, measured.

WHAT "EDIT-TRACE FORENSICS" MEANS HERE
    Edit-trace forensics answer one question: *was this file modified after
    it was created?* They look for the residue of an editing operation on an
    otherwise finished file. This battery implements the two classic signals
    that need nothing but the PDF itself:

      1. n_revisions          — how many times the file was saved. An
                                incremental edit appends a new revision, so
                                a value > 1 means the file was re-saved after
                                its first write. (Counted as %%EOF markers.)
      2. modified_after_creation — the PDF's ModDate is later than its
                                CreationDate: the file was touched after it
                                was made.

    A file is FLAGGED by the battery if either signal fires.

WHAT THIS BATTERY DELIBERATELY DOES *NOT* CHECK
    It does NOT ask "is the producer a desktop authoring tool?" — that is an
    authorship/provenance question, handled by STEP 4. Keeping it out here is
    the point of the paper: edit-trace forensics have no notion of "born in
    the wrong kind of tool", only of "edited after birth". That is precisely
    why they are blind to a re-authored document, which was never edited.

EXPECTED RESULT (the paper's argument, now measured on releasable data)
    genuine            -> ~0% flagged   (the false-positive floor)
    forged_inplace     -> ~100% flagged (a real edit leaves a real trace)
    forged_reauthored  -> ~0% flagged   (THE BLIND SPOT: nothing to find)

    Step 4 will then take that ~0% on the re-authored arm up to ~100% with
    provenance coherence — the two tables together are the whole story.

Font inventory is reported as an informational column (distinct embedded
fonts per file) but is NOT used as a detector here: our forgeries do not
insert mixed-font text, so on this corpus the font signal fires on nothing —
we report it honestly rather than pretend it contributes.

--------------------------------------------------------------------------
RUN (venv active, from the Research folder):

    source ~/research_venv/bin/activate
    cd ~/Desktop/Research
    python3 code/edittrace_checks_step3.py

OUTPUT
    results/edittrace_per_file.csv   one row per document (all arms)
    results/table1_edittrace.csv     the summary — detection rate per arm
    (also printed to the screen)

WHAT TO TEST
    1. The printed Table 1 shows genuine ~0%, inplace high, reauthored ~0%.
       That pattern IS the paper's negative result.
    2. Open results/edittrace_per_file.csv: reauthored rows have
       n_revisions=1 and modified_after_creation=False (nothing to catch);
       inplace rows have modified_after_creation=True.
    3. Re-run -> identical numbers (deterministic).
--------------------------------------------------------------------------
"""

import csv
from pathlib import Path

from pypdf import PdfReader

RESEARCH_ROOT = Path(__file__).resolve().parent.parent
CORPUS = RESEARCH_ROOT / "corpus"
RESULTS = RESEARCH_ROOT / "results"

# (arm label, folder under corpus/, ground-truth: is this arm a forgery?)
ARMS = [
    ("genuine",            "genuine",            False),
    ("forged_inplace",     "forged_inplace",     True),
    ("forged_reauthored",  "forged_reauthored",  True),
]
SUBFOLDERS = ["invoices", "letters", "medicals"]


def count_revisions(pdf_bytes: bytes) -> int:
    """Number of saved revisions ~= number of %%EOF markers in the file.
    A single clean save has one; each incremental edit appends another."""
    n = pdf_bytes.count(b"%%EOF")
    return max(n, 1)


def count_fonts(reader: PdfReader) -> int:
    """Distinct embedded font BaseFonts across all pages (informational)."""
    fonts = set()
    for page in reader.pages:
        try:
            res = page.get("/Resources")
            if res is None:
                continue
            fdict = res.get("/Font")
            if fdict is None:
                continue
            for fref in fdict.values():
                obj = fref.get_object()
                bf = obj.get("/BaseFont")
                if bf:
                    fonts.add(str(bf))
        except Exception:
            continue
    return len(fonts)


def inspect(path: Path) -> dict:
    """Run the edit-trace battery on one PDF. Returns the signals + verdict."""
    raw = path.read_bytes()
    reader = PdfReader(path)

    n_rev = count_revisions(raw)

    created = mod = None
    try:
        created = reader.metadata.creation_date
        mod = reader.metadata.modification_date
    except Exception:
        pass
    # "modified after creation": ModDate strictly later than CreationDate.
    mod_after = bool(created and mod and mod > created)

    n_fonts = count_fonts(reader)

    flagged = (n_rev > 1) or mod_after
    return {"n_revisions": n_rev, "modified_after_creation": mod_after,
            "n_fonts": n_fonts, "flagged": flagged}


def main():
    RESULTS.mkdir(exist_ok=True)
    per_file = []

    for arm, folder, is_forgery in ARMS:
        for sub in SUBFOLDERS:
            d = CORPUS / folder / sub
            if not d.exists():
                continue
            for pdf in sorted(d.glob("*.pdf")):
                sig = inspect(pdf)
                per_file.append({
                    "arm": arm, "is_forgery": is_forgery,
                    "doc_type": sub[:-1], "file": f"{folder}/{sub}/{pdf.name}",
                    **sig,
                })

    if not per_file:
        raise SystemExit("No PDFs found. Run steps 1 and 2 first.")

    # per-file CSV
    cols = ["arm", "is_forgery", "doc_type", "file", "n_revisions",
            "modified_after_creation", "n_fonts", "flagged"]
    pf_path = RESULTS / "edittrace_per_file.csv"
    with open(pf_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader(); w.writerows(per_file)

    # summary Table 1
    summary = []
    for arm, folder, is_forgery in ARMS:
        rows = [r for r in per_file if r["arm"] == arm]
        if not rows:
            continue
        n = len(rows)
        flagged = sum(1 for r in rows if r["flagged"])
        by_rev = sum(1 for r in rows if r["n_revisions"] > 1)
        by_mod = sum(1 for r in rows if r["modified_after_creation"])
        # For forgeries, "flagged" = correct detection; for genuine, "flagged"
        # = a FALSE POSITIVE. We report the flag rate either way.
        summary.append({
            "arm": arm, "n": n,
            "flagged": flagged, "flag_rate_%": round(100 * flagged / n, 1),
            "by_revisions": by_rev, "by_modified_after_creation": by_mod,
            "interpretation": ("false positives" if not is_forgery
                               else "correct detections"),
        })

    t1_path = RESULTS / "table1_edittrace.csv"
    with open(t1_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0].keys()))
        w.writeheader(); w.writerows(summary)

    # pretty print
    print("\n" + "=" * 70)
    print("TABLE 1 — Edit-trace forensics: the blind spot, measured")
    print("=" * 70)
    print(f"{'arm':<20}{'n':>6}{'flagged':>9}{'rate':>8}   {'what it means'}")
    print("-" * 70)
    for s in summary:
        print(f"{s['arm']:<20}{s['n']:>6}{s['flagged']:>9}{s['flag_rate_%']:>7}%   {s['interpretation']}")
    print("-" * 70)
    print("Reading it: the re-authored arm should be ~0% — edit-trace forensics")
    print("cannot see a document that was never edited. Step 4 closes that gap.")
    print(f"\nPer-file: {pf_path}\nSummary:  {t1_path}")


if __name__ == "__main__":
    main()
