#!/usr/bin/env python3
"""
forge_careful_step6.py — Paper 1, STEP 6: the GRADED CAREFUL-ATTACKER arm.

This is the experiment reviewers ask for: instead of only the naive forger,
we build a re-authored forgery at FOUR attacker strengths and measure how
provenance-coherence detection degrades as the attacker gets careful. The
output is a degradation curve (via stats_step7.py) that shows exactly where
the provenance defense breaks — and why the signature layer (§4.2) is the
floor the careful attacker still cannot cross.

ATTACKER LEVELS (each defeats one more provenance signal than the last)
  L0  naive          desktop producer (Word/Canva/...) + large date gap.
                     Defeats nothing — provenance catches it (score 5).
  L1  producer-spoof edits the PDF producer/creator to mimic an INSTITUTIONAL
                     system generator (an ERP/HR/LIS string). Defeats signal A.
                     Date gap remains -> still caught by signals B (+C partial).
  L2  date-spoof     L1 PLUS sets the file CreationDate within a few days of
                     the printed date (using a metadata tool like exiftool/qpdf
                     in practice; here we write it directly). Defeats A and B.
                     Only the weak unsigned-institutional leg (C) can remain,
                     and C requires desktop authorship (A) -> so C also fails.
  L3  full-coherent  L2 PLUS a producer that is a plausible institutional
                     generator AND no residual desktop tell. Defeats A, B, C
                     entirely: at the document level this forgery is
                     indistinguishable from genuine. Provenance -> 0 detection.

THE POINT (for §5 / §7.1): provenance coherence is an anomaly detector whose
recall degrades to zero against a sufficiently careful attacker. That is not a
failure of the paper — it is the honest operating curve, and it is precisely
why the paper's ONLY proof-grade clear is the cryptographic signature layer,
which the L3 attacker still cannot forge (they hold no issuer key).

Everything is derived from step 1's genuine manifest + invented Faker filler.
NO client data. Deterministic under a fixed seed.

--------------------------------------------------------------------------
RUN (venv active, from the Research folder; needs step 1 done):

    python3 code/forge_careful_step6.py                 # 200 per level
    python3 code/forge_careful_step6.py --n-per-level 500

Then:  python3 code/stats_step7.py    # builds the degradation curve + CIs
--------------------------------------------------------------------------
"""

import argparse
import csv
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

from faker import Faker
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from pypdf import PdfWriter

RESEARCH_ROOT = Path(__file__).resolve().parent.parent
GENUINE_DIR = RESEARCH_ROOT / "corpus" / "genuine"
OUT_DIR = RESEARCH_ROOT / "corpus" / "forged_careful"
PLURAL = {"invoice": "invoices", "letter": "letters", "medical": "medicals"}

# Desktop tells (what the naive forger leaks) vs institutional generators the
# careful forger MIMICS. The institutional set mirrors step 1's genuine
# producers plus common real server-side libraries a forger could imitate.
DESKTOP_PRODUCERS = [
    ("Microsoft® Word for Microsoft 365", "Microsoft® Word for Microsoft 365"),
    ("LibreOffice 7.5", "Writer"), ("Canva", "Canva"),
    ("Skia/PDF m120", "Chromium"), ("iLovePDF", "iLovePDF"),
]
INSTITUTIONAL_PRODUCERS = [
    ("NimbusERP PDF Engine 4.2.7", "NimbusERP Billing Module"),
    ("HRDesk Document Server 3.3.1", "HRDesk Letters"),
    ("MediSys LIS Report Writer 8.1.0", "MediSys Pathology Reports"),
    ("Oracle BI Publisher 12.2.1", "Oracle BI Publisher"),
    ("iText 7.2.5", "iText"), ("JasperReports 6.20", "JasperReports Library"),
]

SERVICES = ["Annual maintenance contract", "Consulting services",
            "Cloud hosting services", "Logistics and freight charges",
            "Audit services", "Equipment rental", "Legal advisory retainer"]
INFLATION = [2, 3, 5, 10, 20, 52]

LEVELS = ["L0_naive", "L1_producer_spoof", "L2_date_spoof", "L3_full_coherent"]


def _pdf_date(d): return d.strftime("D:%Y%m%d%H%M%S+05'30'")
def _parse(s): return datetime.strptime(s, "%Y-%m-%d")
def _disp(s): return _parse(s).strftime("%d-%m-%Y")


def render_invoice(row, fake, rng, out: Path):
    fee0 = float(row["fee"]); rate = int(float(row["gst_rate"]))
    k = rng.choice(INFLATION)
    fee1 = round(fee0 * k, 2); gst1 = round(fee1 * rate / 100, 2)
    total1 = round(fee1 + gst1, 2)
    c = pdfcanvas.Canvas(str(out), pagesize=A4)
    c.setFont("Helvetica-Bold", 14); c.drawString(20*mm, 280*mm, row["issuer"])
    c.setFont("Helvetica", 8)
    c.drawString(20*mm, 275*mm, f"{fake.street_address()}, {fake.city()}")
    c.line(20*mm, 272*mm, 190*mm, 272*mm)
    c.setFont("Helvetica-Bold", 11); c.drawString(20*mm, 264*mm, "TAX INVOICE")
    c.setFont("Helvetica", 9); y = 254*mm
    for ln in [f"Invoice No: {row['ref_no']}", f"Invoice Date: {_disp(row['printed_date'])}",
               f"GSTIN: {row['gstin']}", "", f"Bill To: {row['subject']}"]:
        c.drawString(20*mm, y, ln); y -= 6*mm
    y -= 6*mm
    c.drawString(20*mm, y, rng.choice(SERVICES)); c.drawRightString(190*mm, y, f"{fee1:,.2f}"); y -= 7*mm
    c.drawString(20*mm, y, f"GST @ {rate}%"); c.drawRightString(190*mm, y, f"{gst1:,.2f}"); y -= 7*mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20*mm, y, "Total"); c.drawRightString(190*mm, y, f"{total1:,.2f}")
    c.showPage(); c.save()


