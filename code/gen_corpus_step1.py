#!/usr/bin/env python3
"""
gen_corpus_step1.py — Paper 1, corpus build, STEP 1 of the pipeline.

Generates the GENUINE arm of the synthetic evaluation corpus:
born-digital PDFs that simulate SYSTEM-GENERATED institutional documents
(the way a real ERP / HR system / hospital LIS produces them), with:

  - 100% invented identities (Faker library, Indian locale) — no real people,
    no client data, nothing scraped. Fully releasable.
  - COHERENT PROVENANCE, like real system output:
      * PDF CreationDate metadata == the date printed on the document
        (a genuine system generates the file at issuance)
      * Producer/Creator = a synthetic "system generator" string
        (documented in the manifest; the paper's corpus-construction
        section states this simulation choice openly)
  - Reconciling arithmetic on invoices (fee + GST at a legal slab = total)
  - Valid-checksum GSTINs (public checksum algorithm, reimplemented here)
  - A ground-truth manifest.csv recording every generated field
  - Deterministic output: same --seed => byte-comparable corpus content

STEP 2 (next file) will attack a subset of these documents by re-authoring
them (the shape-2 attack of the paper) and by in-place edits (shape 1).

--------------------------------------------------------------------------
SETUP (one time), from the Research folder in Terminal / VS Code terminal:

    python3 -m venv research_venv
    source research_venv/bin/activate
    pip install faker reportlab pypdf

RUN:

    python3 code/gen_corpus_step1.py            # defaults: 12+9+9 docs, seed 42
    python3 code/gen_corpus_step1.py --seed 7   # a different (but reproducible) corpus

WHAT TO TEST after running — your acceptance checklist:
  1. corpus/genuine/ contains invoices/ letters/ medical/ with PDFs.
  2. Open several PDFs: do they look like PLAUSIBLE genuine documents
     (boring, clean, system-generated — not obviously fake)?
  3. Pick one invoice: check fee + GST = total exactly, and GST is a legal
     slab % of the fee.
  4. In Finder: Get Info on a PDF -> "Created" is TODAY (that's the
     filesystem date and is fine). The PDF-INTERNAL CreationDate is the
     printed date — verify with:  research_venv/bin/python3 -c
     "from pypdf import PdfReader; import sys;
      print(PdfReader(sys.argv[1]).metadata)" <path-to-a-pdf>
     -> /CreationDate should match the date printed in the document.
  5. manifest.csv row count == number of PDFs, and spot-check 2-3 rows
     against their PDFs.
  6. Run twice with the same seed -> identical manifest.csv both times.
--------------------------------------------------------------------------
"""

import argparse
import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from faker import Faker
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from pypdf import PdfReader, PdfWriter

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RESEARCH_ROOT = Path(__file__).resolve().parent.parent   # .../Research
DEFAULT_OUT = RESEARCH_ROOT / "corpus" / "genuine"

# Synthetic "system generator" identities per genre. These SIMULATE the
# producer strings real ERP/report systems write. Invented names — they
# match no real product, and the manifest + paper document the simulation.
GENERATORS = {
    "invoice": {"producer": "NimbusERP PDF Engine 4.2.7",
                "creator":  "NimbusERP Billing Module"},
    "letter":  {"producer": "HRDesk Document Server 3.3.1",
                "creator":  "HRDesk Letters"},
    "medical": {"producer": "MediSys LIS Report Writer 8.1.0",
                "creator":  "MediSys Pathology Reports"},
}

GST_SLABS = [5, 12, 18, 28]          # legal Indian GST slabs used on invoices
STATE_CODES = {                       # public GST state codes (subset)
    "27": "Maharashtra", "07": "Delhi", "29": "Karnataka",
    "33": "Tamil Nadu", "06": "Haryana", "24": "Gujarat",
    "19": "West Bengal", "36": "Telangana",
}

SERVICES = [
    "Annual maintenance contract", "Software licence subscription",
    "Consulting services", "Logistics and freight charges",
    "Equipment rental", "Facility management services",
    "Digital marketing retainer", "Security services",
    "Cloud hosting services", "Professional training programme",
    "Audit and assurance services", "Legal advisory retainer",
    "Office stationery supply", "Courier and dispatch services",
    "Housekeeping services", "Catering services",
    "Printing and binding", "Website development",
    "Data entry services", "Recruitment consultancy",
    "Machinery spare parts", "Electrical installation work",
    "Air-conditioning maintenance", "Pest control services",
    "Furniture supply", "Vehicle hire charges",
    "Telecom and internet charges", "Insurance premium (group)",
    "Warehousing charges", "Packaging material supply",
    "Civil repair work", "Landscaping and gardening",
    "Event management services", "Photography services",
    "Translation services", "Calibration services",
]

