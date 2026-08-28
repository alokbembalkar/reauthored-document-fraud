#!/usr/bin/env python3
"""
signature_demo_step5.py — Paper 1, STEP 5 of the pipeline.

Demonstrates the OFFLINE CRYPTOGRAPHIC ISSUER-VERIFICATION layer of the
paper (§4.2) on entirely synthetic material, and produces TABLE 3.

WHAT §4.2 CLAIMS, AND WHAT THIS SHOWS
    A digital signature is the ONE piece of evidence a re-authored fraud
    cannot fabricate — but only if verification separates two things:

      INTEGRITY (support grade): the content is unmodified since signing.
          A forger can achieve this by signing their own fabrication with a
          self-generated certificate. Integrity alone must NOT clear.

      ISSUER IDENTITY (proof grade): the signer's certificate CHAINS to a
          trusted national root (here: a bundled toy "National Root CA").
          A forger cannot forge this. Only this clears a document.

    This script builds a toy CA, signs documents from the genuine corpus,
    and measures four scenarios — the paper's adversarial pair plus the two
    genuine cases:

      1. genuine_signed        issuer cert chains to the bundled root
                               -> INTEGRITY ok, WHOLE-FILE ok, CHAIN trusted
                               -> PROOF: cleared, issuer named
      2. self_signed_forger    valid signature, but a self-signed cert that
                               does NOT chain to the root
                               -> INTEGRITY ok, CHAIN untrusted
                               -> SUPPORT only: NOT cleared (integrity != identity)
      3. edited_after_sign     one byte changed inside the signed range
                               -> INTEGRITY broken -> TAMPER caught
      4. appended_after_sign   bytes appended after the signature
                               -> WHOLE-FILE coverage broken -> TAMPER caught
                                  (modified after signing)

HONEST SCOPE
    Real X.509 certificates and real RSA-SHA256 signatures are used (via the
    `cryptography` library) over a PAdES-style byte range. To stay
    dependency-light and reproducible, the signed document is a self-
    contained container rather than a full PAdES-embedded PDF; the
    VERIFICATION LOGIC (digest over a covered range, whole-file coverage,
    certificate-chain-to-root, validity dates) is exactly what a PAdES
    verifier does. A production/real-PAdES variant (e.g. pyhanko) is a
    drop-in upgrade if a reviewer asks; it would not change these outcomes.

SIGNATURE *PREVALENCE* (what fraction of real documents carry signatures)
    stays as prose corroboration from the production record — it cannot be
    reproduced on synthetic data and is not claimed here.

--------------------------------------------------------------------------
SETUP (one new library):

    source ~/research_venv/bin/activate
    pip install cryptography

RUN (from the Research folder):

    cd ~/Desktop/Research
    python3 code/signature_demo_step5.py            # 25 documents
    python3 code/signature_demo_step5.py --n 50

OUTPUT
    results/table3_signature.csv   the four scenarios x outcomes
    (also printed)

WHAT TO TEST
    1. Printed Table 3: genuine_signed all PROOF (issuer named); self_signed
       all SUPPORT (not cleared); both attacks all TAMPER (caught).
    2. That is §4.2 end to end: integrity is not identity, and the
       adversarial pair is caught.
    3. Re-run -> identical outcome counts (keys differ each run, verdicts
       do not).
--------------------------------------------------------------------------
"""

import argparse
import base64
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.exceptions import InvalidSignature

RESEARCH_ROOT = Path(__file__).resolve().parent.parent
GENUINE_INV = RESEARCH_ROOT / "corpus" / "genuine" / "invoices"
MANIFEST = RESEARCH_ROOT / "corpus" / "genuine" / "manifest.csv"
RESULTS = RESEARCH_ROOT / "results"

SIG_OPEN = b"\n%%SIG%%\n"
SIG_CLOSE = b"\n%%ENDSIG%%\n"


# --------------------------------------------------------------------------
# Toy certificate authority (the "bundled national root" + issuers + forger)
# --------------------------------------------------------------------------

def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def make_root(cn="Toy National Root CA"):
    key = _key()
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(_name(cn)).issuer_name(_name(cn))
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    return key, cert


def issue_cert(subject_cn, root_key, root_cert):
    """An issuer certificate signed BY the root (so it chains to the root)."""
    key = _key()
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(_name(subject_cn)).issuer_name(root_cert.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=730))
            .sign(root_key, hashes.SHA256()))       # signed by the ROOT key
    return key, cert


def self_signed(subject_cn):
    """A forger's self-signed certificate: it names the issuer it wants to
    impersonate, but is signed by its OWN key -> chains to nothing trusted."""
    key = _key()
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(_name(subject_cn)).issuer_name(_name(subject_cn))
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=730))
            .sign(key, hashes.SHA256()))            # signed by ITSELF
    return key, cert


# --------------------------------------------------------------------------
# Sign / verify (PAdES-style byte range in a self-contained container)
# --------------------------------------------------------------------------

def sign_document(content: bytes, signer_key, signer_cert) -> bytes:
    """Signature covers content bytes [0:len(content)]; the signature block
    is appended immediately after and is the end of the file."""
    signature = signer_key.sign(content, padding.PKCS1v15(), hashes.SHA256())
    cert_der = signer_cert.public_bytes(serialization.Encoding.DER)
    trailer = json.dumps({
        "br_start": 0, "br_end": len(content),
        "sig": base64.b64encode(signature).decode(),
        "cert": base64.b64encode(cert_der).decode(),
        "alg": "rsa-sha256",
    }).encode()
    return content + SIG_OPEN + trailer + SIG_CLOSE


