#!/usr/bin/env python3
"""
forge_corpus_step2.py — Paper 1, corpus build, STEP 2 of the pipeline.

Takes a reproducible SUBSET of the genuine documents from step 1 and
manufactures FORGERIES of two kinds — the two "shapes" of document fraud
the paper is built around:

  corpus/forged_reauthored/   SHAPE 2 (the paper's contribution)
      The document is REBUILT from scratch as a fresh PDF with fraudulent
      content and DESKTOP-AUTHORING provenance:
        * producer/creator = a desktop tool (Word / LibreOffice / Canva /
          Chromium print-to-PDF / iLovePDF) — NOT a system generator
        * file-birth date (CreationDate) is gapped far from the date the
          document still PRINTS on its face (backdated document, made later)
      Content fraud by genre:
        * invoice  — RECONCILING inflation: fee, GST and total all scaled
          by the same factor, so the arithmetic still balances AND the GST
          rate stays a legal slab (invisible to any arithmetic check)
        * letter   — identity swap: the relieving letter is reissued under
          a different employee name
        * medical  — identity swap: a benign report reassigned to a
          different patient name
      A re-authored file has ONE clean revision, consistent fonts, coherent
      internal metadata — nothing an edit-trace check can see. That is the
      whole point.

  corpus/forged_inplace/      SHAPE 1 (the contrast / control)
      The genuine file is ACTUALLY EDITED in place: its producer is
      rewritten to a PDF-EDITOR string and its ModDate set long after its
      CreationDate. This leaves a real editing TRACE (editor producer +
      creation/modification incoherence) — the kind of signal edit-trace
      forensics is designed to catch. It exists so §3 can show, on our own
      releasable data, that edit-trace forensics work on shape 1 and fail
      on shape 2.

  corpus/forged_manifest.csv  Ground truth for every forgery: source file,
      forged file, shape, attack, exactly what changed (original -> forged),
      producer, printed date, file-birth date, printed-vs-birth gap.

Everything is derived from step 1's manifest + invented Faker filler; NO
client data, fully releasable. Deterministic: same --seed => same forgeries.

--------------------------------------------------------------------------
RUN (venv active, from the Research folder):

    source ~/research_venv/bin/activate
    cd ~/Desktop/Research
    python3 code/forge_corpus_step2.py                 # 25 of each type
    python3 code/forge_corpus_step2.py --n-per-type 40 # more forgeries

PREREQUISITE: step 1 must have been run (corpus/genuine/manifest.csv exists).

WHAT TO TEST — your acceptance checklist:
  1. corpus/forged_reauthored/{invoices,letters,medicals}/ and
     corpus/forged_inplace/{...}/ contain PDFs.
  2. Open a re-authored invoice: the amount is inflated, but fee + GST still
     = total and the GST % is still a legal slab (5/12/18/28). This is the
     "arithmetic can't catch it" case — see it with your own eyes.
  3. Check that re-authored PDF's metadata (command below): /Producer is a
     desktop tool and /CreationDate is FAR from the printed date.
        python3 -c "from pypdf import PdfReader; \
          print(PdfReader('corpus/forged_reauthored/invoices/<file>').metadata)"
  4. Open a re-authored letter/medical: the name differs from its source in
     corpus/genuine/ (check forged_manifest.csv for the source pairing).
  5. Check an in-place forgery's metadata: /Producer is now a PDF EDITOR and
     /ModDate is much later than /CreationDate (the edit trace).
  6. forged_manifest.csv: one row per forgery, original_value != forged_value,
     and the source_file column points at a real file in corpus/genuine/.
  7. Re-run with the same seed -> identical forged_manifest.csv.
--------------------------------------------------------------------------
"""

import argparse
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

from faker import Faker
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from pypdf import PdfReader, PdfWriter

RESEARCH_ROOT = Path(__file__).resolve().parent.parent
GENUINE_DIR = RESEARCH_ROOT / "corpus" / "genuine"
REAUTH_DIR = RESEARCH_ROOT / "corpus" / "forged_reauthored"
INPLACE_DIR = RESEARCH_ROOT / "corpus" / "forged_inplace"

PLURAL = {"invoice": "invoices", "letter": "letters", "medical": "medicals"}

