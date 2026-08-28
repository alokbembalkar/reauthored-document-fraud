#!/usr/bin/env python3
"""
wild_corpus_step10.py — sample real-world PDFs from GovDocs1 (Digital Corpora).

Purpose: a genuine test corpus of REAL PDFs with REAL metadata, for measuring
the provenance engine's false-positive rate on documents nobody constructed
(step 11). GovDocs1 is ~1M files crawled from .gov websites, distributed as
1,000 thread zips; each ~300-490 MB zip holds ~1,000 files of which ~200-480
are PDFs. There is no PDF-only archive (checked by_type/: none), so we pull
whole zips in seeded-random order, keep only the *.pdf members, and delete
the zip before fetching the next. Peak transient disk = one zip.

The same corpus doubles as a real-world test set for the TrustLens forgery
product, hence the data location under Desktop/Forgery.

  * resumable: re-running skips zips already marked done in the manifest
  * dedupes by sha256
  * stops at --target PDFs or --max-gb kept, whichever comes first

RUN:
    python3 code/wild_corpus_step10.py                 # defaults: 20,000 / 14 GB
    python3 code/wild_corpus_step10.py --target 5000   # pilot

OUTPUT:
    ~/Desktop/Forgery/Data/govdocs1_pdfs/NNN/NNNNNN.pdf
    ~/Desktop/Forgery/Data/govdocs1_pdfs/manifest_wild.csv
"""

import argparse
import csv
import hashlib
import random
import sys
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://digitalcorpora.s3.amazonaws.com/corpora/files/govdocs1/zipfiles"
DATA = Path.home() / "Desktop" / "Forgery" / "Data" / "govdocs1_pdfs"
SEED = 42
CHUNK = 1 << 20


# The first run abandoned 49 of 83 zips to DNS dropouts, because three tries at
# a 5/10/15 s backoff give up after half a minute and a laptop resolver can be
# down for several. Wait it out instead.
BACKOFF = [5, 15, 30, 60, 120, 120]


def fetch(url: str, dest: Path) -> bool:
    """Stream url to dest, retrying patiently. True on success."""
    part = dest.with_suffix(".part")
    for attempt, pause in enumerate(BACKOFF, 1):
        try:
            req = Request(url, headers={"User-Agent": "research-corpus-sampler"})
            with urlopen(req, timeout=120) as r, open(part, "wb") as fh:
                while True:
                    buf = r.read(CHUNK)
                    if not buf:
                        break
                    fh.write(buf)
            part.rename(dest)
            return True
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            print(f"    attempt {attempt}/{len(BACKOFF)} failed: {e}", flush=True)
            part.unlink(missing_ok=True)     # otherwise a dead partial is left on disk
            time.sleep(pause)
    return False


def load_manifest(path: Path):
    done_zips, hashes, n_pdf, kept = set(), set(), 0, 0
    if path.exists():
        for row in csv.DictReader(open(path)):
            if row["kind"] == "zip_done":
                done_zips.add(row["zip_id"])
            elif row["kind"] == "pdf":
                hashes.add(row["sha256"])
                n_pdf += 1
                kept += int(row["bytes"])
    return done_zips, hashes, n_pdf, kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=20000, help="PDFs to keep")
    ap.add_argument("--max-gb", type=float, default=14.0, help="kept-bytes cap")
    args = ap.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    manifest = DATA / "manifest_wild.csv"
    new_file = not manifest.exists()
    done_zips, hashes, n_pdf, kept = load_manifest(manifest)
    print(f"resume state: {len(done_zips)} zips done, {n_pdf} PDFs, "
          f"{kept/1e9:.1f} GB kept", flush=True)

    order = [f"{i:03d}" for i in range(1000)]
    random.Random(SEED).shuffle(order)

    out = open(manifest, "a", newline="")
    w = csv.writer(out)
    if new_file:
        w.writerow(["kind", "name", "zip_id", "bytes", "sha256"])

    cap = int(args.max_gb * 1e9)
    for zid in order:
        if n_pdf >= args.target or kept >= cap:
            break
        if zid in done_zips:
            continue
        url = f"{BASE}/{zid}.zip"
        zpath = DATA / f"{zid}.zip"
        print(f"[{n_pdf}/{args.target}  {kept/1e9:.1f} GB] {zid}.zip ...", flush=True)
        if not fetch(url, zpath):
            print(f"    giving up on {zid}.zip, continuing", flush=True)
            continue
        added = 0
        try:
            with zipfile.ZipFile(zpath) as zf:
                for m in zf.namelist():
                    if not m.lower().endswith(".pdf"):
                        continue
                    if n_pdf >= args.target or kept >= cap:
                        break
                    try:
                        data = zf.read(m)
                    except Exception:
                        continue
                    h = hashlib.sha256(data).hexdigest()
                    if h in hashes:
                        continue
                    dest = DATA / m                      # NNN/NNNNNN.pdf
                    dest.parent.mkdir(exist_ok=True)
                    dest.write_bytes(data)
                    hashes.add(h)
                    n_pdf += 1
                    kept += len(data)
                    added += 1
                    w.writerow(["pdf", m, zid, len(data), h])
        except zipfile.BadZipFile:
            print(f"    corrupt zip {zid}, skipped", flush=True)
        finally:
            zpath.unlink(missing_ok=True)
        w.writerow(["zip_done", "", zid, "", ""])
        out.flush()
        print(f"    +{added} PDFs", flush=True)

    out.close()
    print(f"\nDONE: {n_pdf} PDFs, {kept/1e9:.2f} GB in {DATA}", flush=True)


if __name__ == "__main__":
    main()