def verify_document(blob: bytes, root_cert) -> dict:
    """Reproduce a PAdES verifier: recompute the digest over the covered
    range, check whole-file coverage, and chain the signer cert to root."""
    out = {"integrity_ok": False, "whole_file_ok": False,
           "chain_trusted": False, "signer": "", "verdict": "", "grade": ""}

    i = blob.find(SIG_OPEN)
    j = blob.find(SIG_CLOSE)
    if i == -1 or j == -1:
        out["verdict"] = "UNSIGNED"; out["grade"] = "none"; return out
    trailer = json.loads(blob[i + len(SIG_OPEN):j])
    br_end = trailer["br_end"]
    covered = blob[trailer["br_start"]:br_end]
    signer_cert = x509.load_der_x509_certificate(base64.b64decode(trailer["cert"]))
    _cn = signer_cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    out["signer"] = _cn[0].value if _cn else signer_cert.subject.rfc4514_string()

    # (1) INTEGRITY: signature valid over the covered bytes?
    try:
        signer_cert.public_key().verify(base64.b64decode(trailer["sig"]),
                                        covered, padding.PKCS1v15(), hashes.SHA256())
        out["integrity_ok"] = True
    except InvalidSignature:
        out["integrity_ok"] = False

    # (2) WHOLE-FILE COVERAGE: signature immediately follows covered content,
    #     and nothing is appended after the signature block.
    out["whole_file_ok"] = (br_end == i) and (blob.endswith(SIG_CLOSE))

    # (3) CHAIN: is the signer cert signed by the bundled root, and valid now?
    try:
        root_cert.public_key().verify(
            signer_cert.signature, signer_cert.tbs_certificate_bytes,
            padding.PKCS1v15(), signer_cert.signature_hash_algorithm)
        now = datetime.now(timezone.utc)
        valid = signer_cert.not_valid_before_utc <= now <= signer_cert.not_valid_after_utc
        out["chain_trusted"] = valid
    except InvalidSignature:
        out["chain_trusted"] = False

    # verdict ladder (§4.2 / §4.3)
    if not out["integrity_ok"]:
        out["verdict"], out["grade"] = "TAMPER (signature broken)", "conviction"
    elif not out["whole_file_ok"]:
        out["verdict"], out["grade"] = "TAMPER (modified after signing)", "conviction"
    elif out["chain_trusted"]:
        out["verdict"], out["grade"] = f"PROOF: issuer verified ({out['signer']})", "proof-clear"
    else:
        out["verdict"], out["grade"] = "SUPPORT: integrity only, issuer unverified", "support"
    return out


def main():
    ap = argparse.ArgumentParser(description="Cryptographic issuer verification demo (step 5)")
    ap.add_argument("--n", type=int, default=25, help="documents to sign/attack")
    args = ap.parse_args()

    if not MANIFEST.exists():
        raise SystemExit("Run step 1 first (need corpus/genuine/invoices).")
    issuer_by_file = {r["filename"]: r["issuer"]
                      for r in csv.DictReader(open(MANIFEST)) if r["doc_type"] == "invoice"}

    pdfs = sorted(GENUINE_INV.glob("*.pdf"))[:args.n]
    if not pdfs:
        raise SystemExit("No genuine invoices found. Run step 1.")

    root_key, root_cert = make_root()

    # tally per scenario
    scen = {"genuine_signed": [], "self_signed_forger": [],
            "edited_after_sign": [], "appended_after_sign": []}

    for pdf in pdfs:
        content = pdf.read_bytes()
        issuer_cn = issuer_by_file.get(pdf.name, "Unknown Issuer")

        # trusted issuer
        ik, ic = issue_cert(issuer_cn, root_key, root_cert)
        signed = sign_document(content, ik, ic)
        scen["genuine_signed"].append(verify_document(signed, root_cert))

        # forger self-signed
        fk, fc = self_signed(issuer_cn)
        forged = sign_document(content, fk, fc)
        scen["self_signed_forger"].append(verify_document(forged, root_cert))

        # attack 1: edit one byte inside the covered range
        edited = bytearray(signed); edited[100] ^= 0x01
        scen["edited_after_sign"].append(verify_document(bytes(edited), root_cert))

        # attack 2: append bytes after the signature
        appended = signed + b"\n%%EOF appended payload\n"
        scen["appended_after_sign"].append(verify_document(appended, root_cert))

    # summarise
    def summarise(name, results, expect_grade):
        n = len(results)
        as_expected = sum(1 for r in results if r["grade"] == expect_grade)
        example = results[0]["verdict"]
        return {"scenario": name, "n": n, "as_expected": as_expected,
                "expected_grade": expect_grade, "example_verdict": example}

    rows = [
        summarise("genuine_signed", scen["genuine_signed"], "proof-clear"),
        summarise("self_signed_forger", scen["self_signed_forger"], "support"),
        summarise("edited_after_sign", scen["edited_after_sign"], "conviction"),
        summarise("appended_after_sign", scen["appended_after_sign"], "conviction"),
    ]

    RESULTS.mkdir(exist_ok=True)
    t3 = RESULTS / "table3_signature.csv"
    with open(t3, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    print("\n" + "=" * 78)
    print("TABLE 3 — Offline cryptographic issuer verification (§4.2)")
    print("=" * 78)
    print(f"{'scenario':<22}{'n':>4}{'as_expected':>13}   expected -> example verdict")
    print("-" * 78)
    for r in rows:
        print(f"{r['scenario']:<22}{r['n']:>4}{r['as_expected']:>13}   "
              f"{r['expected_grade']}")
        print(f"{'':<39}e.g. {r['example_verdict']}")
    print("-" * 78)
    print("Reading it: a self-signed forgery has VALID integrity but is NOT cleared")
    print("(integrity is not identity). Only a root-chained issuer clears (PROOF).")
    print("Both post-signing attacks are caught.")
    print(f"\nSummary: {t3}")


if __name__ == "__main__":
    main()
