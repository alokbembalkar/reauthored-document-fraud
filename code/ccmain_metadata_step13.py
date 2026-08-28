#!/usr/bin/env python3
"""
Step 13 — measure our signals against CC-MAIN-2021-31-PDF-UNTRUNCATED.

7.9M PDFs crawled July/August 2021, re-fetched to undo Common Crawl's 1 MB
truncation, assembled at NASA JPL under DARPA SafeDocs. The files are 8 TB and
we need none of them: Tika-derived metadata for every one of the 7.9M ships as
a 448 MB gzipped CSV in the same S3 bucket as GovDocs1.

Three of our signals are computable from that CSV alone, at full scale:

  producer / creator  -> the desktop-vs-institutional precondition for A and C
  created vs modified -> modified_after_creation, the working half of Table 1
  has_signature       -> the empirical basis for the two-grade clearing doctrine

Why this matters more than another synthetic run: the GovDocs1 pilot put the
desktop-producer rate at 2.5% under the converter-exclusion policy, and that
number is an artifact of a 2008 corpus in which almost everything went through
Acrobat Distiller. The 1k sample of this corpus puts the same figure at 33.8%.
Any false-positive rate we quote from GovDocs1 alone is era-bound.

Rows are URL-keyed, not file-keyed, so a file crawled from five URLs appears
five times. Deduplication is by file_name, held in a bitmap rather than a set
because 7.9M Python strings would cost about a gigabyte.

Nothing here is fitted to the data. The token policies are imported unchanged
from step 12 so this corpus stays a clean held-out measurement.

Usage:
    python3 ccmain_metadata_step13.py                 # tika only, ~448 MB
    python3 ccmain_metadata_step13.py --with-pdfinfo  # adds poppler cross-check
"""
import argparse
import collections
import csv
import gzip
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rescore_tokens_step12 import POLICIES, wilson   # noqa: E402

BASE = ("https://digitalcorpora.s3.amazonaws.com/corpora/files/"
        "CC-MAIN-2021-31-PDF-UNTRUNCATED/metadata")
DATA = Path.home() / "Desktop" / "Forgery" / "Data" / "ccmain2021"
RESULTS = Path.home() / "Desktop" / "Research" / "results"
TIKA = "tika-20230714.csv.gz"
PDFINFO = "pdfinfo-20230315.csv.gz"
N_FILES = 7_932_878

# GovDocs1 (2008), same policy code, from step 12 on the 2000-doc pilot.
GOVDOCS_DESKTOP = {"P0_published": 80.2, "P1_converter_excl": 2.5,
                   "P2_authoring_only": 1.1, "P3_author_or_editor": 1.1}
# Synthetic genuine arm (n=3000), from edittrace_per_file.csv.
SYNTH_MODIFIED_AFTER = 0.0


def download(name: str) -> Path:
    """curl -C - resumes a partial, which matters: the GovDocs1 run lost 49 of
    83 zips to DNS dropouts on this same connection."""
    DATA.mkdir(parents=True, exist_ok=True)
    dest = DATA / name
    print(f"fetching {name} ...", flush=True)
    subprocess.run(["curl", "-fL", "-C", "-", "--retry", "8",
                    "--retry-delay", "10", "--retry-all-errors",
                    "-o", str(dest), f"{BASE}/{name}"], check=True)
    print(f"  {dest.stat().st_size/1e6:.0f} MB at {dest}", flush=True)
    return dest


class Seen:
    """Bitmap over the 0000000.pdf .. 7932877.pdf naming, with a set for
    anything that does not fit the pattern."""

    def __init__(self):
        self.bits = bytearray(N_FILES // 8 + 2)
        self.odd = set()

    def first(self, name: str) -> bool:
        stem = name[:-4] if name.endswith(".pdf") else name
        if not stem.isdigit():
            if name in self.odd:
                return False
            self.odd.add(name)
            return True
        i = int(stem)
        if i >= N_FILES:
            if name in self.odd:
                return False
            self.odd.add(name)
            return True
        b, m = divmod(i, 8)
        if self.bits[b] >> m & 1:
            return False
        self.bits[b] |= 1 << m
        return True


def rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8-sig", errors="replace",
                   newline="") as fh:
        yield from csv.DictReader(fh)


def istrue(v) -> bool:
    return (v or "").strip().lower() in ("t", "true", "1", "yes")


def parse_dt(s: str):
    if not s:
        return None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def scan_tika(path: Path):
    seen = Seen()
    producers = collections.Counter()
    incr = collections.Counter()
    desktop = collections.Counter()
    gaps = []
    n = signed = both_dates = mod_after = 0
    encrypted = xfa = nonembedded = 0

    for i, r in enumerate(rows(path), 1):
        if i % 1_000_000 == 0:
            print(f"  {i/1e6:.0f}M rows, {n/1e6:.2f}M unique files", flush=True)
        fname = r.get("file_name") or ""
        if not fname or not seen.first(fname):
            continue
        n += 1

        prod = (r.get("pdf_producer") or "").strip()
        creator = (r.get("xmp_creator_tool") or "").strip()
        producers[prod[:80] or "(none)"] += 1
        blob = (prod + " " + creator).lower()
        for name, fn in POLICIES.items():
            if fn(blob):
                desktop[name] += 1

        # Tika writes these as t/f, not true/false. Getting this wrong reads
        # as a clean 0.00% across all 7.9M rows, which is what it did first pass.
        if istrue(r.get("has_signature")):
            signed += 1
        if istrue(r.get("encrypted")):
            encrypted += 1
        if istrue(r.get("has_xfa")):
            xfa += 1
        if istrue(r.get("pdf_contains_non_embedded_font")):
            nonembedded += 1

        iu = (r.get("pdf_incremental_updates") or "").strip()
        incr[iu if iu in ("", "0", "1", "2", "3") else "4+"] += 1

        c, m = parse_dt(r.get("created", "")), parse_dt(r.get("modified", ""))
        if c and m:
            both_dates += 1
            if m > c:
                mod_after += 1
                gaps.append((m - c).days)

    return dict(n=n, producers=producers, incr=incr, desktop=desktop,
                signed=signed, both_dates=both_dates, mod_after=mod_after,
                gaps=gaps, encrypted=encrypted, xfa=xfa,
                nonembedded=nonembedded)


