"""
check_concepts.py: check every concept id in a mapping against your own OMOP
vocabulary download from Athena.

Usage:
    python tools/check_concepts.py --mapping mapping.csv --vocab <folder with CONCEPT.csv>
        [--project project.json] [--replacements] [--report check_report.csv]
        [--write-names mapping_named.csv]

The wizard's "Check concepts" step applies the same rules in the browser. Per
concept id:

  error    not in this release
  error    deprecated or upgraded (invalid_reason D or U)
  error    non-standard: *_concept_id columns need standard concepts
  error    domain does not match the table the row is written to
  warning  the same id is used for answers with different labels
  review   the row's label and the vocabulary's name share no words

--replacements also reads CONCEPT_RELATIONSHIP.csv and proposes the "Maps to"
target for each non-standard or retired id. --write-names writes a copy of the
mapping whose *_concept_name columns carry the vocabulary's own names, so a
wrong id is visible on sight.

Exit code 1 when any error is found. Nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

TABLE_DOMAIN = {"CONDITION_OCCURRENCE": "Condition", "DRUG_EXPOSURE": "Drug",
                "PROCEDURE_OCCURRENCE": "Procedure", "OBSERVATION": "Observation",
                "MEASUREMENT": "Measurement"}
PROJECT_DOMAIN = {"sex_concepts": "Gender", "race_concepts": "Race",
                  "ethnicity_concepts": "Ethnicity"}
CONCEPT_COLS = ["concept_id", "concept_name", "domain_id", "vocabulary_id",
                "concept_class_id", "standard_concept", "invalid_reason"]
# Answer labels that say nothing about the concept; compare the question instead.
GENERIC_ANSWERS = {"yes", "no", "true", "false", "checked", "present", "absent",
                   "y", "n", "done"}
STOP = {"of", "the", "a", "an", "and", "or", "with", "in", "on", "for", "to", "at",
        "by", "per", "current", "currently", "performed", "since", "last", "visit",
        "check", "all", "that", "apply", "other", "specify", "any", "ever", "has",
        "have", "had", "is", "was", "were", "been", "did", "do", "does", "type",
        "kg", "cm", "mg", "date", "new"}
ALIASES = {"eeg": "electroencephalogram", "ecg": "electrocardiogram",
           "ekg": "electrocardiogram", "mri": "magnetic resonance mri",
           "ct": "computed tomography", "bp": "blood pressure", "bmi": "body mass index",
           "hr": "heart rate", "dx": "diagnosis", "hx": "history",
           "tbi": "traumatic brain injury", "ms": "multiple sclerosis",
           "copd": "chronic obstructive pulmonary disease"}


def words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in STOP or len(w) < 2:
            continue
        out.add(w)
        out |= set(ALIASES.get(w, "").split())
    return out


def share_words(label: str, name: str) -> bool:
    a, b = words(label), words(name)
    if not a or not b:
        return True                      # nothing to compare; do not flag
    if a & b:
        return True
    stems = lambda s: {w[:4] for w in s if len(w) >= 4}
    return bool(stems(a) & stems(b))


def row_label(r: dict) -> str:
    lab = r.get("choice_label", "")
    if not lab or lab.strip().lower() in GENERIC_ANSWERS:
        return r.get("field_label", "")
    return lab


def to_int(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0


def read_concepts(path: Path, ids: set[int]) -> dict[int, dict]:
    found = {}
    for ch in pd.read_csv(path, sep="\t", usecols=CONCEPT_COLS, dtype=str, quoting=3,
                          keep_default_na=False, chunksize=1_000_000):
        hit = ch[pd.to_numeric(ch["concept_id"], errors="coerce").isin(ids)]
        for rec in hit.to_dict("records"):
            found[int(rec["concept_id"])] = rec
    return found


def read_replacements(path: Path, ids: set[int]) -> dict[int, list[int]]:
    out = defaultdict(list)
    keep = {"Maps to", "Concept replaced by"}
    for ch in pd.read_csv(path, sep="\t", usecols=["concept_id_1", "concept_id_2",
                                                   "relationship_id", "invalid_reason"],
                          dtype=str, quoting=3, keep_default_na=False, chunksize=2_000_000):
        hit = ch[pd.to_numeric(ch["concept_id_1"], errors="coerce").isin(ids)
                 & ch["relationship_id"].isin(keep) & (ch["invalid_reason"] == "")]
        for a, b in zip(hit["concept_id_1"], hit["concept_id_2"]):
            if int(b) not in out[int(a)]:
                out[int(a)].append(int(b))
    return out


def uses(mapping: list[dict], project: dict | None) -> list[dict]:
    """Every place a concept id is used, with the domain it has to be in."""
    out = []
    for r in mapping:
        table = r.get("omop_table", "").strip().upper()
        if table not in TABLE_DOMAIN:
            continue
        where = {"form": r["form"], "field": r["field"], "choice_code": r["choice_code"],
                 "label": row_label(r), "table": table}
        for role, col, domain in (("concept", "concept_id", TABLE_DOMAIN[table]),
                                  ("value", "value_concept_id", None),
                                  ("unit", "unit_concept_id", "Unit")):
            cid = to_int(r.get(col))
            if cid > 0:
                out.append({**where, "role": role, "concept_id": cid, "domain": domain})
    if project:
        person = project.get("person", {})
        for key, domain in PROJECT_DOMAIN.items():
            for code, cid in (person.get(key) or {}).items():
                if to_int(cid) > 0:
                    out.append({"form": "project.json", "field": f"person.{key}",
                                "choice_code": str(code), "label": "", "table": "PERSON",
                                "role": "project", "concept_id": to_int(cid),
                                "domain": domain})
        for form, cfg in (project.get("forms") or {}).items():
            if to_int(cfg.get("visit_concept_id")) > 0:
                out.append({"form": "project.json", "field": f"forms.{form}",
                            "choice_code": "", "label": "", "table": "VISIT_OCCURRENCE",
                            "role": "project", "concept_id": to_int(cfg["visit_concept_id"]),
                            "domain": "Visit"})
        if to_int(project.get("type_concept_id", 32879)) > 0:
            out.append({"form": "project.json", "field": "type_concept_id",
                        "choice_code": "", "label": "", "table": "all", "role": "project",
                        "concept_id": to_int(project.get("type_concept_id", 32879)),
                        "domain": "Type Concept"})
    return out


def check(used: list[dict], vocab: dict[int, dict]) -> list[dict]:
    issues = []

    def add(u, severity, message, v=None):
        issues.append({**{k: u[k] for k in ("form", "field", "choice_code", "label",
                                             "table", "role", "concept_id")},
                       "vocab_name": (v or {}).get("concept_name", ""),
                       "vocab_domain": (v or {}).get("domain_id", ""),
                       "vocab_vocabulary": (v or {}).get("vocabulary_id", ""),
                       "severity": severity, "message": message})

    for u in used:
        v = vocab.get(u["concept_id"])
        if v is None:
            add(u, "error", "not in this vocabulary release")
            continue
        if v["invalid_reason"] in ("D", "U"):
            add(u, "error", {"D": "deprecated", "U": "upgraded: replaced by another concept"}
                [v["invalid_reason"]], v)
        elif v["standard_concept"] != "S":
            add(u, "error", "non-standard: OMOP *_concept_id columns need a standard concept", v)
        if u["domain"] and v["domain_id"] != u["domain"]:
            add(u, "error", f"domain is {v['domain_id']}, but this is written where "
                            f"{u['domain']} belongs ({u['table']})", v)
        if u["role"] == "concept" and not share_words(u["label"], v["concept_name"]):
            add(u, "review", "the label and the vocabulary's name share no words", v)

    labels = defaultdict(set)
    for u in used:
        if u["role"] == "concept":
            labels[u["concept_id"]].add(u["label"].strip().lower())
    for u in used:
        if u["role"] == "concept" and len(labels[u["concept_id"]]) > 1:
            others = sorted(labels[u["concept_id"]] - {u["label"].strip().lower()})
            add(u, "warning", "the same id is used for different answers: "
                              + "; ".join(others[:3]), vocab.get(u["concept_id"]))
    return issues


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mapping", type=Path, required=True)
    ap.add_argument("--vocab", type=Path, required=True,
                    help="folder holding CONCEPT.csv from an Athena download")
    ap.add_argument("--project", type=Path, help="also check project.json concepts")
    ap.add_argument("--replacements", action="store_true",
                    help="propose Maps to targets from CONCEPT_RELATIONSHIP.csv")
    ap.add_argument("--report", type=Path, default=Path("check_report.csv"))
    ap.add_argument("--write-names", type=Path, metavar="CSV",
                    help="write the mapping with vocabulary names filled in")
    args = ap.parse_args(argv)

    with args.mapping.open(encoding="utf-8-sig", newline="") as fh:
        mapping = list(csv.DictReader(fh))
    project = json.loads(args.project.read_text(encoding="utf-8-sig")) if args.project else None
    used = uses(mapping, project)
    ids = {u["concept_id"] for u in used}
    print(f"checking {len(ids):,} distinct concept ids used {len(used):,} times")
    vocab = read_concepts(args.vocab / "CONCEPT.csv", ids)
    issues = check(used, vocab)

    if args.replacements:
        bad = {i["concept_id"] for i in issues if i["severity"] == "error"
               and ("standard" in i["message"] or "deprecated" in i["message"]
                    or "upgraded" in i["message"])}
        rel = read_replacements(args.vocab / "CONCEPT_RELATIONSHIP.csv", bad) if bad else {}
        targets = read_concepts(args.vocab / "CONCEPT.csv",
                                {t for ts in rel.values() for t in ts}) if rel else {}
        for i in issues:
            ts = rel.get(i["concept_id"], [])
            i["suggestion"] = "; ".join(f"{t} {targets.get(t, {}).get('concept_name', '')}"
                                        f" ({targets.get(t, {}).get('domain_id', '')})"
                                        for t in ts)
    fields = ["severity", "form", "field", "choice_code", "label", "table", "role",
              "concept_id", "vocab_name", "vocab_domain", "vocab_vocabulary", "message",
              "suggestion"]
    with args.report.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        order = {"error": 0, "warning": 1, "review": 2}
        w.writerows(sorted(issues, key=lambda i: (order[i["severity"]], i["form"],
                                                  i["field"], i["choice_code"])))

    if args.write_names:
        for r in mapping:
            for idc, namec in (("concept_id", "concept_name"),
                               ("value_concept_id", "value_concept_name"),
                               ("unit_concept_id", "unit_concept_name")):
                v = vocab.get(to_int(r.get(idc)))
                if v:
                    r[namec] = v["concept_name"]
        with args.write_names.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(mapping[0].keys()), lineterminator="\n")
            w.writeheader()
            w.writerows(mapping)

    counts = {s: sum(1 for i in issues if i["severity"] == s)
              for s in ("error", "warning", "review")}
    for s in ("error", "warning", "review"):
        for i in [i for i in issues if i["severity"] == s][:25]:
            print(f"[{s.upper():7}] {i['field']}{'=' + i['choice_code'] if i['choice_code'] else ''}"
                  f" {i['role']} {i['concept_id']} {i['vocab_name']!r}: {i['message']}"
                  + (f"  -> {i['suggestion']}" if i.get("suggestion") else ""))
    print(f"\n{counts['error']} error(s), {counts['warning']} warning(s), "
          f"{counts['review']} to review. Report: {args.report}")
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
