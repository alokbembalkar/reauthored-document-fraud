#!/usr/bin/env python3
"""
provenance_wild_step11.py — provenance engine over the wild GovDocs1 sample.

Purpose: measure the false-positive behaviour of the provenance-coherence
signals on REAL PDFs that nobody constructed (corpus from step 10). This is
the number the synthetic corpus cannot provide: on the synthetic genuine arm
the flag rate is 0 by construction (§5.1 of the paper); here it is measured.

Differences from provenance_check_step4.py, all forced by wild data:
  * the desktop-token list is IDENTICAL to step 4 (comparability); the raw
    producer/creator strings are recorded per file so alternative lists can
    be re-scored offline without re-parsing 20k PDFs
  * genre cues are broadened beyond the three Indian-BFSI genres (US .gov
    corpus); every cue hit is recorded so results can be sliced per cue
  * printed-date extraction handles US formats; the gap uses the MINIMUM
    |printed - created| over all dates found, which is the conservative
    choice for an FP measurement (it can only under-fire signal B)
  * per-file 90 s alarm and 80 MB size guard against pathological PDFs

Documents are unlabelled: we assume genuine (fraud base rate in a 2009 .gov
crawl is negligible) and every flag is written to a review worksheet for
manual adjudication. Coverage per signal is reported, not assumed.

RUN (venv active, from the Research folder, after step 10):
    python3 code/provenance_wild_step11.py            # default 6 workers
    python3 code/provenance_wild_step11.py --workers 8 --limit 2000   # pilot

OUTPUT:
    results/wild_features.csv     raw per-file features (re-scorable)
    results/wild_scored.csv       per-file scores at >=3 and >=2
    results/wild_flags_review.csv flagged files for manual adjudication
    stdout                        coverage, flag rates with Wilson 95% CIs
"""

import argparse
import csv
import math
import re
import signal
import sys
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path

import logging
logging.disable(logging.WARNING)          # wild PDFs make pypdf noisy

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
DATA = Path.home() / "Desktop" / "Forgery" / "Data" / "govdocs1_pdfs"
RESULTS = ROOT / "results"

DATE_GAP_DEFAULT = 45
MAX_PAGES = 5
MAX_BYTES = 80 * 1024 * 1024
FILE_TIMEOUT_S = 90

# identical to step 4 — do not extend without re-scoring the synthetic corpus
DESKTOP_TOKENS = ["microsoft", "word", "libreoffice", "canva", "skia", "chromium",
                  "ilovepdf", "print to pdf", "pdf-xchange", "acrobat", "foxit",
                  "nitro", "photoshop", "google docs", "wps", "openoffice"]

# broadened for a US .gov corpus; every hit is recorded per file
GENRE_CUES = {
    "invoice":   ["tax invoice", "invoice no", "invoice number", "amount due",
                  "bill to", "remit to", "purchase order"],
    "letter":    ["relieving letter", "employed with", "to whom it may concern",
                  "letter of appointment", "termination of employment"],
    "medical":   ["histopathology", "microscopic examination", "pathology report",
                  "laboratory report", "specimen received"],
    "statement": ["account statement", "statement of account", "opening balance",
                  "closing balance", "statement period"],
    "certificate": ["this is to certify", "certificate of completion",
                    "certificate no", "hereby certifies"],
}

MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}
MON_RE = "|".join(m[:3] for m in MONTHS)


def _mk(y, mo, d):
    try:
        if 1980 <= y <= 2030:
            return datetime(y, mo, d)
    except ValueError:
        pass
    return None


def printed_dates(text: str, cap: int = 40):
    """All plausible printed dates. Ambiguous n/n/yyyy tries both readings —
    harmless because the caller takes the minimum gap."""
    out = []
    for m in re.finditer(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", text):
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        for d in (_mk(y, a, b), _mk(y, b, a)):
            if d:
                out.append(d)
        if len(out) >= cap:
            return out
    for m in re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text):
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            out.append(d)
    for m in re.finditer(rf"\b({MON_RE})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})",
                         text, re.I):
        d = _mk(int(m.group(3)), MONTHS.get(m.group(1).lower()[:3] + "xxxxx"[:0] or "", 0)
                or [v for k, v in MONTHS.items() if k.startswith(m.group(1).lower())][0],
                int(m.group(2)))
        if d:
            out.append(d)
    for m in re.finditer(rf"\b(\d{{1,2}})\s+({MON_RE})[a-z]*\.?\s+(\d{{4}})", text, re.I):
        mo = [v for k, v in MONTHS.items() if k.startswith(m.group(2).lower())]
        if mo:
            d = _mk(int(m.group(3)), mo[0], int(m.group(1)))
            if d:
                out.append(d)
    return out[:cap]