def pct(k, n):
    lo, hi = wilson(k, n)
    return f"{100*k/n:6.2f}%  [{lo:.2f}, {hi:.2f}]"


def report(s):
    n = s["n"]
    RESULTS.mkdir(parents=True, exist_ok=True)
    print(f"\nunique PDFs measured: {n:,}\n")

    print("desktop-producer rate, by policy, against the 2008 corpus")
    print(f"  {'policy':22s} {'CC-MAIN 2021':>26s} {'GovDocs1 2008':>15s}")
    rowsout = []
    for name in POLICIES:
        k = s["desktop"][name]
        lo, hi = wilson(k, n)
        print(f"  {name:22s} {100*k/n:11.2f}%  [{lo:.2f}, {hi:.2f}] "
              f"{GOVDOCS_DESKTOP[name]:13.1f}%")
        rowsout.append([name, k, n, round(100*k/n, 3), round(lo, 3), round(hi, 3),
                        GOVDOCS_DESKTOP[name]])
    with (RESULTS / "ccmain_policy_compare.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["policy", "desktop", "n", "rate_%", "wilson_lo_%",
                    "wilson_hi_%", "govdocs1_2008_rate_%"])
        w.writerows(rowsout)

    print("\nedit-trace signals on real documents")
    bd = s["both_dates"]
    print(f"  both dates present        {pct(bd, n)}")
    print(f"  modified AFTER created    {pct(s['mod_after'], bd)}  of those")
    print(f"                            {pct(s['mod_after'], n)}  of all files")
    print(f"    synthetic genuine arm   {SYNTH_MODIFIED_AFTER:6.2f}%"
          "          <- generator writes the two dates equal")
    nz = n - s["incr"].get("0", 0) - s["incr"].get("", 0)
    print(f"  incremental updates > 0   {pct(nz, n)}")

    print("\nsignature and container facts")
    print(f"  has_signature             {pct(s['signed'], n)}")
    print(f"  encrypted                 {pct(s['encrypted'], n)}")
    print(f"  has_xfa                   {pct(s['xfa'], n)}")
    print(f"  non-embedded font         {pct(s['nonembedded'], n)}")

    if s["gaps"]:
        g = sorted(s["gaps"])
        qs = [(q, g[min(len(g) - 1, int(len(g) * q / 100))]) for q in
              (10, 25, 50, 75, 90, 95, 99)]
        print("\n  modified-minus-created, days, where modified is later")
        print("   " + "  ".join(f"p{q}={v}" for q, v in qs))
        with (RESULTS / "ccmain_modgap_quantiles.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["quantile", "days"])
            w.writerows(qs)

    with (RESULTS / "ccmain_producers.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["producer", "files", "share_%"])
        for p, k in s["producers"].most_common(1000):
            w.writerow([p, k, round(100 * k / n, 4)])
    print("\ntop producers")
    for p, k in s["producers"].most_common(15):
        print(f"  {k:8,}  {100*k/n:5.2f}%  {p[:62]}")

    with (RESULTS / "ccmain_summary.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["metric", "k", "n", "rate_%", "wilson_lo_%", "wilson_hi_%"])
        for lbl, k, d in [("has_signature", s["signed"], n),
                          ("encrypted", s["encrypted"], n),
                          ("has_xfa", s["xfa"], n),
                          ("non_embedded_font", s["nonembedded"], n),
                          ("both_dates_present", bd, n),
                          ("modified_after_created_of_dated", s["mod_after"], bd),
                          ("modified_after_created_of_all", s["mod_after"], n),
                          ("incremental_updates_gt0", nz, n)]:
            lo, hi = wilson(k, d)
            w.writerow([lbl, k, d, round(100 * k / d, 4), round(lo, 4), round(hi, 4)])
    print(f"\nwrote {RESULTS}/ccmain_summary.csv, _policy_compare.csv, _producers.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-pdfinfo", action="store_true",
                    help="also fetch the poppler table as a producer cross-check")
    ap.add_argument("--tika", type=Path, default=None, help="use a local copy")
    args = ap.parse_args()

    path = args.tika or (DATA / TIKA)
    if not path.exists():
        path = download(TIKA)
    if args.with_pdfinfo and not (DATA / PDFINFO).exists():
        download(PDFINFO)

    print(f"\nscanning {path.name} ...", flush=True)
    report(scan_tika(path))


if __name__ == "__main__":
    main()
