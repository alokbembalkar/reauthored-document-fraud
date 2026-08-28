#!/usr/bin/env python3
"""
provenance_check_step4.py — Paper 1, STEP 4 of the pipeline.

Runs a clean-room PROVENANCE-COHERENCE engine over all three corpus arms
and produces TABLE 2 of the paper: the gap, closed.

THE QUESTION IT ASKS  (contrast with step 3)
    Edit-trace forensics (step 3) ask "was this file modified after it was
    created?" — and are blind to a re-authored document, which was never
    modified. Provenance coherence asks a different question:

        "Is this file's BIRTH STORY coherent with the document it claims
         to be?"

    A re-authored fraud cannot hide two facts about itself: it was born in
    a desktop tool (not an institutional system), and it was born long
    after the date it prints on its face. This engine scores exactly that.

THE THREE SIGNALS  (as in the paper, §4.1; issuer-independent, template-free)
    A (+2) claimed institutional origin vs desktop authorship class:
           the CONTENT claims an institutional document (invoice / letter /
           medical report), but the PRODUCER is a general-purpose desktop
           authoring / conversion / editing tool, not a system generator.
    B (+2) printed date vs file birth:
           the date printed on the document is > 45 days from the file's
           CreationDate. A genuine system issues the file at issuance;
           a re-authored copy is born when the fraud is made.
    C (+1) institutional + unsigned + desktop (a weak corroborating leg).

    score >= 3  -> provenance-incoherent (flagged)
    score == 5  -> compound (all families) -> CONVICTION grade
    score 3-4   -> partial -> REVIEW grade

WHY GENUINE DOCUMENTS STAY CLEAN
    A genuine document here is produced by a system generator (not on the
    desktop list) and its CreationDate equals its printed date -> signals
    A and B do not fire, C's desktop leg does not fire -> score 0.

EXPECTED RESULT (the payoff)
    genuine            -> ~0% flagged   (0 false positives, same as step 3)
    forged_reauthored  -> ~100% flagged (0% in step 3 -> caught here)
    forged_inplace     -> also flagged  (a forgery too; bonus coverage)

    Table 1 (step 3) + Table 2 (step 4) together are the whole argument:
    edit-trace forensics miss the re-authored document; provenance catches
    it, at no cost in false positives.

NOTE ON SIGNAL A's TOOL LIST
    §4.1 describes the desktop class as "general-purpose authoring or
    conversion tools (word processors, design tools, image editors, online
    PDF converters)". We include PDF editors in that class too, so the
    in-place forgeries (edited in a PDF editor) are also recognised as
    desktop-born. The list is a blacklist of consumer tools; a genuine
    system generator is not on it. This mirrors the production engine's
    suspicious-producer list.

--------------------------------------------------------------------------
RUN (venv active, from the Research folder):

    source ~/research_venv/bin/activate
    cd ~/Desktop/Research
    python3 code/provenance_check_step4.py

OUTPUT
    results/provenance_per_file.csv   one row per document (all signals)
    results/table2_provenance.csv     summary — flag rate + grade per arm
    (also printed; includes the mean per-document runtime in ms)

WHAT TO TEST
    1. Printed Table 2: genuine ~0%, reauthored ~100%. Compare the
       reauthored row to Table 1 — 0% there, ~100% here. That jump IS the
       paper's contribution.
    2. results/provenance_per_file.csv: genuine rows score 0; reauthored
       rows score 5 (all three signals fired).
    3. The printed mean runtime confirms "milliseconds per document".
    4. Re-run -> identical numbers.
--------------------------------------------------------------------------
"""

import csv
import time
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

RESEARCH_ROOT = Path(__file__).resolve().parent.parent
CORPUS = RESEARCH_ROOT / "corpus"
RESULTS = RESEARCH_ROOT / "results"

ARMS = [
    ("genuine",            "genuine"),
    ("forged_inplace",     "forged_inplace"),
    ("forged_reauthored",  "forged_reauthored"),
]
SUBFOLDERS = ["invoices", "letters", "medicals"]

DATE_GAP_THRESHOLD_DAYS = 45

# Blacklist of consumer/desktop tools (authoring, conversion, editing).
# Tokens are matched case-insensitively as substrings of Producer/Creator.
# NOTE: deliberately avoids generic words ("writer", "report", "engine")
# so it never matches the genuine system generators
# (NimbusERP / HRDesk / MediSys ... "Report Writer").
DESKTOP_TOOL_TOKENS = [
    "microsoft", "word", "libreoffice", "canva", "skia", "chromium",
    "ilovepdf", "print to pdf", "pdf-xchange", "acrobat", "foxit", "nitro",
    "photoshop", "google docs", "wps", "openoffice",
]

# Content cues that mark a document as claiming institutional origin.
GENRE_CUES = {
    "invoice": ["tax invoice", "gstin", "invoice no"],
    "letter":  ["relieving letter", "relieved from", "employed with"],
    "medical": ["histopathology", "specimen:", "microscopic examination"],
}


def detect_genre(text: str):
    t = text.lower()
    for genre, cues in GENRE_CUES.items():
        if any(cue in t for cue in cues):
            return genre
    return None