DESIGNATIONS = ["Software Engineer", "Accounts Executive", "Sales Manager",
                "HR Executive", "Operations Analyst", "Senior Consultant",
                "Marketing Manager", "Business Analyst", "Project Lead",
                "Quality Engineer", "Customer Support Executive", "Finance Manager",
                "Administration Officer", "Procurement Specialist", "Data Scientist",
                "Network Administrator", "Content Writer", "Graphic Designer",
                "Logistics Coordinator", "Legal Associate", "Research Associate",
                "Product Manager", "Field Sales Officer", "Store Supervisor"]

SPECIMENS = ["kidney (needle biopsy)", "liver (needle biopsy)",
             "skin lesion, left forearm (excision)", "gastric antrum (endoscopic biopsy)",
             "thyroid nodule (FNAC)", "breast lump, right (core biopsy)",
             "cervical lymph node (excision)", "colonic polyp (colonoscopic biopsy)",
             "prostate (TRUS biopsy)", "endometrium (curettage)",
             "lung (CT-guided biopsy)", "bone marrow (trephine biopsy)",
             "salivary gland (FNAC)", "oral mucosa, buccal (incisional biopsy)",
             "gallbladder (cholecystectomy specimen)", "uterine cervix (punch biopsy)",
             "soft tissue, thigh (excision)", "nasal polyp (endoscopic excision)"]

MICRO_FINDINGS = [
    "Sections show preserved architecture with no evidence of granuloma or malignancy.",
    "Mild chronic inflammatory infiltrate noted; no dysplasia identified.",
    "Features are consistent with a benign lesion; margins are clear.",
    "No atypical cells seen in the examined material.",
    "Reactive changes present; no evidence of neoplasia.",
    "Fibrocollagenous tissue with focal chronic inflammation; no malignancy.",
    "Sections show unremarkable tissue with preserved cytoarchitecture.",
    "Mild oedema and congestion noted; no atypia identified.",
    "Benign glandular tissue with no significant pathological change.",
    "Focal reactive lymphoid hyperplasia; no granuloma or malignancy seen.",
    "Sections reveal normal histological features for the site.",
    "Chronic non-specific inflammation; special stains negative for organisms.",
    "Scattered foamy macrophages noted; no evidence of malignancy.",
    "Well-differentiated benign tissue; resection margins uninvolved.",
    "No epithelial atypia or invasive component identified.",
]

IMPRESSIONS = [
    "No evidence of malignancy in the material examined.",
    "Benign histological features; no further action indicated on this specimen.",
    "Chronic inflammatory changes; clinical correlation advised.",
    "Reactive process; no evidence of neoplasm.",
    "Findings consistent with a benign lesion.",
    "No dysplasia or malignancy detected in the examined sections.",
]

# ---------------------------------------------------------------------------
# GSTIN with a VALID public checksum
# (standard base-36 Luhn-variant used by GSTN; public algorithm)
# ---------------------------------------------------------------------------

_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        v = _B36.index(ch)
        factor = 2 if i % 2 else 1          # double every 2nd char (0-based odd)
        prod = v * factor
        total += prod // 36 + prod % 36
    return _B36[(36 - total % 36) % 36]

def make_gstin(rng: random.Random, state_code: str) -> str:
    # embedded PAN: 5 letters + 4 digits + 1 letter; then entity '1', then 'Z'
    pan = ("".join(rng.choices(_B36[10:], k=5))
           + "".join(rng.choices(_B36[:10], k=4))
           + rng.choice(_B36[10:]))
    first14 = f"{state_code}{pan}1Z"
    return first14 + gstin_check_char(first14)

# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def _pdf_date(d: datetime) -> str:
    """pypdf metadata date string, IST offset."""
    return d.strftime("D:%Y%m%d%H%M%S+05'30'")

def rewrite_metadata(path: Path, genre: str, issued: datetime) -> None:
    """Overwrite the PDF's internal metadata so provenance is coherent:
    the file 'was created' by its system generator at issuance time."""
    writer = PdfWriter(clone_from=str(path))
    writer.add_metadata({
        "/Producer": GENERATORS[genre]["producer"],
        "/Creator":  GENERATORS[genre]["creator"],
        "/CreationDate": _pdf_date(issued),
        "/ModDate":      _pdf_date(issued),
    })
    with open(path, "wb") as fh:
        writer.write(fh)