# Desktop authoring / conversion tools — what a re-authored document is
# actually produced by. None is a legitimate institutional system generator.
DESKTOP_PRODUCERS = [
    ("Microsoft® Word for Microsoft 365", "Microsoft® Word for Microsoft 365"),
    ("LibreOffice 7.5", "Writer"),
    ("Canva", "Canva"),
    ("Skia/PDF m120", "Chromium"),          # "print to PDF" from a browser
    ("iLovePDF", "iLovePDF"),
    ("Microsoft: Print To PDF", "Microsoft: Print To PDF"),
]

# PDF editors — what an IN-PLACE edit of a genuine file passes through.
EDITOR_PRODUCERS = [
    "PDF-XChange Editor", "iLovePDF", "Foxit PhantomPDF",
    "Adobe Acrobat Pro DC", "Nitro PDF",
]

SERVICES = [
    "Annual maintenance contract", "Software licence subscription",
    "Consulting services", "Logistics and freight charges", "Equipment rental",
    "Facility management services", "Cloud hosting services", "Audit services",
    "Legal advisory retainer", "Recruitment consultancy",
]
DESIGNATIONS = ["Software Engineer", "Accounts Executive", "Sales Manager",
                "HR Executive", "Operations Analyst", "Senior Consultant",
                "Business Analyst", "Project Lead"]
SPECIMENS = ["kidney (needle biopsy)", "liver (needle biopsy)",
             "thyroid nodule (FNAC)", "breast lump, right (core biopsy)",
             "cervical lymph node (excision)", "colonic polyp (colonoscopic biopsy)"]
MICRO = [
    "Sections show preserved architecture with no evidence of malignancy.",
    "Mild chronic inflammatory infiltrate noted; no dysplasia identified.",
    "Features are consistent with a benign lesion; margins are clear.",
    "Reactive changes present; no evidence of neoplasia.",
]
IMPRESSIONS = [
    "No evidence of malignancy in the material examined.",
    "Benign histological features; no further action indicated.",
    "Findings consistent with a benign lesion.",
]

INFLATION_FACTORS = [2, 3, 5, 10, 20, 52]   # incl. the red-team ~52x case


def _pdf_date(d: datetime) -> str:
    return d.strftime("D:%Y%m%d%H%M%S+05'30'")


def set_metadata(path: Path, producer: str, creator: str,
                 creation: datetime, mod: datetime) -> None:
    writer = PdfWriter(clone_from=str(path))
    writer.add_metadata({
        "/Producer": producer, "/Creator": creator,
        "/CreationDate": _pdf_date(creation), "/ModDate": _pdf_date(mod),
    })
    with open(path, "wb") as fh:
        writer.write(fh)


def header(c, org, addr, title):
    c.setFont("Helvetica-Bold", 14); c.drawString(20*mm, 280*mm, org)
    c.setFont("Helvetica", 8);       c.drawString(20*mm, 275*mm, addr)
    c.line(20*mm, 272*mm, 190*mm, 272*mm)
    c.setFont("Helvetica-Bold", 11); c.drawString(20*mm, 264*mm, title)


# --------------------------------------------------------------------------
# helpers for dates stored as YYYY-MM-DD in the manifest
# --------------------------------------------------------------------------

def _parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")

def _disp(s: str) -> str:
    return _parse(s).strftime("%d-%m-%Y")


# --------------------------------------------------------------------------
# SHAPE 2 — re-authored forgeries (fresh fraudulent PDF, desktop provenance)
# --------------------------------------------------------------------------

