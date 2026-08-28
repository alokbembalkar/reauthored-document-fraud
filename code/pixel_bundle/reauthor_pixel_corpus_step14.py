#!/usr/bin/env python3
"""
Step 14 — build a four-arm pixel corpus so the re-authoring blind spot can be
measured on public data at a defensible sample size.

The blind-spot claim in §3.2 currently rests on 16 tampered invoices scored by
a U-Net trained inside the deployment. Three things are wrong with that, and
only one of them is the sample size:

  n = 16          0/16 leaves a 19.4% upper bound on the true detection rate
  our own model   "your network was broken" is an unanswerable objection
  production data nobody can reproduce it

The paired OG/Edit set at ~/Desktop/Forgery/Data/jithin fixes all three. It has
1,451 pixel-aligned pairs across eight RVL-CDIP genres, each Edit a localized
alteration of its OG. Differencing a pair yields the ground-truth mask the
dataset does not ship.

FOUR ARMS, because two are not enough to make the argument:

  A  original     the OG image, genuine, untouched
  B  edited       the Edit image, fraudulent, edit residue present
  C  reauthored   the EDIT text re-typed and re-rendered: same fraudulent
                  content as B, no residue
  D  reauth_ctrl  the OG text re-typed and re-rendered: genuine content,
                  also no residue

B against A is the positive control. If a detector cannot separate those it is
broken, and the negative result means nothing. C against D is the actual claim:
identical rendering pipeline, one fraudulent and one not, and a detector that
cannot tell them apart has no purchase on re-authored fraud. Scoring C alone
would invite the objection that the model returns zero because the page looks
freshly rendered rather than because fraud is undetectable. D removes that.

WHY THE EDIT SIDE GETS RE-AUTHORED. A re-authored forgery carries the
fraudulent content and none of the residue. Re-authoring the OG would produce a
clean document with honest content, which is arm D, a control, not an attack.

FORMAT NORMALISATION IS MANDATORY. The dataset ships OG as grayscale JPEG and
Edit as RGB or RGBA PNG. Any classifier separates those perfectly on
compression signature alone, so an un-normalised positive control would look
excellent and mean nothing. Every arm is written out as grayscale JPEG at one
quality. Arms A and B then share a compression history, which is what makes
their comparison honest. Arms C and D are fresh renders and do not share it,
which is not a confound: that is the property under study.

Layout is reconstructed from tesseract word boxes and rendered through headless
Chrome, whose Skia/PDF producer string is one of the desktop tools in the
threat model, then rasterised. Fonts are deliberately not matched to the
original; a forger retyping a document uses their own.

Usage:
    python3 reauthor_pixel_corpus_step14.py --limit 20        # try it small
    python3 reauthor_pixel_corpus_step14.py --workers 4       # everything
"""
import argparse
import base64
import csv
import html
import json
import os
import random
import shutil
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image

# Paths default to the macOS layout but every one is overridable, because the
# build is meant to run on a Linux box, not here.
SRC = Path(os.environ.get("PIXEL_SRC",
           Path.home() / "Desktop" / "Forgery" / "Data" / "jithin"))
OUT = Path(os.environ.get("PIXEL_OUT",
           Path.home() / "Desktop" / "Forgery" / "Data" / "pixel_corpus"))
RESULTS = Path(os.environ.get("PIXEL_RESULTS",
               Path.home() / "Desktop" / "Research" / "results"))
ARMS = ["original", "edited", "reauthored", "reauth_ctrl"]


def find_chrome() -> str:
    """Nothing in this script is GPU work: tesseract and Chrome page layout are
    both CPU-bound. The GPU stage is scoring, in step 15."""
    env = os.environ.get("CHROME_BIN")
    cands = ([env] if env else []) + [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "google-chrome", "google-chrome-stable", "chromium",
        "chromium-browser", "/usr/bin/chromium", "/usr/bin/google-chrome",
    ]
    for c in cands:
        if not c:
            continue
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
        w = shutil.which(c)
        if w:
            return w
    raise SystemExit("no Chrome/Chromium found; set CHROME_BIN")


CHROME = None      # resolved in main(), so --help works without a browser

JPEG_Q = 92            # one quality for every arm; uniformity is the point
MIN_WORDS = 25         # below this a re-authoring is not a plausible document
MIN_CONF = 55.0        # mean tesseract confidence; handwriting fails this
# Skip heavy documents so a laptop build stays cool. Render cost scales with
# raster area and word count; a few dense pages dominate wall time and heat.
# Overridable with MAX_PIXELS / MAX_WORDS env vars for a full run on a big box.
MAX_PIXELS = int(os.environ.get("MAX_PIXELS", 2_200_000))   # ~1275x1650, a page
MAX_WORDS = int(os.environ.get("MAX_WORDS", 1200))
DIFF_THRESH = 32       # per-pixel delta counted as an edit
SEED = 42