def header(c, org_name: str, org_addr: str, title: str):
    c.setFont("Helvetica-Bold", 14); c.drawString(20*mm, 280*mm, org_name)
    c.setFont("Helvetica", 8);       c.drawString(20*mm, 275*mm, org_addr)
    c.line(20*mm, 272*mm, 190*mm, 272*mm)
    c.setFont("Helvetica-Bold", 11); c.drawString(20*mm, 264*mm, title)

# ---------------------------------------------------------------------------
# Generators (one per genre)
# ---------------------------------------------------------------------------

def gen_invoice(fake, rng, out_dir: Path, idx: int, manifest: list):
    seller = fake.company()
    state_code = rng.choice(list(STATE_CODES))
    gstin = make_gstin(rng, state_code)
    seller_addr = f"{fake.street_address()}, {fake.city()}, {STATE_CODES[state_code]}"
    buyer = fake.company()
    service = rng.choice(SERVICES)

    issued = fake.date_time_between(start_date="-400d", end_date="-3d")
    inv_no = f"INV-{issued:%Y}-{issued:%m}-{rng.randint(1000, 9999)}"

    fee = round(rng.uniform(1500, 95000), 2)
    slab = rng.choice(GST_SLABS)
    gst = round(fee * slab / 100, 2)
    total = round(fee + gst, 2)

    fn = out_dir / f"invoice_{idx:03d}.pdf"
    c = pdfcanvas.Canvas(str(fn), pagesize=A4)
    header(c, seller, seller_addr, "TAX INVOICE")
    c.setFont("Helvetica", 9)
    y = 254*mm
    for line in [f"Invoice No: {inv_no}", f"Invoice Date: {issued:%d-%m-%Y}",
                 f"GSTIN: {gstin}", f"Place of Supply: {STATE_CODES[state_code]} ({state_code})",
                 "", f"Bill To: {buyer}", f"          {fake.street_address()}, {fake.city()}"]:
        c.drawString(20*mm, y, line); y -= 6*mm
    y -= 4*mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20*mm, y, "Description"); c.drawRightString(190*mm, y, "Amount (Rs.)")
    c.line(20*mm, y-2*mm, 190*mm, y-2*mm); y -= 8*mm
    c.setFont("Helvetica", 9)
    c.drawString(20*mm, y, service);            c.drawRightString(190*mm, y, f"{fee:,.2f}"); y -= 7*mm
    c.drawString(20*mm, y, f"GST @ {slab}%");   c.drawRightString(190*mm, y, f"{gst:,.2f}"); y -= 7*mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20*mm, y, "Total");            c.drawRightString(190*mm, y, f"{total:,.2f}")
    c.setFont("Helvetica", 7)
    c.drawString(20*mm, 20*mm, "This is a system-generated invoice and does not require a signature.")
    c.showPage(); c.save()
    rewrite_metadata(fn, "invoice", issued)

    manifest.append({"filename": fn.name, "doc_type": "invoice", "issuer": seller,
                     "subject": buyer, "ref_no": inv_no, "printed_date": f"{issued:%Y-%m-%d}",
                     "creation_date_meta": f"{issued:%Y-%m-%d}", "producer": GENERATORS["invoice"]["producer"],
                     "gstin": gstin, "state_code": state_code, "fee": fee,
                     "gst_rate": slab, "gst_amount": gst, "total": total})

