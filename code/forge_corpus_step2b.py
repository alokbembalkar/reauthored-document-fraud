#!/usr/bin/env python3
"""
forge_corpus_step2b.py — Paper 1, STEP 2b: the SCALED + GRADED-ADVERSARY corpus.

Addresses the core reviewer critique ("n=75 is thin; the careful attacker is
unmeasured; vary the authoring tools"). Builds on step 2's generators
(imported from forge_corpus_step2.py — keep both files in code/).

WHAT IT ADDS OVER STEP 2
  1. SCALE: ~1,000 re-authored + ~1,000 in-place forgeries (defaults below),
     enough for tight confidence intervals.
  2. AUTHORING-TOOL STRATIFICATION: each re-authored forgery cycles through
     the 6 desktop tool profiles evenly (Word / LibreOffice / Canva /
     Chromium print-to-PDF / iLovePDF / MS Print To PDF) and the manifest
     records which — enabling the per-tool robustness table.
  3. THE GRADED CAREFUL ATTACKER (the paper's §7.1 adversary, now measured).
     Grades are defined by WHICH provenance signals the attacker defeats:
       G0  naive re-authoring        (defeats nothing)      — same as step 2
       G1  date-coherent             (defeats signal B: sets the file's
           CreationDate equal to the printed date, exactly as a genuine
           system would — requires metadata-editing tooling)
       G2  producer-spoofed          (defeats signals A and C: writes the
           GENUINE system-generator producer/creator strings into the
           desktop-made file)
       G3  fully careful = G1 + G2   (defeats all three signals)
     EXPECTED, HONESTLY: G2 and G3 will largely EVADE the provenance engine
     (that is the §7.1 admission, now quantified as a degradation curve);
     G1 remains flagged via A+C. Publishing this cliff — and where the
     signature layer still holds — is the point.

OUTPUT LAYOUT (new corpus arms; step 2's original arms are untouched)
  corpus/forged_reauthored_v2/{invoices,letters,medicals}/   G0 at scale
  corpus/forged_inplace_v2/{...}                             shape 1 at scale
  corpus/forged_careful