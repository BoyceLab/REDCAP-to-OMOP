"""
start_mapping.py: turn a REDCap data dictionary into a blank mapping.csv.

Usage:
    python tools/start_mapping.py --dd data_dictionary.csv --output mapping.csv

The wizard's first step does the same thing in the browser; this is for people
who would rather work in a spreadsheet. Both follow the same rules:

  * radio, dropdown and checkbox fields get one row per choice
  * yes/no gets 1 Yes and 0 No; true/false gets 1 True and 0 False
  * text, notes, calc, slider and sql fields get one row for the field
  * descriptive and file fields are left out, because they hold no answers
  * the first field (the record id) is marked SKIP

Accepts the data dictionary as downloaded from REDCap, or as returned by the
API metadata export (snake_case headers).
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path

MAPPING_COLUMNS = [
    "form", "field", "field_type", "validation", "field_label", "choice_code",
    "choice_label", "omop_table", "concept_id", "concept_name", "value_concept_id",
    "value_concept_name", "unit_concept_id", "unit_concept_name", "date_field",
    "date_offset_days", "notes",
]
# Header text in a REDCap download, and in an API metadata export.
HEADERS = {
    "field": ("variable / field name", "field_name"),
    "form": ("form name", "form_name"),
    "type": ("field type", "field_type"),
    "label": ("field label", "field_label"),
    "choices": ("choices, calculations, or slider labels",
                "select_choices_or_calculations"),
    "validation": ("text validation type or show slider number",
                   "text_validation_type_or_show_slider_number"),
}
LEAVE_OUT = {"descriptive", "file"}
FIXED_CHOICES = {"yesno": [("1", "Yes"), ("0", "No")],
                 "truefalse": [("1", "True"), ("0", "False")]}
_tags = re.compile(r"<[^>]*>")
_space = re.compile(r"\s+")


def clean(text: str) -> str:
    """Drop HTML tags and collapse whitespace, as the wizard does."""
    return _space.sub(" ", _tags.sub(" ", text or "")).strip()


def parse_choices(text: str) -> list[tuple[str, str]]:
    out = []
    for part in (text or "").split("|"):
        part = part.strip()
        if not part:
            continue
        code, sep, label = part.partition(",")
        out.append((code.strip(), clean(label) if sep else clean(code)))
    return out


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def expand(text: str) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        raise SystemExit("the data dictionary is empty")
    header = [h.strip().lower() for h in rows[0]]
    col = {}
    for key, names in HEADERS.items():
        for n in names:
            if n in header:
                col[key] = header.index(n)
                break
        else:
            raise SystemExit(f"no '{names[0]}' column: is this a REDCap data dictionary?")
    out, first = [], True
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        r = r + [""] * (len(header) - len(r))
        field = r[col["field"]].strip()
        ftype = r[col["type"]].strip().lower()
        base = {k: "" for k in MAPPING_COLUMNS}
        base.update(form=r[col["form"]].strip(), field=field, field_type=ftype,
                    validation=r[col["validation"]].strip().lower(),
                    field_label=clean(r[col["label"]]))
        if first:
            first = False
            out.append({**base, "omop_table": "SKIP", "notes": "record id"})
            continue
        if ftype in LEAVE_OUT:
            continue
        if ftype in FIXED_CHOICES:
            choices = FIXED_CHOICES[ftype]
        elif ftype in ("radio", "dropdown", "checkbox"):
            choices = parse_choices(r[col["choices"]])
        else:
            choices = []
        if choices:
            out += [{**base, "choice_code": c, "choice_label": lab} for c, lab in choices]
        else:
            out.append(base)
    return out


def write(rows: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MAPPING_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dd", type=Path, required=True, help="REDCap data dictionary CSV")
    ap.add_argument("--output", type=Path, required=True, help="mapping.csv to write")
    args = ap.parse_args(argv)
    if args.output.exists():
        print(f"{args.output} already exists; not overwriting it")
        return 1
    rows = expand(read_text(args.dd))
    write(rows, args.output)
    forms = len({r["form"] for r in rows})
    fields = len({r["field"] for r in rows})
    print(f"wrote {args.output}: {len(rows):,} rows from {fields:,} fields in {forms} form(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
