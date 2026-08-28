# Four-arm re-authored pixel corpus

Builds the corpus that measures the re-authoring blind spot at a defensible n.
CPU-only (tesseract + Chrome layout). The GPU scoring is done by the existing
Forgery models; this bundle does not touch them.

## System deps
    apt-get install -y tesseract-ocr chromium poppler-utils
    pip install -r requirements.txt

## Run
    PIXEL_SRC=/path/to/jithin ./run.sh
    # optional: WORKERS=12  PIXEL_OUT=/data/pixel_corpus  CHROME_BIN=/usr/bin/chromium

## Output layout (feed this to the existing scorer)
    pixel_corpus/
      original/    <stem>.jpg   arm A  genuine, untouched          (POSITIVE label 0)
      edited/      <stem>.jpg   arm B  fraudulent, edit residue    (POSITIVE label 1)
      reauthored/  <stem>.jpg   arm C  fraudulent, no residue      (the claim)
      reauth_ctrl/ <stem>.jpg   arm D  genuine, no residue         (control for C)
      masks/       <stem>.npz   ground-truth tamper mask (uint8), key "mask"
    results/pixel_corpus_manifest.csv   status, genre, tamper density per stem

All four arms are grayscale JPEG at one quality and identical dimensions, so no
model can separate them on format. The <stem> is shared across arms, so a scorer
iterates each directory and joins on filename.

## What to compute from the scores
    B vs A : positive control. AUC near 1 expected. If low, the model is broken
             and the negative result below is meaningless.
    C vs D : the result. Same render pipeline, one fraudulent, one not. AUC near
             0.5 means the re-authored forgery is undetectable in pixels.
    Report per-arm mean score, C-vs-D AUC with 95% CI, and rule-of-three bound
    on detections in arm C at the operating threshold.