def render_textdoc(row, fake, rng, out: Path, kind: str):
    new_name = fake.name()
    c = pdfcanvas.Canvas(str(out), pagesize=A4)
    title = "RELIEVING LETTER" if kind == "letter" else "HISTOPATHOLOGY REPORT"
    c.setFont("Helvetica-Bold", 14); c.drawString(20*mm, 280*mm, row["issuer"])
    c.setFont("Helvetica", 8)
    c.drawString(20*mm, 275*mm, f"{fake.street_address()}, {fake.city()}")
    c.line(20*mm, 272*mm, 190*mm, 272*mm)
    c.setFont("Helvetica-Bold", 11); c.drawString(20*mm, 264*mm, title)
    c.setFont("Helvetica", 9); y = 252*mm
    if kind == "letter":
        body = [f"Ref: {row['ref_no']}", f"Date: {_disp(row['printed_date'])}", "",
                f"To, {new_name}", "", f"Dear {new_name.split()[0]},", "",
                f"This is to certify that you were employed with {row['issuer']}.", "",
                "You have been relieved with no dues pending.", "",
                "For " + row["issuer"], "", "Authorised Signatory, Human Resources"]
    else:
        body = [f"Report No: {row['ref_no']}", f"Patient: {new_name}",
                f"Reported: {_disp(row['printed_date'])}", "",
                "MICROSCOPIC EXAMINATION:",
                "Sections show benign features; no malignancy.", "",
                "IMPRESSION:", "No evidence of malignancy.", "",
                "Electronically verified report."]
    for ln in body:
        c.drawString(20*mm, y, ln); y -= 6*mm
    c.showPage(); c.save()


def set_meta(path, producer, creator, creation, mod):
    w = PdfWriter(clone_from=str(path))
    w.add_metadata({"/Producer": producer, "/Creator": creator,
                    "/CreationDate": _pdf_date(creation), "/ModDate": _pdf_date(mod)})
    with open(path, "wb") as fh:
        w.write(fh)


def main():
    ap = argparse.ArgumentParser(description="Graded careful-attacker arm (step 6)")
    ap.add_argument("--n-per-level", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    man = GENUINE_DIR / "manifest.csv"
    if not man.exists():
        raise SystemExit("Run step 1 first.")
    rows = list(csv.DictReader(open(man)))
    by_type = {g: [r for r in rows if r["doc_type"] == g] for g in PLURAL}

    rng = random.Random(args.seed); Faker.seed(args.seed); fake = Faker("en_IN")
    for lvl in LEVELS:
        for p in PLURAL.values():
            (OUT_DIR / lvl / p).mkdir(parents=True, exist_ok=True)

    records = []
    for lvl in LEVELS:
        for i in range(args.n_per_level):
            genre = rng.choice(list(PLURAL))
            row = rng.choice(by_type[genre])
            plural = PLURAL[genre]
            printed = _parse(row["printed_date"])
            stem = Path(row["filename"]).stem
            out = OUT_DIR / lvl / plural / f"{stem}_{lvl}_{i:04d}.pdf"

            if genre == "invoice":
                render_invoice(row, fake, rng, out)
            else:
                render_textdoc(row, fake, rng, out, genre)

            # metadata per attacker level
            if lvl == "L0_naive":
                prod, creator = rng.choice(DESKTOP_PRODUCERS)
                gap = rng.randint(60, 300)
            elif lvl == "L1_producer_spoof":
                prod, creator = rng.choice(INSTITUTIONAL_PRODUCERS)  # spoof A
                gap = rng.randint(60, 300)                            # gap remains
            elif lvl == "L2_date_spoof":
                prod, creator = rng.choice(DESKTOP_PRODUCERS)         # still desktop
                gap = rng.randint(0, 30)                              # spoof B
            else:  # L3_full_coherent
                prod, creator = rng.choice(INSTITUTIONAL_PRODUCERS)  # spoof A
                gap = rng.randint(0, 20)                              # spoof B
            birth = printed + timedelta(days=gap)
            set_meta(out, prod, creator, birth, birth)

            records.append({"level": lvl, "doc_type": genre,
                            "file": f"forged_careful/{lvl}/{plural}/{out.name}",
                            "producer": prod, "printed_date": row["printed_date"],
                            "file_creation_date": f"{birth:%Y-%m-%d}", "gap_days": gap})

    mpath = RESEARCH_ROOT / "corpus" / "forged_careful_manifest.csv"
    with open(mpath, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(records[0].keys()))
        w.writeheader(); w.writerows(records)

    print(f"\nManufactured {len(records)} careful-attacker forgeries "
          f"({args.n_per_level} per level x {len(LEVELS)} levels)")
    for lvl in LEVELS:
        print(f"  {lvl}: {sum(1 for r in records if r['level']==lvl)}")
    print(f"Manifest: {mpath}")
    print("\nNext: python3 code/stats_step7.py   (degradation curve + CIs)")


if __name__ == "__main__":
    main()