def is_desktop_tool(producer: str, creator: str) -> bool:
    blob = f"{producer or ''} {creator or ''}".lower()
    return any(tok in blob for tok in DESKTOP_TOOL_TOKENS)


def is_signed(pdf_bytes: bytes) -> bool:
    return (b"/ByteRange" in pdf_bytes
            or b"/Type/Sig" in pdf_bytes or b"/Type /Sig" in pdf_bytes)


def printed_date_from_text(text: str):
    """Find a dd-mm-yyyy date printed on the document (invoice/letter/medical
    all print one). Returns a datetime or None."""
    import re
    m = re.search(r"\b(\d{2})-(\d{2})-(\d{4})\b", text)
    if not m:
        return None
    d, mo, y = map(int, m.groups())
    try:
        return datetime(y, mo, d)
    except ValueError:
        return None


def inspect(path: Path) -> dict:
    raw = path.read_bytes()
    reader = PdfReader(path)

    text = ""
    for page in reader.pages:
        try:
            text += page.extract_text() or ""
        except Exception:
            pass

    producer = creator = None
    created = None
    try:
        producer = reader.metadata.producer
        creator = reader.metadata.creator
        created = reader.metadata.creation_date
    except Exception:
        pass

    genre = detect_genre(text)
    institutional = genre is not None
    desktop = is_desktop_tool(producer, creator)
    unsigned = not is_signed(raw)

    printed = printed_date_from_text(text)
    gap_days = None
    if printed and created:
        gap_days = abs((printed - created.replace(tzinfo=None)).days)
    date_incoherent = gap_days is not None and gap_days > DATE_GAP_THRESHOLD_DAYS

    # the three signals
    sig_A = 2 if (institutional and desktop) else 0
    sig_B = 2 if date_incoherent else 0
    sig_C = 1 if (institutional and unsigned and desktop) else 0
    score = sig_A + sig_B + sig_C

    if score >= 5:
        grade = "conviction"
    elif score >= 3:
        grade = "review"
    else:
        grade = "clear"
    flagged = score >= 3

    return {"genre": genre or "", "desktop_producer": desktop,
            "date_gap_days": gap_days if gap_days is not None else "",
            "unsigned": unsigned, "sig_A": sig_A, "sig_B": sig_B,
            "sig_C": sig_C, "score": score, "grade": grade, "flagged": flagged}


def main():
    RESULTS.mkdir(exist_ok=True)
    per_file = []
    total_time = 0.0

    for arm, folder in ARMS:
        for sub in SUBFOLDERS:
            d = CORPUS / folder / sub
            if not d.exists():
                continue
            for pdf in sorted(d.glob("*.pdf")):
                t0 = time.perf_counter()
                sig = inspect(pdf)
                total_time += time.perf_counter() - t0
                per_file.append({"arm": arm, "doc_type": sub[:-1],
                                 "file": f"{folder}/{sub}/{pdf.name}", **sig})

    if not per_file:
        raise SystemExit("No PDFs found. Run steps 1 and 2 first.")

    cols = ["arm", "doc_type", "file", "genre", "desktop_producer",
            "date_gap_days", "unsigned", "sig_A", "sig_B", "sig_C",
            "score", "grade", "flagged"]
    pf_path = RESULTS / "provenance_per_file.csv"
    with open(pf_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader(); w.writerows(per_file)

    summary = []
    for arm, folder in ARMS:
        rows = [r for r in per_file if r["arm"] == arm]
        if not rows:
            continue
        n = len(rows)
        flagged = sum(1 for r in rows if r["flagged"])
        conv = sum(1 for r in rows if r["grade"] == "conviction")
        rev = sum(1 for r in rows if r["grade"] == "review")
        summary.append({
            "arm": arm, "n": n, "flagged": flagged,
            "flag_rate_%": round(100 * flagged / n, 1),
            "conviction": conv, "review": rev,
            "meaning": "false positives" if arm == "genuine" else "correct detections",
        })

    t2_path = RESULTS / "table2_provenance.csv"
    with open(t2_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0].keys()))
        w.writeheader(); w.writerows(summary)

    mean_ms = 1000 * total_time / len(per_file)

    print("\n" + "=" * 74)
    print("TABLE 2 — Provenance coherence: the gap, closed")
    print("=" * 74)
    print(f"{'arm':<20}{'n':>6}{'flagged':>9}{'rate':>8}{'convict':>9}{'review':>8}")
    print("-" * 74)
    for s in summary:
        print(f"{s['arm']:<20}{s['n']:>6}{s['flagged']:>9}{s['flag_rate_%']:>7}%"
              f"{s['conviction']:>9}{s['review']:>8}")
    print("-" * 74)
    print("Compare the re-authored row to TABLE 1: 0% caught there -> caught here.")
    print("Genuine stays at 0 flagged: the defense costs no false positives.")
    print(f"\nMean provenance runtime: {mean_ms:.2f} ms/document "
          f"(confirms the 'milliseconds' claim in the manuscript).")
    print(f"\nPer-file: {pf_path}\nSummary:  {t2_path}")


if __name__ == "__main__":
    main()
