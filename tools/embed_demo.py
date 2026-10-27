"""
embed_demo.py: copy the demo files into docs/index.html, so the wizard's demo
buttons work offline from a single file.

    python tools/embed_demo.py           # rewrite the embedded copy
    python tools/embed_demo.py --check   # exit 1 if it is out of date (used in CI)

The files in examples/demo/ are the source; the block between the DEMO markers
in docs/index.html is generated from them.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "index.html"
DEMO = ROOT / "examples" / "demo"
BLOCKS = [("demo-dd", "text/csv", "data_dictionary.csv"),
          ("demo-mapping-blank", "text/csv", "mapping_blank.csv"),
          ("demo-mapping", "text/csv", "mapping.csv"),
          ("demo-mapping-mistakes", "text/csv", "mapping_with_mistakes.csv"),
          ("demo-project", "application/json", "project.json")]
BEGIN, END = "<!-- DEMO:BEGIN -->", "<!-- DEMO:END -->"


def generated() -> str:
    parts = [BEGIN]
    for bid, mime, name in BLOCKS:
        text = (DEMO / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        if "</script" in text.lower():
            raise SystemExit(f"{name} contains '</script', which cannot be embedded")
        parts.append(f'<script type="{mime}" id="{bid}">\n{text}</script>')
    parts.append(END)
    return "\n".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    page = PAGE.read_text(encoding="utf-8")
    a, b = page.index(BEGIN), page.index(END) + len(END)
    new = page[:a] + generated() + page[b:]
    if args.check:
        if new != page:
            print("docs/index.html has an out-of-date copy of the demo: run tools/embed_demo.py")
            return 1
        print("embedded demo matches examples/demo/")
        return 0
    PAGE.write_text(new, encoding="utf-8", newline="\n")
    print("embedded the demo files in docs/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
