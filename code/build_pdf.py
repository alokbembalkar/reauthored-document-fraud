#!/usr/bin/env python3
"""
build_pdf.py — render the manuscript to a clean, readable HTML (and then PDF).

It produces a READING version of the paper:
  * strips internal scaffolding (status table, revision log, numbers ledger,
    [src: ...] tags, "note to us" parentheticals)
  * embeds the PNG figures inline (base64)
  * keeps the Mermaid diagrams as <pre class="mermaid"> so a browser / headless
    Chrome renders them as real flowcharts
  * writes build/Paper1.html

To get the PDF, this script also prints the exact headless-Chrome command
(run by build_pdf.sh, or copy it yourself).

RUN:
    source ~/research_venv/bin/activate
    cd ~/Desktop/Research
    python3 code/build_pdf.py
"""

import base64
import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Paper 1 — MANUSCRIPT.md"
BUILD = ROOT / "build"
FIGS = ROOT / "figures"


def clean(md: str) -> str:
    # keep from the H1 title; splice out the scaffolding between title and Abstract
    title_match = re.search(r"^# .+$", md, re.M)
    title = title_match.group(0) if title_match else "# Paper"
    # body runs from "## Abstract" to just before the internal ledger
    start = md.find("## Abstract")
    if start == -1:
        start = 0
    end = md.find("## Consolidated numbers ledger")
    if end == -1:
        end = len(md)
    md = title + "\n\n" + md[start:end]

    # drop internal-only lines / paragraphs
    md = re.sub(r"\*\(Note to us:.*?\)\*", "", md, flags=re.S)   # notes to us
    md = re.sub(r"\[src:[^\]]*\]", "", md)                        # source tags
    md = re.sub(r"\*Revision log —.*?\*", "", md, flags=re.S)     # revision log
    # tidy doubled spaces/newlines left behind
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md


def extract_mermaid(md: str):
    """Pull ```mermaid blocks out so markdown doesn't escape them; return
    (md_with_placeholders, [raw_blocks])."""
    blocks = []

    def repl(m):
        blocks.append(m.group(1))
        return f"\n\nMERMAIDPLACEHOLDER{len(blocks)-1}\n\n"

    md = re.sub(r"```mermaid\n(.*?)```", repl, md, flags=re.S)
    return md, blocks


def embed_images(html: str) -> str:
    def repl(m):
        alt, src = m.group(1), m.group(2)
        p = ROOT / src
        if not p.exists():
            return m.group(0)
        b64 = base64.b64encode(p.read_bytes()).decode()
        return f'<figure><img alt="{alt}" src="data:image/png;base64,{b64}"></figure>'
    return re.sub(r'<img alt="([^"]*)" src="([^"]+)"\s*/?>', repl, html)


TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>Beyond Edit Traces</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true, theme:'neutral'}});</script>
<style>
  body {{ font-family: Georgia, 'Times New Roman', serif; max-width: 760px;
         margin: 0 auto; padding: 24px 28px; color: #1a1a1a; line-height: 1.5;
         font-size: 15px; }}
  h1 {{ font-size: 26px; line-height: 1.25; margin: 0 0 6px; }}
  h2 {{ font-size: 20px; border-bottom: 1px solid #ddd; padding-bottom: 4px;
        margin-top: 30px; }}
  h3 {{ font-size: 16px; margin-top: 22px; color: #222; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 12px 0; }}
  th, td {{ border: 1px solid #bbb; padding: 5px 8px; text-align: left;
           vertical-align: top; }}
  th {{ background: #f2f2f2; }}
  code {{ background: #f4f4f4; padding: 1px 4px; border-radius: 3px;
         font-size: 12px; }}
  pre {{ background: #f7f7f7; padding: 10px; border-radius: 5px; overflow-x: auto;
        font-size: 12px; }}
  pre.mermaid {{ background: transparent; text-align: center; }}
  figure {{ text-align: center; margin: 16px 0; }}
  img {{ max-width: 100%; border: 1px solid #eee; }}
  blockquote {{ border-left: 3px solid #ccc; margin: 10px 0; padding-left: 12px;
               color: #444; }}
  a {{ color: #205081; }}
  @media print {{ h2 {{ page-break-after: avoid; }}
                  figure, pre, table {{ page-break-inside: avoid; }} }}
</style></head><body>
{body}
</body></html>
"""


def main():
    BUILD.mkdir(exist_ok=True)
    md = clean(SRC.read_text())
    md, mermaid_blocks = extract_mermaid(md)
    html_body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
    # reinsert mermaid diagrams as live blocks
    for i, blk in enumerate(mermaid_blocks):
        html_body = html_body.replace(
            f"<p>MERMAIDPLACEHOLDER{i}</p>",
            f'<pre class="mermaid">\n{blk}</pre>')
    html_body = embed_images(html_body)
    html = TEMPLATE.format(body=html_body)

    out_html = BUILD / "Paper1.html"
    out_html.write_text(html)
    print(f"wrote {out_html}")

    out_pdf = BUILD / "Paper1.pdf"
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    print("\nNow render to PDF with headless Chrome (also in build_pdf.sh):\n")
    print(f'  "{chrome}" \\\n    --headless=new --disable-gpu --no-pdf-header-footer \\\n'
          f'    --virtual-time-budget=20000 --run-all-compositor-stages-before-draw \\\n'
          f'    --print-to-pdf="{out_pdf}" "file://{out_html}"\n')


if __name__ == "__main__":
    main()