def gen_letter(fake, rng, out_dir: Path, idx: int, manifest: list):
    company = fake.company()
    emp = fake.name()
    desig = rng.choice(DESIGNATIONS)
    relieved = fake.date_time_between(start_date="-400d", end_date="-10d")
    joined = relieved - timedelta(days=rng.randint(300, 2200))
    ref = f"HR/{relieved:%Y}/{rng.randint(100, 999)}"

    fn = out_dir / f"letter_{idx:03d}.pdf"
    c = pdfcanvas.Canvas(str(fn), pagesize=A4)
    header(c, company, f"{fake.street_address()}, {fake.city()}", "RELIEVING LETTER")
    c.setFont("Helvetica", 9)
    y = 252*mm
    for line in [f"Ref: {ref}", f"Date: {relieved:%d-%m-%Y}", "",
                 f"To, {emp}", "",
                 f"Dear {emp.split()[0]},", "",
                 f"This is to certify that you were employed with {company} as",
                 f"{desig} from {joined:%d-%m-%Y} to {relieved:%d-%m-%Y}.", "",
                 "You have been relieved from your duties at the close of business",
                 f"on {relieved:%d-%m-%Y}, and the company has no dues pending against you.", "",
                 "We thank you for your contribution and wish you success ahead.", "",
                 "For " + company, "", "", "Authorised Signatory, Human Resources"]:
        c.drawString(20*mm, y, line); y -= 6*mm
    c.setFont("Helvetica", 7)
    c.drawString(20*mm, 20*mm, "Generated by HRDesk; valid without physical signature.")
    c.showPage(); c.save()
    rewrite_metadata(fn, "letter", relieved)

    manifest.append({"filename": fn.name, "doc_type": "letter", "issuer": company,
                     "subject": emp, "ref_no": ref, "printed_date": f"{relieved:%Y-%m-%d}",
                     "creation_date_meta": f"{relieved:%Y-%m-%d}", "producer": GENERATORS["letter"]["producer"],
                     "gstin": "", "state_code": "", "fee": "", "gst_rate": "",
                     "gst_amount": "", "total": ""})

def gen_medical(fake, rng, out_dir: Path, idx: int, manifest: list):
    hospital = f"{fake.last_name()} Institute of Medical Sciences"
    patient = fake.name()
    age = rng.randint(23, 78)
    reported = fake.date_time_between(start_date="-400d", end_date="-5d")
    collected = reported - timedelta(days=rng.randint(2, 6))
    ref = f"HISTO/{reported:%Y}/{rng.randint(1000, 9999)}"
    specimen = rng.choice(SPECIMENS)

    fn = out_dir / f"medical_{idx:03d}.pdf"
    c = pdfcanvas.Canvas(str(fn), pagesize=A4)
    header(c, hospital, f"{fake.street_address()}, {fake.city()}",
           "HISTOPATHOLOGY REPORT")
    c.setFont("Helvetica", 9)
    y = 252*mm
    for line in [f"Report No: {ref}", f"Patient: {patient}    Age/Sex: {age}",
                 f"Specimen: {specimen}",
                 f"Collected: {collected:%d-%m-%Y}    Reported: {reported:%d-%m-%Y}", "",
                 "GROSS EXAMINATION:",
                 "Received specimen in formalin, processed entirely.", "",
                 "MICROSCOPIC EXAMINATION:",
                 rng.choice(MICRO_FINDINGS), "",
                 "IMPRESSION:",
                 rng.choice(IMPRESSIONS), "",
                 "Electronically verified report — generated by MediSys LIS."]:
        c.drawString(20*mm, y, line); y -= 6*mm
    c.showPage(); c.save()
    rewrite_metadata(fn, "medical", reported)

    manifest.append({"filename": fn.name, "doc_type": "medical", "issuer": hospital,
                     "subject": patient, "ref_no": ref, "printed_date": f"{reported:%Y-%m-%d}",
                     "creation_date_meta": f"{reported:%Y-%m-%d}", "producer": GENERATORS["medical"]["producer"],
                     "gstin": "", "state_code": "", "fee": "", "gst_rate": "",
                     "gst_amount": "", "total": ""})

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Generate the GENUINE synthetic corpus (step 1)")
    ap.add_argument("--n-invoices", type=int, default=500)
    ap.add_argument("--n-letters", type=int, default=500)
    ap.add_argument("--n-medical", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    Faker.seed(args.seed)
    fake = Faker("en_IN")            # Indian-locale invented identities

    dirs = {g: args.out / (g + "s") for g in ("invoice", "letter", "medical")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    for i in range(args.n_invoices):
        gen_invoice(fake, rng, dirs["invoice"], i + 1, manifest)
    for i in range(args.n_letters):
        gen_letter(fake, rng, dirs["letter"], i + 1, manifest)
    for i in range(args.n_medical):
        gen_medical(fake, rng, dirs["medical"], i + 1, manifest)

    mpath = args.out / "manifest.csv"
    with open(mpath, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(manifest[0].keys()))
        w.writeheader()
        w.writerows(manifest)

    print(f"\nGenerated {len(manifest)} genuine documents "
          f"({args.n_invoices} invoices, {args.n_letters} letters, {args.n_medical} medical)")
    print(f"Output:   {args.out}")
    print(f"Manifest: {mpath}")
    print("\nNow run YOUR acceptance checklist (see the docstring at the top of this file).")

if __name__ == "__main__":
    main()
