#!/usr/bin/env python3
"""
Diagnostic — did our q92 normalisation kill the positive control?

Step 15's positive control (edited vs original) came back at AUC 0.504 on the
normalised corpus: chance. Either the V5 model does not transfer to 1990s
RVL-CDIP scans, or step 14's uniform JPEG q92 re-encode destroyed the residue
the model reads. Those have opposite consequences for the paper, so score the
same documents in their native form (OG .jpg / Edit .png, untouched) and
compare the two separations on the same files.

Small by design: 40 pairs, chosen as the largest tampers in the corpus, so if
the model can see anything it should see these.
"""
import csv, sys, time
from pathlib import Path

sys.argv = [sys.argv[0]]                       # step15 parses argv at main()
import score_pixel_corpus_step15 as S          # noqa: E402  (does os.chdir)

JITHIN = Path.home() / "Desktop" / "Forgery" / "Data" / "jithin"
MANIFEST = Path.home() / "Desktop" / "Research" / "results" / "pixel_corpus_manifest.csv"
OUT = Path.home() / "Desktop" / "Research" / "results" / "pixel_diag_native.csv"
N = 25

rows = [r for r in csv.DictReader(open(MANIFEST)) if r["status"] == "ok"]
rows.sort(key=lambda r: -float(r["tamper_px_pct"]))
pick = rows[:N]

dev = S.pick_device(None)    # run this alone: two Metal processes segfault both
qt = S.pickle.load(open(S.ASSETS / "qt_table.pk", "rb"))[75]
qt = S.torch.LongTensor(qt).reshape(1, 1, 8, 8).to(dev)
model = S.build(dev)
print(f"device: {dev}  scoring {len(pick)} pairs x 4 variants", flush=True)

fh = open(OUT, "w", newline="")
w = csv.writer(fh)
w.writerow(["file", "genre", "tamper_px_pct", "variant", "peak", "rawmax"])
t0 = time.time()
for i, r in enumerate(pick, 1):
    g, f = r["genre"], r["file"]
    variants = {
        "native_og":   JITHIN / "OG" / f"{g}-OG" / f"{f}.jpg",
        "native_edit": JITHIN / "Edit" / f"{g}-Edit" / f"{f}.png",
        "norm_og":     S.CORPUS / "original" / f"{f}.jpg",
        "norm_edit":   S.CORPUS / "edited" / f"{f}.jpg",
    }
    for name, p in variants.items():
        if not p.exists():
            print(f"  missing {name}: {p}", flush=True)
            continue
        bgr = S.load_bgr(p) if name.startswith("norm") else S.cv2.imread(str(p))
        if bgr is None:
            continue
        pk, raw, _ = S.peak_native(model, bgr, dev, qt)
        w.writerow([f, g, r["tamper_px_pct"], name, f"{pk:.6f}", f"{raw:.6f}"])
    fh.flush()               # a crash mid-run should not cost the rows already scored
    if i % 5 == 0:
        print(f"  {i}/{len(pick)}  {(time.time()-t0)/i:.1f}s/doc", flush=True)
print("done ->", OUT)
