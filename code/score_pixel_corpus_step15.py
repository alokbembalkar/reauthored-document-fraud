#!/usr/bin/env python3
"""
Step 15 — score the four-arm pixel corpus with the production V5 pixel model.

Faithful port of the inference path in L4_checkpoints/finetune_real.py:
same seg_dtd architecture, same checkpoint (pixel_v5_8177.pt), same
peak_native() sliding-tile scorer (512 tiles, stride 384, JPEG-75 DCT channel,
gray-in-RGB, page score = max prob over connected components >= 60 px).
Nothing is retrained and no threshold is chosen here; RECALL_THR = 0.40 is the
production operating point and is applied only at reporting time.

What the four arms mean (built by step 14):
  original    genuine scan            edited      tampered, residue present
  reauth_ctrl re-render of genuine    reauthored  re-render of the tamper

The paper needs two numbers from this: edited-vs-original (positive control:
the model must catch residue-bearing tampers here or its silence elsewhere is
meaningless) and reauthored-vs-reauth_ctrl (the claim: same fraudulent content,
residue destroyed by re-rendering).

Runs on MPS if available, else CPU. Resumable: rows already in the output CSV
are skipped, so it can be stopped and restarted freely.

Usage:
    python3 score_pixel_corpus_step15.py --limit 12          # smoke test
    python3 score_pixel_corpus_step15.py                     # full run
    python3 score_pixel_corpus_step15.py --device cpu        # numerical check
"""
import os
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")   # before torch import

import argparse
import csv
import pickle
import sys
import tempfile
import time
import types
from pathlib import Path

import numpy as np

ASSETS = Path.home() / "Desktop" / "train_bundle" / "assets"
CKPT = Path.home() / "Desktop" / "Forgery" / "L4_checkpoints" / "pixel_v5_8177.pt"
CORPUS = Path.home() / "Desktop" / "Forgery" / "Data" / "pixel_corpus"
OUTCSV = Path.home() / "Desktop" / "Research" / "results" / "pixel_scores_v5.csv"
ARMS = ["original", "edited", "reauthored", "reauth_ctrl"]
TILE, STRIDE, MAX_SIDE = 512, 384, 1024
RECALL_THR = 0.40

sys.path.insert(0, str(ASSETS))
os.chdir(ASSETS)        # dtd.py loads vph_imagenet.pt / swin_imagenet.pt by
                        # relative path, exactly as production does post-chdir
import torch, torchvision, cv2, jpegio          # noqa: E402
import timm.models.layers as L                   # noqa: E402
# older DocTamper code imports from timm.models.layers.* submodules that no
# longer exist; shim them exactly as finetune_real.py does
for _n in ["drop", "weight_init", "helpers"]:
    _m = types.ModuleType("timm.models.layers." + _n)
    for _a in ["DropPath", "trunc_normal_", "to_2tuple", "to_ntuple",
               "to_3tuple", "variance_scaling_"]:
        if hasattr(L, _a):
            setattr(_m, _a, getattr(L, _a))
    sys.modules["timm.models.layers." + _n] = _m
import swins                                     # noqa: E402
for _n in dir(swins):
    _o = getattr(swins, _n)
    if isinstance(_o, type):
        setattr(sys.modules["__main__"], _n, _o)
# dtd.py imports training losses at module level; they are never used in
# inference, so satisfy the import with a stub when the module is absent
try:
    import losses                                # noqa: F401
except ModuleNotFoundError:
    _l = types.ModuleType("losses")
    for _cls in ["DiceLoss", "FocalLoss", "SoftCrossEntropyLoss", "LovaszLoss"]:
        setattr(_l, _cls, type(_cls, (), {}))
    sys.modules["losses"] = _l
from dtd import seg_dtd                          # noqa: E402

NORM = torchvision.transforms.Normalize(mean=(0.485, 0.455, 0.406),
                                        std=(0.229, 0.224, 0.225))


def pick_device(arg):
    if arg:
        return torch.device(arg)
    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


def build(dev):
    md = seg_dtd("", 2)
    ck = torch.load(CKPT, map_location="cpu", weights_only=False)
    sd = ck.get("model", ck.get("state_dict", ck)) if isinstance(ck, dict) else ck
    sd = {k.replace("module.", ""): v for k, v in sd.items()}
    res = md.load_state_dict(sd, strict=False)
    n_model = len(md.state_dict())
    n_missing = len(res.missing_keys)
    # strict=False will happily load nothing; refuse to score with a random net
    if n_missing > n_model * 0.05:
        raise SystemExit(f"checkpoint mismatch: {n_missing}/{n_model} params "
                         f"missing, {len(res.unexpected_keys)} unexpected — "
                         "wrong architecture or key prefix")
    print(f"loaded {n_model - n_missing}/{n_model} params "
          f"({len(res.unexpected_keys)} unexpected)", flush=True)
    for mm in md.modules():
        if isinstance(mm, torch.nn.GELU) and not hasattr(mm, "approximate"):
            mm.approximate = "none"
        if mm.__class__.__name__ == "DropPath":
            if not hasattr(mm, "scale_by_keep"):
                mm.scale_by_keep = True
            if not hasattr(mm, "drop_prob"):
                mm.drop_prob = 0.0
    return md.to(dev).eval()


