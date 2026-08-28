# Re-Authored Document Fraud — corpus and evaluation code

Corpus generator, scored corpora and evaluation code for:

> A. Bembalkar, "Beyond Edit Traces: Re-Authored Document Fraud and the Limits
> of Provenance Coherence," Acxiom Technologies LLP, Mumbai, Maharashtra, India.

Every table and figure in Sections 3 and 5 of the paper rebuilds from this
release. The pipeline is deterministic under a fixed seed.

## What a re-authored document is

A fraudulent file retyped end to end in a desktop tool and exported fresh. It
has one clean revision, uniform fonts and coherent metadata, so there is no
edit residue for edit-trace or pixel-level forensics to find: no detector can
locate an edit that never happened. The paper measures that blind spot and
proposes provenance coherence as a template-free, learning-free check, then
measures where that defence breaks.

## Layout

    code/       20 evaluation and corpus-generation scripts, numbered by step
    corpus/     3,950 generated PDFs, all synthetic, with per-document ground truth
    results/    21 CSVs; every number reported in the paper resolves to one of these

## Corpus

All documents are synthetic, with invented identities and no client data.

| Arm | n | What it is |
|---|---|---|
| `genuine/` | 3,000 | born-digital invoices, relieving letters, histopathology reports |
| `forged_reauthored/` | 75 | shape 2: rebuilt as fresh desktop-authored PDFs |
| `forged_inplace/` | 75 | shape 1: producer string edited, ModDate set after CreationDate |
| `forged_careful/` | 800 | graded adversary, four strengths (below) |

The graded arm is the paper's central experiment:

    L0_naive            spoofs nothing
    L1_producer_spoof   rewrites the producer string alone
    L2_date_spoof       rewrites the dates alone
    L3_full_coherent    spoofs both, coherently

Detection at the deployed threshold goes from 100% at L0 to 0.0% at L1, and L3
defeats the layer at any threshold. That is the result the paper is built around.

Manifests: `corpus/genuine/manifest.csv`, `corpus/forged_manifest.csv`,
`corpus/forged_careful_manifest.csv`.

## Scripts by paper section

| Script | Produces |
|---|---|
| `gen_corpus_step1.py` | genuine arm (§5.1) |
| `forge_corpus_step2.py`, `forge_corpus_step2b.py` | forged arms (§5.1) |
| `edittrace_checks_step3.py` | Table 1, the edit-trace battery (§5.3) |
| `provenance_check_step4.py` | Table 2, the provenance engine (§5.3) |
| `signature_demo_step5.py` | Table 3, offline issuer verification (§4.2) |
| `forge_careful_step6.py` | the graded adversary (§5.2) |
| `stats_step7.py` | confidence intervals throughout |
| `wild_corpus_step10.py`, `retry_failed_step10b.py` | GovDocs1 sample (§5.6) |
| `provenance_wild_step11.py` | false positives on real PDFs (§5.6) |
| `rescore_tokens_step12.py` | alternative producer-token policies (§5.6) |
| `ccmain_metadata_step13.py` | CC-MAIN-2021-31, 7.93M PDFs (§5.6) |
| `reauthor_pixel_corpus_step14.py` | four-arm pixel corpus (§3.2) |
| `score_pixel_corpus_step15.py` | pixel-model scoring (§3.2) |
| `diag_native_vs_norm_step15b.py` | normalisation diagnostic (§3.2) |
| `analyze_pixel_step16.py` | the §3.2 numbers |
| `make_figures.py` | data figures |

## Reproducing

Steps 1 through 7 rebuild the synthetic corpus and every table in §5.3 and §5.2
from nothing, offline:

    python code/gen_corpus_step1.py
    python code/forge_corpus_step2.py
    python code/forge_corpus_step2b.py
    python code/forge_careful_step6.py
    python code/edittrace_checks_step3.py
    python code/provenance_check_step4.py
    python code/signature_demo_step5.py
    python code/stats_step7.py

Steps 10 through 13 need network access, since they fetch GovDocs1 and the
Apache Tika metadata release for CC-MAIN-2021-31-PDF-UNTRUNCATED. Those two
corpora are third-party and are not redistributed here.

## Not included

The pixel model of §3.2 is a production checkpoint and is not part of this
release; `score_pixel_corpus_step15.py` documents the inference path it used.
The DocTamper dataset and weights belong to their authors and are obtained from
them. Aggregate production figures cited in §6 come from a deployed system and
are reported in prose only, never released.

## Licence

Code under MIT (`LICENSE`). Corpus and result CSVs under CC BY 4.0
(`LICENSE-DATA`). If you use either, please cite the paper.