# --------------------------------------------------------------------------
# pairing and ground truth
# --------------------------------------------------------------------------
def find_pairs():
    pairs = []
    for og_dir in sorted(SRC.glob("OG/*-OG")):
        genre = og_dir.name[:-3]
        cands = list(SRC.glob(f"Edit/{genre}-*dit")) + list(SRC.glob(f"Edit/{genre}-edit"))
        if not cands:
            continue
        ed_dir = cands[0]
        for f in sorted(og_dir.iterdir()):
            if f.name.startswith(".") or f.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            ed = ed_dir / (f.stem + ".png")
            if ed.exists():
                pairs.append((genre, f, ed))
    return pairs


def tamper_mask(og_path, ed_path):
    """The dataset ships no masks. Differencing aligned pairs recovers them."""
    a = np.asarray(Image.open(og_path).convert("L"), dtype=np.int16)
    b = np.asarray(Image.open(ed_path).convert("L"), dtype=np.int16)
    if a.shape != b.shape:
        return None, None
    d = (np.abs(a - b) > DIFF_THRESH)
    if not d.any():
        return None, None
    ys, xs = np.where(d)
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    return d, box


# --------------------------------------------------------------------------
# OCR and re-authoring
# --------------------------------------------------------------------------
def ocr_words(img_path, workdir):
    """tesseract TSV: word text, box and confidence. Shelled out so the script
    has no pytesseract dependency."""
    stem = str(Path(workdir) / "ocr")
    subprocess.run(["tesseract", str(img_path), stem, "--psm", "3", "tsv"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    words, confs = [], []
    with open(stem + ".tsv", newline="", encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            txt = (r.get("text") or "").strip()
            try:
                conf = float(r.get("conf", -1))
            except ValueError:
                conf = -1.0
            if not txt or conf < 0:
                continue
            words.append({"t": txt, "l": int(r["left"]), "y": int(r["top"]),
                          "w": int(r["width"]), "h": int(r["height"]),
                          "ln": (r.get("block_num"), r.get("par_num"),
                                 r.get("line_num"))})
            confs.append(conf)
    return words, (sum(confs) / len(confs) if confs else 0.0)


def build_html(words, size):
    """Absolute-position each OCR word at its box. Approximate, and that is
    faithful: a forger retyping a document reproduces layout by eye."""
    w, h = size
    # One font size per OCR line, from the median word height. Sizing each word
    # off its own box makes ascenders and descenders swing the size wildly and
    # the page stops looking like something a person typed.
    by_line = {}
    for x in words:
        by_line.setdefault(x["ln"], []).append(x["h"])
    line_h = {k: sorted(v)[len(v) // 2] for k, v in by_line.items()}

    spans = []
    for x in words:
        fs = max(6, int(line_h.get(x["ln"], x["h"]) * 0.82))
        spans.append(
            f'<span style="position:absolute;left:{x["l"]}px;top:{x["y"]}px;'
            f'font-size:{fs}px;line-height:{x["h"]}px;white-space:pre">'
            f'{html.escape(x["t"])}</span>')
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<style>@page{{size:{w}px {h}px;margin:0}}'
            f'html,body{{margin:0;padding:0;width:{w}px;height:{h}px;'
            f'background:#fff;font-family:Helvetica,Arial,sans-serif;color:#000}}'
            f'</style></head><body>{"".join(spans)}</body></html>')


def render(html_text, size, dest, workdir):
    """HTML -> PDF via Chrome (Skia/PDF, a desktop producer in our threat
    model) -> raster at the original pixel dimensions."""
    # Workers are spawned processes: they re-import this module and see the
    # unresolved CHROME = None, so resolution must happen here, not in main().
    global CHROME
    if CHROME is None:
        CHROME = find_chrome()
    w, h = size
    hp = Path(workdir) / "page.html"
    pp = Path(workdir) / "page.pdf"
    hp.write_text(html_text, encoding="utf-8")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-pdf-header-footer", "--virtual-time-budget=8000",
                    "--run-all-compositor-stages-before-draw",
                    f"--print-to-pdf={pp}", hp.as_uri()],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=120)
    if not pp.exists():
        return False
    subprocess.run(["pdftoppm", "-png", "-r", "150", "-singlefile",
                    str(pp), str(Path(workdir) / "raster")],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raster = Path(workdir) / "raster.png"
    if not raster.exists():
        return False
    Image.open(raster).convert("L").resize((w, h), Image.LANCZOS).save(
        dest, "JPEG", quality=JPEG_Q)
    return True


def normalise(src_path, size, dest):
    """One colour mode, one size, one quality, for every arm."""
    Image.open(src_path).convert("L").resize(size, Image.LANCZOS).save(
        dest, "JPEG", quality=JPEG_Q)


# --------------------------------------------------------------------------
def process(job):
    genre, og, ed = job
    stem = og.stem
    rec = {"file": stem, "genre": genre, "status": "", "words": 0,
           "ocr_conf": 0.0, "tamper_px_pct": 0.0, "tamper_bbox_pct": 0.0,
           "width": 0, "height": 0}
    try:
        mask, box = tamper_mask(og, ed)
        if mask is None:
            rec["status"] = "unaligned_or_identical"
            return rec
        h, w = mask.shape
        size = (w, h)
        if w * h > MAX_PIXELS:
            rec["status"] = "skipped_heavy_pixels"
            rec.update(width=w, height=h)
            return rec
        rec.update(width=w, height=h,
                   tamper_px_pct=round(100 * float(mask.mean()), 4),
                   tamper_bbox_pct=round(
                       100 * (box[2] - box[0]) * (box[3] - box[1]) / (w * h), 3))

        with tempfile.TemporaryDirectory() as wd:
            ed_words, ed_conf = ocr_words(ed, wd)
            rec["words"], rec["ocr_conf"] = len(ed_words), round(ed_conf, 1)
            if len(ed_words) < MIN_WORDS or ed_conf < MIN_CONF:
                rec["status"] = "ocr_below_gate"
                return rec
            if len(ed_words) > MAX_WORDS:
                rec["status"] = "skipped_heavy_words"
                return rec
            og_words, _ = ocr_words(og, wd)

            normalise(og, size, OUT / "original" / f"{stem}.jpg")
            normalise(ed, size, OUT / "edited" / f"{stem}.jpg")
            if not render(build_html(ed_words, size), size,
                          OUT / "reauthored" / f"{stem}.jpg", wd):
                rec["status"] = "render_failed"
                return rec
            if not render(build_html(og_words, size), size,
                          OUT / "reauth_ctrl" / f"{stem}.jpg", wd):
                rec["status"] = "render_failed"
                return rec
            np.savez_compressed(OUT / "masks" / f"{stem}.npz",
                                mask=mask.astype(np.uint8))
        rec["status"] = "ok"
    except subprocess.TimeoutExpired:
        rec["status"] = "timeout"
    except Exception as e:
        rec["status"] = f"error:{type(e).__name__}"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = all pairs")
    ap.add_argument("--workers", type=int, default=max(2, os.cpu_count() // 2))
    ap.add_argument("--shuffle", action="store_true",
                    help="seeded shuffle, so a --limit run is not genre-ordered")
    args = ap.parse_args()

    global CHROME
    if not shutil.which("tesseract"):
        raise SystemExit("tesseract not found on PATH")
    CHROME = find_chrome()
    if not SRC.exists():
        raise SystemExit(f"source pairs not found at {SRC}; set PIXEL_SRC")
    print(f"chrome: {CHROME}\nsource: {SRC}", flush=True)

    for a in ARMS + ["masks"]:
        (OUT / a).mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)

    pairs = find_pairs()
    if args.shuffle or args.limit:
        random.Random(SEED).shuffle(pairs)
    if args.limit:
        pairs = pairs[:args.limit]
    print(f"{len(pairs)} pairs, {args.workers} workers -> {OUT}", flush=True)

    recs = []
    with ProcessPoolExecutor(args.workers) as ex:
        futs = [ex.submit(process, p) for p in pairs]
        for i, f in enumerate(as_completed(futs), 1):
            recs.append(f.result())
            if i % 25 == 0 or i == len(pairs):
                ok = sum(1 for r in recs if r["status"] == "ok")
                print(f"  {i}/{len(pairs)}  ok={ok}", flush=True)

    man = RESULTS / "pixel_corpus_manifest.csv"
    with man.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(recs[0].keys()))
        w.writeheader()
        w.writerows(sorted(recs, key=lambda r: (r["genre"], r["file"])))

    ok = [r for r in recs if r["status"] == "ok"]
    print(f"\nusable documents: {len(ok)}/{len(recs)}   "
          f"({len(ok)} per arm, {4*len(ok)} images total)")
    import collections
    print("dropped:", dict(collections.Counter(
        r["status"] for r in recs if r["status"] != "ok")))
    print("\nper genre:")
    by = collections.Counter(r["genre"] for r in ok)
    tot = collections.Counter(r["genre"] for r in recs)
    for g in sorted(tot):
        print(f"  {g:20s} {by[g]:4d}/{tot[g]:4d}")
    if ok:
        d = sorted(r["tamper_px_pct"] for r in ok)
        print(f"\ntampered-pixel density: median {d[len(d)//2]:.3f}%  "
              f"p90 {d[int(.9*len(d))]:.3f}%   "
              f"(DocForge-Bench reports 0.27-4.17% for document tampers)")
    print(f"\nmanifest: {man}")


if __name__ == "__main__":
    main()