def dct_of(gray):
    with tempfile.NamedTemporaryFile(suffix=".jpg") as t:
        cv2.imwrite(t.name, gray, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return np.clip(np.abs(jpegio.read(t.name).coef_arrays[0]), 0, 20).astype(np.int64)


def load_bgr(p):
    """norm_fmt(JPEG q90) as in production. Our corpus is already uniform JPEG
    q92, but keeping the step means the pipeline is byte-for-byte the deployed
    one, and no reviewer can attribute the result to a preprocessing delta."""
    im = cv2.imread(str(p))
    if im is None:
        return None
    ok, buf = cv2.imencode(".jpg", im, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else im


@torch.no_grad()
def peak_native(model, bgr, dev, qt):
    if max(bgr.shape[:2]) > MAX_SIDE:
        s = MAX_SIDE / max(bgr.shape[:2])
        bgr = cv2.resize(bgr, None, fx=s, fy=s)
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    rgb = cv2.cvtColor(g, cv2.COLOR_GRAY2RGB)
    H, W = g.shape
    H8, W8 = (H + 7) // 8 * 8, (W + 7) // 8 * 8
    gp = np.zeros((H8, W8), np.uint8); gp[:H, :W] = g
    dct = np.zeros((H8, W8), np.int64)
    d = dct_of(gp); dct[:d.shape[0], :d.shape[1]] = d
    rp = np.zeros((H8, W8, 3), np.uint8); rp[:H, :W] = rgb
    acc = np.zeros((H8, W8), np.float32); cnt = np.zeros((H8, W8), np.float32)
    ys = sorted(set(list(range(0, max(1, H8 - TILE + 1), STRIDE)) + [max(0, H8 - TILE)]))
    xs = sorted(set(list(range(0, max(1, W8 - TILE + 1), STRIDE)) + [max(0, W8 - TILE)]))
    for y in ys:
        for x in xs:
            rr = np.zeros((TILE, TILE, 3), np.uint8)
            dd = np.zeros((TILE, TILE), np.int64)
            rc = rp[y:y + TILE, x:x + TILE]; dc = dct[y:y + TILE, x:x + TILE]
            rr[:rc.shape[0], :rc.shape[1]] = rc
            dd[:dc.shape[0], :dc.shape[1]] = dc
            out = model(NORM(torch.from_numpy(rr).permute(2, 0, 1).float() / 255)[None].to(dev),
                        torch.from_numpy(dd)[None].to(dev), qt)
            p = torch.softmax(out.float(), 1)[0, 1].cpu().numpy()
            acc[y:y + TILE, x:x + TILE] += p[:rc.shape[0], :rc.shape[1]]
            cnt[y:y + TILE, x:x + TILE] += 1
    prob = (acc / np.maximum(cnt, 1))[:H, :W]
    binm = (prob > 0.5).astype(np.uint8)
    nn, lab, st, _ = cv2.connectedComponentsWithStats(binm, 8)
    pk = 0.0
    for i in range(1, nn):
        if st[i][4] >= 60:
            pk = max(pk, float(prob[lab == i].max()))
    # raw page max as a secondary score: AUC from it needs no area gate, so the
    # comparison cannot hinge on the 60-px component rule
    return pk, float(prob.max()), float(prob.mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stems to score (0=all)")
    ap.add_argument("--device", default="", help="mps / cpu (default: auto)")
    args = ap.parse_args()

    dev = pick_device(args.device)
    print(f"device: {dev}", flush=True)
    qt = pickle.load(open(ASSETS / "qt_table.pk", "rb"))[75]
    qt = torch.LongTensor(qt).reshape(1, 1, 8, 8).to(dev)
    model = build(dev)

    stems = sorted(p.stem for p in (CORPUS / "original").glob("*.jpg"))
    if args.limit:
        stems = stems[:args.limit]

    done = set()
    if OUTCSV.exists():
        with open(OUTCSV) as fh:
            done = {(r["file"], r["arm"]) for r in csv.DictReader(fh)}
        print(f"resuming: {len(done)} rows already scored", flush=True)
    new_file = not OUTCSV.exists()
    out = open(OUTCSV, "a", newline="")
    w = csv.writer(out)
    if new_file:
        w.writerow(["file", "arm", "peak", "rawmax", "meanprob", "secs"])

    t0 = time.time()
    n_done = 0
    total = sum(1 for s in stems for a in ARMS if (s, a) not in done)
    for stem in stems:                       # all four arms per stem, so a
        for arm in ARMS:                     # partial run stays balanced
            if (stem, arm) in done:
                continue
            p = CORPUS / arm / f"{stem}.jpg"
            if not p.exists():
                continue
            bgr = load_bgr(p)
            if bgr is None:
                continue
            t1 = time.time()
            pk, mx, mn = peak_native(model, bgr, dev, qt)
            w.writerow([stem, arm, f"{pk:.4f}", f"{mx:.4f}", f"{mn:.6f}",
                        f"{time.time() - t1:.1f}"])
            n_done += 1
            if n_done % 40 == 0:
                out.flush()
                el = time.time() - t0
                print(f"  {n_done}/{total}  {el/n_done:.1f}s/img  "
                      f"eta {(total - n_done) * el / n_done / 3600:.1f}h", flush=True)
    out.close()

    # summary at the production threshold
    with open(OUTCSV) as fh:
        rows = list(csv.DictReader(fh))
    print()
    for arm in ARMS:
        s = [float(r["peak"]) for r in rows if r["arm"] == arm]
        if not s:
            continue
        hit = sum(1 for x in s if x >= RECALL_THR)
        print(f"{arm:12s} n={len(s):5d}  detected@{RECALL_THR} {hit:5d} "
              f"({100 * hit / len(s):5.1f}%)  median peak {sorted(s)[len(s)//2]:.3f}")


if __name__ == "__main__":
    main()
