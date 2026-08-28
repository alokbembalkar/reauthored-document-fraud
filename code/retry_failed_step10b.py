#!/usr/bin/env python3
"""
Step 10b — re-fetch the GovDocs1 zips that step 10 abandoned.

Step 10's fetch() retries three times with a 5/10/15 s backoff, so it gives up
after about half a minute. The failures in the pilot were DNS dropouts
("nodename nor servname provided"), which on a laptop last minutes, not
seconds: 49 of 83 attempted zips were abandoned, costing roughly half the
corpus. Nothing was wrong with the zips themselves.

Abandoned zips are never written to the manifest, so step 10 would retry them
on a later resume. This script does it directly: it reads the ids out of the
download log, waits for the resolver to come back before each attempt, and
backs off far more patiently.

Run this AFTER the main download finishes. Two processes appending to the same
manifest will interleave rows, and both would enforce --max-gb independently.

Usage:
    python3 retry_failed_step10b.py                  # dry run, lists what it would fetch
    python3 retry_failed_step10b.py --go
    python3 retry_failed_step10b.py --go --max-gb 6
"""
import argparse
import csv
import hashlib
import re
import socket
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://digitalcorpora.s3.amazonaws.com/corpora/files/govdocs1/zipfiles"
HOST = "digitalcorpora.s3.amazonaws.com"
DATA = Path.home() / "Desktop" / "Forgery" / "Data" / "govdocs1_pdfs"
LOG = Path.home() / "Desktop" / "Forgery" / "Data" / "download.log"
CHUNK = 1 << 20
BACKOFF = [5, 15, 30, 60, 120, 120, 120, 120]     # ~10 min of patience per zip


def wait_for_dns(tries: int = 30, gap: int = 20) -> bool:
    """A dead resolver is the failure mode that cost us 49 zips. Sit on it
    rather than burning a retry budget against a network that is simply down."""
    for i in range(tries):
        try:
            socket.getaddrinfo(HOST, 443)
            return True
        except OSError:
            print(f"    no DNS, waiting {gap}s ({i+1}/{tries})", flush=True)
            time.sleep(gap)
    return False


def fetch(url: str, dest: Path) -> bool:
    part = dest.with_suffix(".part")
    for attempt, pause in enumerate(BACKOFF, 1):
        if not wait_for_dns():
            print("    network still down, aborting", flush=True)
            return False
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
            part.unlink(missing_ok=True)      # step 10 leaked these; 615.part was 240 MB
            time.sleep(pause)
    return False


def abandoned_ids(log: Path):
    """Ids in log order, deduplicated, as step 10 printed them."""
    if not log.exists():
        return []
    seen, out = set(), []
    for m in re.finditer(r"giving up on (\d{3})\.zip", log.read_text(errors="replace")):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


def load_manifest(path: Path):
    done, hashes, n_pdf, kept = set(), set(), 0, 0
    if path.exists():
        for row in csv.DictReader(open(path, errors="replace")):
            if row["kind"] == "zip_done":
                done.add(row["zip_id"])
            elif row["kind"] == "pdf":
                hashes.add(row["sha256"])
                n_pdf += 1
                kept += int(row["bytes"])
    return done, hashes, n_pdf, kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="actually download")
    ap.add_argument("--max-gb", type=float, default=8.0, help="additional kept bytes")
    ap.add_argument("--log", type=Path, default=LOG)
    args = ap.parse_args()

    manifest = DATA / "manifest_wild.csv"
    done, hashes, n_pdf, kept = load_manifest(manifest)
    todo = [z for z in abandoned_ids(args.log) if z not in done]

    print(f"manifest: {len(done)} zips done, {n_pdf} PDFs, {kept/1e9:.1f} GB")
    print(f"abandoned and still missing: {len(todo)} zips")
    print("  " + " ".join(todo))
    if not args.go:
        print("\ndry run. re-run with --go once the main download has finished.")
        return

    stale = [p for p in DATA.glob("*.part")]
    if stale:
        print(f"warning: {len(stale)} .part files present "
              f"({', '.join(p.name for p in stale)}). "
              "If the main download is still running, stop and wait.")

    out = open(manifest, "a", newline="")
    w = csv.writer(out)
    cap = kept + int(args.max_gb * 1e9)

    for zid in todo:
        if kept >= cap:
            print("disk budget reached, stopping", flush=True)
            break
        zpath = DATA / f"{zid}.zip"
        print(f"[{n_pdf} PDFs  {kept/1e9:.1f} GB] retrying {zid}.zip ...", flush=True)
        if not fetch(f"{BASE}/{zid}.zip", zpath):
            print(f"    still failing on {zid}.zip, leaving for a later pass", flush=True)
            continue
        added = 0
        try:
            with zipfile.ZipFile(zpath) as zf:
                for m in zf.namelist():
                    if not m.lower().endswith(".pdf") or kept >= cap:
                        continue
                    try:
                        data = zf.read(m)
                    except Exception:
                        continue
                    h = hashlib.sha256(data).hexdigest()
                    if h in hashes:
                        continue
                    dest = DATA / m
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
