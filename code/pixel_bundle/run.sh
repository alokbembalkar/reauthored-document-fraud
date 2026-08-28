#!/bin/bash
# Build the four-arm re-authored pixel corpus. CPU-only: tesseract OCR + Chrome
# page rendering. No GPU, no .pt files, nothing from the Forgery scorer.
# System deps (Debian/Ubuntu):  apt-get install -y tesseract-ocr chromium poppler-utils
set -e
export PIXEL_SRC="${PIXEL_SRC:?set PIXEL_SRC to the jithin folder (has OG/ and Edit/)}"
export PIXEL_OUT="${PIXEL_OUT:-$PWD/pixel_corpus}"
export PIXEL_RESULTS="${PIXEL_RESULTS:-$PWD/results}"
export CHROME_BIN="${CHROME_BIN:-$(command -v chromium || command -v chromium-browser || command -v google-chrome)}"
python3 reauthor_pixel_corpus_step14.py --workers "${WORKERS:-6}" "$@"