def safe_creation_date(md):
    """pypdf's .creation_date raises ValueError on malformed strings, which is
    common in the wild (7.8% of the pilot: '2/1/2005 14:47:46', 'D:191051017102624').
    Fall back to parsing the raw string; return None if it is unrecoverable."""
    if md is None:
        return None
    try:
        return md.creation_date
    except (ValueError, TypeError, KeyError):
        pass
    try:
        raw = str(md.get("/CreationDate") or "")
    except Exception:
        return None
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", raw)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})", raw)
    if m:                                   # US-style m/d/yyyy, as seen in GovDocs1
        return _mk(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


class _Timeout(Exception):
    pass


def _alarm(sig, frame):
    raise _Timeout()


def inspect(path_str: str) -> dict:
    path = Path(path_str)
    rec = {"file": path.name, "thread": path.parent.name, "status": "ok",
           "bytes": 0, "producer": "", "creator": "", "creation_date": "",
           "n_printed_dates": 0, "min_gap_days": "", "cues_hit": "",
           "institutional": False, "desktop": False, "unsigned": True,
           "sig_A": 0, "sig_B": 0, "sig_C": 0, "score": 0}
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(FILE_TIMEOUT_S)
    try:
        rec["bytes"] = path.stat().st_size
        if rec["bytes"] > MAX_BYTES:
            rec["status"] = "skipped_large"
            return rec
        raw = path.read_bytes()
        rec["unsigned"] = b"/ByteRange" not in raw
        r = PdfReader(path)
        if r.is_encrypted:
            try:
                r.decrypt("")
            except Exception:
                rec["status"] = "encrypted"
                return rec
        md = r.metadata
        prod = (md.producer or "") if md else ""
        crea = (md.creator or "") if md else ""
        # wild PDFs embed NULs/control chars in metadata; keep the CSV parseable
        clean = lambda s: "".join(c for c in s if c.isprintable())[:200]
        rec["producer"], rec["creator"] = clean(prod), clean(crea)
        created = safe_creation_date(md)
        if created:
            rec["creation_date"] = f"{created:%Y-%m-%d}"

        text = ""
        for pg in r.pages[:MAX_PAGES]:
            try:
                text += (pg.extract_text() or "").lower()
            except Exception:
                pass

        hits = [f"{g}:{c}" for g, cues in GENRE_CUES.items()
                for c in cues if c in text]
        rec["cues_hit"] = ";".join(hits)[:300]
        rec["institutional"] = bool(hits)
        rec["desktop"] = any(t in f"{prod} {crea}".lower() for t in DESKTOP_TOKENS)

        dates = printed_dates(text)
        rec["n_printed_dates"] = len(dates)
        if dates and created:
            c = created.replace(tzinfo=None)
            rec["min_gap_days"] = min(abs((d - c).days) for d in dates)

        gap = rec["min_gap_days"]
        rec["sig_A"] = 2 if rec["institutional"] and rec["desktop"] else 0
        rec["sig_B"] = 2 if (gap != "" and gap > DATE_GAP_DEFAULT) else 0
        rec["sig_C"] = 1 if (rec["institutional"] and rec["unsigned"]
                             and rec["desktop"]) else 0
        rec["score"] = rec["sig_A"] + rec["sig_B"] + rec["sig_C"]
    except _Timeout:
        rec["status"] = "timeout"
    except Exception as e:
        rec["status"] = f"parse_error:{type(e).__name__}"
    finally:
        signal.alarm(0)
    return rec


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (100 * max(0, centre - half), 100 * min(1, centre + half))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="pilot: first N files")
    ap.add_argument("--data", type=Path, default=DATA)
    args = ap.parse_args()

    pdfs = sorted(str(p) for p in args.data.rglob("*.pdf"))
    if args.limit:
        pdfs = pdfs[:args.limit]
    if not pdfs:
        sys.exit(f"no PDFs under {args.data} — run step 10 first")
    print(f"scoring {len(pdfs)} wild PDFs with {args.workers} workers...")

    RESULTS.mkdir(exist_ok=True)
    rows = []
    with Pool(args.workers, maxtasksperchild=100) as pool:
        for i, rec in enumerate(pool.imap_unordered(inspect, pdfs, chunksize=20), 1):
            rows.append(rec)
            if i % 500 == 0:
                print(f"  {i}/{len(pdfs)}", flush=True)

    cols = list(rows[0].keys())
    with open(RESULTS / "wild_features.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader(); w.writerows(rows)

    ok = [r for r in rows if r["status"] == "ok"]
    with open(RESULTS / "wild_scored.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader(); w.writerows(ok)
    flagged2 = sorted((r for r in ok if r["score"] >= 2),
                      key=lambda r: -r["score"])
    with open(RESULTS / "wild_flags_review.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader(); w.writerows(flagged2)

    n = len(ok)
    print(f"\nparsed ok: {n}/{len(rows)}  "
          f"(others: {len(rows)-n} skipped/encrypted/error/timeout)")
    print("coverage on parsed files:")
    print(f"  producer present   {sum(1 for r in ok if r['producer'])/n:6.1%}")
    print(f"  creation date      {sum(1 for r in ok if r['creation_date'])/n:6.1%}")
    print(f"  any printed date   {sum(1 for r in ok if r['n_printed_dates'])/n:6.1%}")
    print(f"  gap computable     {sum(1 for r in ok if r['min_gap_days']!='')/n:6.1%}")
    print(f"  institutional cue  {sum(1 for r in ok if r['institutional'])/n:6.1%}")
    print(f"  desktop producer   {sum(1 for r in ok if r['desktop'])/n:6.1%}")
    print(f"  signed             {sum(1 for r in ok if not r['unsigned'])/n:6.1%}")
    for thr in (3, 2):
        k = sum(1 for r in ok if r["score"] >= thr)
        lo, hi = wilson(k, n)
        print(f"score >= {thr}: {k}/{n} = {100*k/n:.2f}%  Wilson95 [{lo:.2f}, {hi:.2f}]%")
    for s in ("sig_A", "sig_B", "sig_C"):
        k = sum(1 for r in ok if r[s] > 0)
        print(f"  {s} fires: {k}/{n} = {100*k/n:.2f}%")
    print(f"\nreview worksheet: results/wild_flags_review.csv "
          f"({len(flagged2)} rows, score >= 2)")


if __name__ == "__main__":
    main()