def reauthor_invoice(row, fake, rng, out: Path):
    fee0 = float(row["fee"]); rate = int(float(row["gst_rate"]))
    total0 = float(row["total"])
    k = rng.choice(INFLATION_FACTORS)
    fee1 = round(fee0 * k, 2); gst1 = round(fee1 * rate / 100, 2)
    total1 = round(fee1 + gst1, 2)
    addr = f"{fake.street_address()}, {fake.city()}"
    service = rng.choice(SERVICES)

    c = pdfcanvas.Canvas(str(out), pagesize=A4)
    header(c, row["issuer"], addr, "TAX INVOICE")
    c.setFont("Helvetica", 9); y = 254*mm
    for line in [f"Invoice No: {row['ref_no']}", f"Invoice Date: {_disp(row['printed_date'])}",
                 f"GSTIN: {row['gstin']}", "", f"Bill To: {row['subject']}"]:
        c.drawString(20*mm, y, line); y -= 6*mm
    y -= 6*mm; c.setFont("Helvetica", 9)
    c.drawString(20*mm, y, service);          c.drawRightString(190*mm, y, f"{fee1:,.2f}"); y -= 7*mm
    c.drawString(20*mm, y, f"GST @ {rate}%"); c.drawRightString(190*mm, y, f"{gst1:,.2f}"); y -= 7*mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20*mm, y, "Total");          c.drawRightString(190*mm, y, f"{total1:,.2f}")
    c.showPage(); c.save()
    return {"attack": "amount_inflation_reconciling",
            "field_changed": "fee/total",
            "original_value": f"fee {fee0:,.2f} / total {total0:,.2f}",
            "forged_value": f"fee {fee1:,.2f} / total {total1:,.2f} (x{k}, GST@{rate}% still reconciles)"}


def reauthor_letter(row, fake, rng, out: Path):
    orig_name = row["subject"]
    new_name = fake.name()
    while new_name == orig_name:
        new_name = fake.name()
    desig = rng.choice(DESIGNATIONS)
    relieved = _parse(row["printed_date"])
    joined = relieved - timedelta(days=rng.randint(300, 2000))

    c = pdfcanvas.Canvas(str(out), pagesize=A4)
    header(c, row["issuer"], f"{fake.street_address()}, {fake.city()}", "RELIEVING LETTER")
    c.setFont("Helvetica", 9); y = 252*mm
    for line in [f"Ref: {row['ref_no']}", f"Date: {_disp(row['printed_date'])}", "",
                 f"To, {new_name}", "", f"Dear {new_name.split()[0]},", "",
                 f"This is to certify that you were employed with {row['issuer']} as",
                 f"{desig} from {joined:%d-%m-%Y} to {relieved:%d-%m-%Y}.", "",
                 "You have been relieved from your duties and have no dues pending.", "",
                 "For " + row["issuer"], "", "", "Authorised Signatory, Human Resources"]:
        c.drawString(20*mm, y, line); y -= 6*mm
    c.showPage(); c.save()
    return {"attack": "identity_swap",
            "field_changed": "employee_name",
            "original_value": orig_name, "forged_value": new_name}


def reauthor_medical(row, fake, rng, out: Path):
    orig_name = row["subject"]
    new_name = fake.name()
    while new_name == orig_name:
        new_name = fake.name()
    age = rng.randint(23, 78); specimen = rng.choice(SPECIMENS)
    reported = _parse(row["printed_date"]); collected = reported - timedelta(days=rng.randint(2, 6))

    c = pdfcanvas.Canvas(str(out), pagesize=A4)
    header(c, row["issuer"], f"{fake.street_address()}, {fake.city()}", "HISTOPATHOLOGY REPORT")
    c.setFont("Helvetica", 9); y = 252*mm
    for line in [f"Report No: {row['ref_no']}", f"Patient: {new_name}    Age/Sex: {age}",
                 f"Specimen: {specimen}",
                 f"Collected: {collected:%d-%m-%Y}    Reported: {reported:%d-%m-%Y}", "",
                 "MICROSCOPIC EXAMINATION:", rng.choice(MICRO), "",
                 "IMPRESSION:", rng.choice(IMPRESSIONS), "",
                 "Electronically verified report."]:
        c.drawString(20*mm, y, line); y -= 6*mm
    c.showPage(); c.save()
    return {"attack": "identity_swap",
            "field_changed": "patient_name",
            "original_value": orig_name, "forged_value": new_name}


REAUTHOR = {"invoice": reauthor_invoice, "letter": reauthor_letter, "medical": reauthor_medical}


# --------------------------------------------------------------------------
# SHAPE 1 — in-place edit of the genuine file (leaves an edit trace)
# --------------------------------------------------------------------------

def inplace_edit(src: Path, out: Path, rng, printed: datetime, orig_producer: str):
    """Edit the genuine file in place: rewrite producer to a PDF editor and
    push ModDate long after CreationDate — a real metadata edit trace."""
    editor = rng.choice(EDITOR_PRODUCERS)
    mod = printed + timedelta(days=rng.randint(120, 500))
    writer = PdfWriter(clone_from=str(src))
    writer.add_metadata({
        "/Producer": editor, "/Creator": editor,
        "/CreationDate": _pdf_date(printed),     # original issuance kept
        "/ModDate": _pdf_date(mod),              # edited long afterwards
    })
    with open(out, "wb") as fh:
        writer.write(fh)
    return {"attack": "inplace_metadata_edit", "field_changed": "producer/moddate",
            "original_value": orig_producer,
            "forged_value": f"{editor}; ModDate +{(mod - printed).days}d after creation",
            "producer": editor, "file_creation_date": f"{printed:%Y-%m-%d}",
            "mod": mod}


def main():
    ap = argparse.ArgumentParser(description="Manufacture forgeries (step 2)")
    ap.add_argument("--n-per-type", type=int, default=25,
                    help="how many genuine docs of each type to attack")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    man = GENUINE_DIR / "manifest.csv"
    if not man.exists():
        raise SystemExit(f"Genuine manifest not found: {man}\nRun gen_corpus_step1.py first.")

    rows = list(csv.DictReader(open(man)))
    by_type = {g: [r for r in rows if r["doc_type"] == g] for g in PLURAL}

    rng = random.Random(args.seed)
    Faker.seed(args.seed)
    fake = Faker("en_IN")

    for base in (REAUTH_DIR, INPLACE_DIR):
        for p in PLURAL.values():
            (base / p).mkdir(parents=True, exist_ok=True)

    records = []
    for genre, plural in PLURAL.items():
        pool = sorted(by_type[genre], key=lambda r: r["filename"])
        n = min(args.n_per_type, len(pool))
        targets = rng.sample(pool, n)
        for row in targets:
            printed = _parse(row["printed_date"])
            stem = Path(row["filename"]).stem

            # SHAPE 2 — re-authored
            r_out = REAUTH_DIR / plural / f"{stem}_reauthored.pdf"
            info = REAUTHOR[genre](row, fake, rng, r_out)
            gap = rng.randint(60, 300)
            birth = printed + timedelta(days=gap)
            prod, creator = rng.choice(DESKTOP_PRODUCERS)
            set_metadata(r_out, prod, creator, birth, birth)
            records.append({
                "source_file": f"genuine/{plural}/{row['filename']}",
                "forged_file": f"forged_reauthored/{plural}/{r_out.name}",
                "shape": "reauthored", "doc_type": genre, **info,
                "producer": prod, "printed_date": row["printed_date"],
                "file_creation_date": f"{birth:%Y-%m-%d}", "gap_days": gap,
            })

            # SHAPE 1 — in-place edit
            src = GENUINE_DIR / plural / row["filename"]
            i_out = INPLACE_DIR / plural / f"{stem}_inplace.pdf"
            iinfo = inplace_edit(src, i_out, rng, printed, row["producer"])
            gap1 = (iinfo.pop("mod") - printed).days
            records.append({
                "source_file": f"genuine/{plural}/{row['filename']}",
                "forged_file": f"forged_inplace/{plural}/{i_out.name}",
                "shape": "inplace", "doc_type": genre, **iinfo,
                "printed_date": row["printed_date"], "gap_days": gap1,
            })

    cols = ["source_file", "forged_file", "shape", "doc_type", "attack",
            "field_changed", "original_value", "forged_value", "producer",
            "printed_date", "file_creation_date", "gap_days"]
    mpath = RESEARCH_ROOT / "corpus" / "forged_manifest.csv"
    with open(mpath, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(records)

    n_re = sum(1 for r in records if r["shape"] == "reauthored")
    n_in = sum(1 for r in records if r["shape"] == "inplace")
    print(f"\nManufactured {len(records)} forgeries: {n_re} re-authored (shape 2), "
          f"{n_in} in-place (shape 1)")
    print(f"Re-authored: {REAUTH_DIR}")
    print(f"In-place:    {INPLACE_DIR}")
    print(f"Manifest:    {mpath}")
    print("\nNow run YOUR acceptance checklist (docstring at the top of this file).")


if __name__ == "__main__":
    main()
