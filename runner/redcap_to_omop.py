"""
redcap_to_omop.py: turn a REDCap export into OMOP CDM v5.4 tables, driven by a
mapping file.

Usage:
    python runner/redcap_to_omop.py --project project.json --mapping mapping.csv \\
        --export export.csv [export2.csv ...] --output omop_output

    python runner/redcap_to_omop.py ... --check     # validate only, write nothing

Inputs
    project.json  record id, person fields, per-form date and visit settings,
                  death fields. Made by the wizard, or by hand (see GUIDE.md).
    mapping.csv   one row per REDCap field, and per choice of a radio, dropdown,
                  yes/no or checkbox field. A row with an OMOP table and a concept
                  id above 0 produces records; every other row produces nothing.
    export.csv    REDCap "Export Data", CSV, raw coded values (not labels). Pass
                  several files if the export was split; they are stacked.

Output
    One CSV per OMOP table (PERSON, OBSERVATION_PERIOD, VISIT_OCCURRENCE,
    CONDITION_OCCURRENCE, DRUG_EXPOSURE, PROCEDURE_OCCURRENCE, OBSERVATION,
    MEASUREMENT, DEATH), always written, header only when empty, plus
    qc_report.md. Exit code 1 when a blocking problem is found.

Only pandas is required. Nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

FORMAT = "redcap-omop-mapping-kit/1"
CLINICAL = ("CONDITION_OCCURRENCE", "DRUG_EXPOSURE", "PROCEDURE_OCCURRENCE",
            "OBSERVATION", "MEASUREMENT")
TABLE_CHOICES = CLINICAL + ("SKIP", "")
CHOICE_TYPES = {"radio", "dropdown", "yesno", "truefalse", "checkbox"}
MAPPING_COLUMNS = [
    "form", "field", "field_type", "validation", "field_label", "choice_code",
    "choice_label", "omop_table", "concept_id", "concept_name", "value_concept_id",
    "value_concept_name", "unit_concept_id", "unit_concept_name", "date_field",
    "date_offset_days", "notes",
]

# OMOP CDM v5.4 column order for every table this runner writes.
CDM = {
    "person": ["person_id", "gender_concept_id", "year_of_birth", "month_of_birth",
               "day_of_birth", "birth_datetime", "race_concept_id",
               "ethnicity_concept_id", "location_id", "provider_id", "care_site_id",
               "person_source_value", "gender_source_value",
               "gender_source_concept_id", "race_source_value",
               "race_source_concept_id", "ethnicity_source_value",
               "ethnicity_source_concept_id"],
    "observation_period": ["observation_period_id", "person_id",
                           "observation_period_start_date",
                           "observation_period_end_date", "period_type_concept_id"],
    "visit_occurrence": ["visit_occurrence_id", "person_id", "visit_concept_id",
                         "visit_start_date", "visit_start_datetime", "visit_end_date",
                         "visit_end_datetime", "visit_type_concept_id", "provider_id",
                         "care_site_id", "visit_source_value",
                         "visit_source_concept_id", "admitted_from_concept_id",
                         "admitted_from_source_value", "discharged_to_concept_id",
                         "discharged_to_source_value", "preceding_visit_occurrence_id"],
    "condition_occurrence": ["condition_occurrence_id", "person_id",
                             "condition_concept_id", "condition_start_date",
                             "condition_start_datetime", "condition_end_date",
                             "condition_end_datetime", "condition_type_concept_id",
                             "condition_status_concept_id", "stop_reason",
                             "provider_id", "visit_occurrence_id", "visit_detail_id",
                             "condition_source_value", "condition_source_concept_id",
                             "condition_status_source_value"],
    "drug_exposure": ["drug_exposure_id", "person_id", "drug_concept_id",
                      "drug_exposure_start_date", "drug_exposure_start_datetime",
                      "drug_exposure_end_date", "drug_exposure_end_datetime",
                      "verbatim_end_date", "drug_type_concept_id", "stop_reason",
                      "refills", "quantity", "days_supply", "sig", "route_concept_id",
                      "lot_number", "provider_id", "visit_occurrence_id",
                      "visit_detail_id", "drug_source_value", "drug_source_concept_id",
                      "route_source_value", "dose_unit_source_value"],
    "procedure_occurrence": ["procedure_occurrence_id", "person_id",
                             "procedure_concept_id", "procedure_date",
                             "procedure_datetime", "procedure_end_date",
                             "procedure_end_datetime", "procedure_type_concept_id",
                             "modifier_concept_id", "quantity", "provider_id",
                             "visit_occurrence_id", "visit_detail_id",
                             "procedure_source_value", "procedure_source_concept_id",
                             "modifier_source_value"],
    "observation": ["observation_id", "person_id", "observation_concept_id",
                    "observation_date", "observation_datetime",
                    "observation_type_concept_id", "value_as_number",
                    "value_as_string", "value_as_concept_id", "qualifier_concept_id",
                    "unit_concept_id", "provider_id", "visit_occurrence_id",
                    "visit_detail_id", "observation_source_value",
                    "observation_source_concept_id", "unit_source_value",
                    "qualifier_source_value", "value_source_value",
                    "observation_event_id", "obs_event_field_concept_id"],
    "measurement": ["measurement_id", "person_id", "measurement_concept_id",
                    "measurement_date", "measurement_datetime", "measurement_time",
                    "measurement_type_concept_id", "operator_concept_id",
                    "value_as_number", "value_as_concept_id", "unit_concept_id",
                    "range_low", "range_high", "provider_id", "visit_occurrence_id",
                    "visit_detail_id", "measurement_source_value",
                    "measurement_source_concept_id", "unit_source_value",
                    "unit_source_concept_id", "value_source_value",
                    "measurement_event_id", "meas_event_field_concept_id"],
    "death": ["person_id", "death_date", "death_datetime", "death_type_concept_id",
              "cause_concept_id", "cause_source_value", "cause_source_concept_id"],
}
# Per clinical table: (file, column prefix, date column, datetime column)
CLINICAL_COLS = {
    "CONDITION_OCCURRENCE": ("condition_occurrence", "condition",
                             "condition_start_date", "condition_start_datetime"),
    "DRUG_EXPOSURE": ("drug_exposure", "drug", "drug_exposure_start_date",
                      "drug_exposure_start_datetime"),
    "PROCEDURE_OCCURRENCE": ("procedure_occurrence", "procedure", "procedure_date",
                             "procedure_datetime"),
    "OBSERVATION": ("observation", "observation", "observation_date",
                    "observation_datetime"),
    "MEASUREMENT": ("measurement", "measurement", "measurement_date",
                    "measurement_datetime"),
}
REDCAP_SYSTEM = re.compile(r"^(redcap_.*|.*_complete)$")


class Report:
    """Collects what the run found, prints it, and writes qc_report.md."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []
        self.counts: dict[str, int] = {}

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.info.append(msg)

    def lines(self) -> list[str]:
        out = []
        for tag, items in (("FAIL", self.errors), ("WARN", self.warnings),
                           ("INFO", self.info)):
            out += [f"[{tag}] {m}" for m in items]
        if self.counts:
            out.append("")
            out.append("Rows written:")
            out += [f"  {t:22} {n:>9,}" for t, n in self.counts.items()]
        return out

    def write(self, path: Path) -> None:
        body = ["# QC report", "", f"Generated {date.today().isoformat()} by "
                "redcap_to_omop.py.", ""]
        for title, items in (("Blocking problems", self.errors),
                             ("Warnings", self.warnings), ("Notes", self.info)):
            body.append(f"## {title}")
            body.append("")
            body += [f"- {m}" for m in items] or ["None."]
            body.append("")
        if self.counts:
            body += ["## Rows written", "", "| Table | Rows |", "|---|---|"]
            body += [f"| {t} | {n:,} |" for t, n in self.counts.items()]
        path.write_text("\n".join(body) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Reading inputs
# ---------------------------------------------------------------------------
def read_csv_text(path: Path) -> pd.DataFrame:
    """Every value as a string, blanks as ''. Tries UTF-8, then Windows-1252."""
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return pd.read_csv(path, dtype=str, keep_default_na=False,
                               encoding=enc)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"could not decode {path}")


def to_int(value, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def read_mapping(path: Path, rep: Report) -> pd.DataFrame:
    m = read_csv_text(path)
    missing = [c for c in MAPPING_COLUMNS if c not in m.columns]
    if missing:
        rep.error(f"{path.name} is missing column(s): {', '.join(missing)}")
        return m
    for c in m.columns:
        m[c] = m[c].astype(str).str.strip()
    m["omop_table"] = m["omop_table"].str.upper()
    bad = sorted(set(m["omop_table"]) - set(TABLE_CHOICES))
    if bad:
        rep.error(f"{path.name}: unknown omop_table value(s) {bad}. Use one of "
                  f"{', '.join(CLINICAL)}, SKIP, or leave blank.")
    for c in ("concept_id", "value_concept_id", "unit_concept_id", "date_offset_days"):
        m[c + "_int"] = m[c].map(to_int)
    mapped = m["omop_table"].isin(CLINICAL) & (m["concept_id_int"] > 0)
    orphan = (~m["omop_table"].isin(CLINICAL)) & (m["concept_id_int"] > 0)
    if orphan.any():
        rep.warn(f"{int(orphan.sum())} mapping row(s) have a concept id but no OMOP "
                 f"table, so they produce nothing (e.g. {m.loc[orphan, 'field'].iloc[0]})")
    unmapped = m["omop_table"].isin(CLINICAL) & (m["concept_id_int"] <= 0)
    rep.note(f"mapping: {int(mapped.sum()):,} rows mapped, "
             f"{int(unmapped.sum()):,} assigned a table but no concept yet, "
             f"{int((m['omop_table'] == 'SKIP').sum()):,} skipped, "
             f"{int((m['omop_table'] == '').sum()):,} not yet decided")
    return m


def read_exports(paths: list[Path]) -> pd.DataFrame:
    frames = [read_csv_text(p) for p in paths]
    df = pd.concat(frames, ignore_index=True, sort=False).fillna("")
    for c in df.columns:
        df[c] = df[c].astype(str).str.strip()
    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def checkbox_column(field: str, code: str, columns) -> str | None:
    """REDCap names a checkbox choice field___code, lowercasing the code and
    writing a minus sign or period as an underscore."""
    cands = [f"{field}___{code}", f"{field}___{code.lower()}",
             f"{field}___{re.sub(r'[^0-9a-z_]', '_', code.lower())}"]
    for c in cands:
        if c in columns:
            return c
    return None


def same_code(values: pd.Series, code: str) -> pd.Series:
    """values == code, also when one side was written as 1.0 and the other as 1."""
    exact = values == code
    try:
        num = float(code)
    except ValueError:
        return exact
    return exact | (pd.to_numeric(values, errors="coerce") == num)


def parse_dates(values: pd.Series, what: str, rep: Report, min_year: int,
                max_date: pd.Timestamp) -> pd.Series:
    """Parse REDCap dates; blank unparseable and implausible ones, and say so."""
    s = values.where(values != "")
    # Read the year first: some pandas versions cannot hold a year like 1506 at
    # all and would report it as "not a date" instead of as implausible.
    year = pd.to_numeric(s.str.extract(r"^\s*(\d{4})", expand=False), errors="coerce")
    early = year < min_year
    d = pd.to_datetime(s.where(~early), errors="coerce", format="ISO8601")
    bad = int((d.isna() & s.notna() & ~early).sum())
    if bad:
        rep.warn(f"{what}: {bad:,} value(s) are not dates and were ignored")
    implausible = early | (d.notna() & (d > max_date))
    if implausible.any():
        rep.warn(f"{what}: {int(implausible.sum()):,} date(s) before {min_year} "
                 f"or in the future were ignored (e.g. "
                 f"{values[implausible].iloc[0]})")
        d = d.where(~implausible)
    return d.dt.normalize()


def first_per_record(df: pd.DataFrame, rid: str, col: str) -> pd.Series:
    """First non-blank value of col for each record."""
    sub = df.loc[df[col] != "", [rid, col]].drop_duplicates(rid, keep="first")
    return sub.set_index(rid)[col]


def empty(table: str) -> pd.DataFrame:
    return pd.DataFrame(columns=CDM[table])


def fmt_date(d: pd.Series) -> pd.Series:
    return d.dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Checks that run before any table is built
# ---------------------------------------------------------------------------
def preflight(project: dict, m: pd.DataFrame, df: pd.DataFrame, rep: Report) -> None:
    cols = set(df.columns)
    rid = project.get("record_id_field", "")
    if rid not in cols:
        rep.error(f"record id field '{rid}' is not a column of the export")
    person = project.get("person", {})
    for key in ("birth_date_field", "birth_year_field", "sex_field",
                "race_field", "ethnicity_field"):
        f = person.get(key)
        if f and f not in cols:
            rep.error(f"person.{key} '{f}' is not a column of the export")
    if not (person.get("birth_date_field") or person.get("birth_year_field")):
        rep.error("project.json needs person.birth_date_field or "
                  "person.birth_year_field: OMOP requires a year of birth")
    for form, cfg in project.get("forms", {}).items():
        f = cfg.get("date_field")
        if f and f not in cols:
            rep.error(f"forms.{form}.date_field '{f}' is not a column of the export")
    death = project.get("death") or {}
    for key in ("flag_field", "date_field"):
        if death.get(key) and death[key] not in cols:
            rep.error(f"death.{key} '{death[key]}' is not a column of the export")

    mapped = m[m["omop_table"].isin(CLINICAL) & (m["concept_id_int"] > 0)]
    absent, undated, bad_date_col = set(), set(), set()
    for r in mapped.itertuples(index=False):
        if r.field_type == "checkbox":
            if checkbox_column(r.field, r.choice_code, cols) is None:
                absent.add(f"{r.field}___{r.choice_code}")
        elif r.field not in cols:
            absent.add(r.field)
        dcol = row_date_field(r, project)
        if not dcol:
            undated.add(r.field)
        elif dcol not in cols:
            bad_date_col.add(f"{r.field} -> {dcol}")
    if undated:
        rep.error(f"no date for {len(undated)} mapped field(s) "
                  f"({', '.join(sorted(undated)[:5])}): set the form's date field "
                  f"in project.json, or date_field in the mapping")
    if bad_date_col:
        rep.error(f"date_field not in the export for: {', '.join(sorted(bad_date_col)[:5])}")
    if absent:
        rep.warn(f"{len(absent)} mapped column(s) are not in the export and "
                 f"produce nothing: {', '.join(sorted(absent)[:8])}"
                 + (" ..." if len(absent) > 8 else ""))

    # A labels export writes "Yes" and "Checked" where a raw export writes 1.
    labels_fields, odd_fields = [], []
    for field, grp in m[m["field_type"].isin(CHOICE_TYPES)].groupby("field", sort=False):
        ftype = grp["field_type"].iloc[0]
        if ftype == "checkbox":
            for code in grp["choice_code"]:
                c = checkbox_column(field, code, cols)
                if c and not set(df[c].unique()) <= {"", "0", "1"}:
                    labels_fields.append(c)
            continue
        if field not in cols:
            continue
        seen = set(df[field].unique()) - {""}
        codes = set(grp["choice_code"])
        labels = set(grp["choice_label"].str.lower())
        unknown = {v for v in seen if not any(same_code(pd.Series([v]), c).iloc[0]
                                               for c in codes)}
        if unknown & {v for v in seen if v.lower() in labels}:
            labels_fields.append(field)
        elif unknown:
            odd_fields.append(f"{field} ({sorted(unknown)[:3]})")
    if labels_fields:
        rep.error(f"this looks like a LABELS export: {len(labels_fields)} field(s) "
                  f"hold answer text instead of codes (e.g. {labels_fields[0]}). "
                  f"Export again with 'raw data'.")
    if odd_fields:
        rep.warn(f"{len(odd_fields)} choice field(s) hold codes the data dictionary "
                 f"does not list: {', '.join(odd_fields[:5])}")

    known = set(m["field"])
    unknown_cols = [c for c in df.columns if not REDCAP_SYSTEM.match(c)
                    and c not in known and c.split("___")[0] not in known]
    if unknown_cols:
        rep.note(f"{len(unknown_cols)} export column(s) are not in the mapping and "
                 f"are ignored (e.g. {unknown_cols[0]})")


def row_date_field(r, project: dict) -> str:
    if r.field_type == "text" and r.validation.startswith("date") and not r.choice_code:
        return r.field
    if r.date_field:
        return r.date_field
    return project.get("forms", {}).get(r.form, {}).get("date_field", "")


# ---------------------------------------------------------------------------
# Building tables
# ---------------------------------------------------------------------------
def build(project: dict, m: pd.DataFrame, df: pd.DataFrame, rep: Report,
          hide_source_ids: bool) -> dict[str, pd.DataFrame]:
    rid = project["record_id_field"]
    type_concept = to_int(project.get("type_concept_id", 32879), 32879)
    min_year = to_int(project.get("min_year", 1900), 1900)
    max_date = pd.Timestamp(date.today())
    person_cfg = project.get("person", {})
    out: dict[str, pd.DataFrame] = {}
    date_cache: dict[str, pd.Series] = {}
    field_form = dict(zip(m["field"], m["form"]))

    def dates(col: str) -> pd.Series:
        if col not in date_cache:
            date_cache[col] = parse_dates(df[col], f"date field {col}", rep,
                                          min_year, max_date)
        return date_cache[col]

    # ---- PERSON -------------------------------------------------------------
    records = df.loc[df[rid] != "", rid].drop_duplicates()
    p = pd.DataFrame({rid: records.values})
    if person_cfg.get("birth_date_field"):
        f = person_cfg["birth_date_field"]
        dob = first_per_record(df.assign(_d=dates(f).dt.strftime("%Y-%m-%d").fillna("")),
                               rid, "_d")
        p["_dob"] = pd.to_datetime(p[rid].map(dob), errors="coerce")
        p["year_of_birth"] = p["_dob"].dt.year
        p["month_of_birth"] = p["_dob"].dt.month
        p["day_of_birth"] = p["_dob"].dt.day
        p["birth_datetime"] = p["_dob"].dt.strftime("%Y-%m-%d 00:00:00")
    else:
        f = person_cfg["birth_year_field"]
        yob = pd.to_numeric(p[rid].map(first_per_record(df, rid, f)), errors="coerce")
        yob = yob.where((yob >= min_year) & (yob <= max_date.year))
        p["year_of_birth"] = yob
    dropped = p["year_of_birth"].isna()
    if dropped.any():
        rep.warn(f"{int(dropped.sum()):,} record(s) have no usable birth date or "
                 f"year and are left out of PERSON and every other table")
    p = p[~dropped].reset_index(drop=True)
    for c in ("year_of_birth", "month_of_birth", "day_of_birth"):
        if c in p.columns:
            p[c] = p[c].astype("Int64")
    p["person_id"] = range(1, len(p) + 1)
    pid = dict(zip(p[rid], p["person_id"]))

    def coded(field_key: str, map_key: str):
        field = person_cfg.get(field_key)
        if not field:
            return pd.Series(0, index=p.index), pd.Series("", index=p.index)
        raw = p[rid].map(first_per_record(df, rid, field)).fillna("")
        cmap = {str(k): to_int(v) for k, v in (person_cfg.get(map_key) or {}).items()}
        return raw.map(cmap).fillna(0).astype(int), raw

    p["gender_concept_id"], p["gender_source_value"] = coded("sex_field", "sex_concepts")
    p["race_concept_id"], p["race_source_value"] = coded("race_field", "race_concepts")
    p["ethnicity_concept_id"], p["ethnicity_source_value"] = coded(
        "ethnicity_field", "ethnicity_concepts")
    if (p["gender_concept_id"] == 0).any():
        rep.note(f"{int((p['gender_concept_id'] == 0).sum()):,} person(s) have "
                 f"gender_concept_id 0 (blank, or a code with no concept in "
                 f"person.sex_concepts)")
    p["person_source_value"] = "" if hide_source_ids else p[rid]
    for c in ("gender_source_concept_id", "race_source_concept_id",
              "ethnicity_source_concept_id"):
        p[c] = 0
    out["person"] = p.reindex(columns=CDM["person"])

    df = df[df[rid].isin(pid)].copy()
    df["_person_id"] = df[rid].map(pid)
    date_cache.clear()   # parsed against the unfiltered export

    # ---- VISIT_OCCURRENCE ---------------------------------------------------
    visit_rows, visit_key = [], {}
    for form, cfg in project.get("forms", {}).items():
        vc = to_int(cfg.get("visit_concept_id", 0))
        f = cfg.get("date_field")
        if vc <= 0 or not f or f not in df.columns:
            continue
        d = dates(f).reindex(df.index)
        sub = pd.DataFrame({"person_id": df["_person_id"], "d": d}).dropna()
        sub = sub.drop_duplicates()
        for r in sub.itertuples(index=False):
            visit_key[(r.person_id, form, r.d)] = len(visit_rows) + 1
            visit_rows.append({"visit_occurrence_id": len(visit_rows) + 1,
                               "person_id": r.person_id, "visit_concept_id": vc,
                               "visit_start_date": r.d, "visit_end_date": r.d,
                               "visit_type_concept_id": type_concept,
                               "visit_source_value": form[:50],
                               "visit_source_concept_id": 0,
                               "admitted_from_concept_id": 0,
                               "discharged_to_concept_id": 0})
    v = pd.DataFrame(visit_rows) if visit_rows else empty("visit_occurrence")
    if visit_rows:
        v["visit_start_datetime"] = v["visit_start_date"].dt.strftime("%Y-%m-%d 00:00:00")
        v["visit_end_datetime"] = v["visit_start_datetime"]
        v["visit_start_date"] = fmt_date(v["visit_start_date"])
        v["visit_end_date"] = fmt_date(v["visit_end_date"])
    out["visit_occurrence"] = v.reindex(columns=CDM["visit_occurrence"])

    # ---- Clinical tables ----------------------------------------------------
    events = {t: [] for t in CLINICAL}
    no_date, non_numeric = {}, {}
    rows = m[m["omop_table"].isin(CLINICAL) & (m["concept_id_int"] > 0)]
    for r in rows.itertuples(index=False):
        if r.field_type == "checkbox":
            col = checkbox_column(r.field, r.choice_code, df.columns)
            if col is None:
                continue
            hit = df[col] == "1"
        elif r.field not in df.columns:
            continue
        elif r.choice_code:
            hit = same_code(df[r.field], r.choice_code)
        else:
            hit = df[r.field] != ""
        if not hit.any():
            continue
        dcol = row_date_field(r, project)
        if not dcol or dcol not in df.columns:
            continue
        d = dates(dcol)[hit]
        if field_form.get(dcol, r.form) != r.form:
            # A date from another form may sit on a different row of the same
            # record (longitudinal projects, repeating forms): fall back to the
            # record's first date.
            per_rec = first_per_record(df.assign(_d=dates(dcol).dt.strftime(
                "%Y-%m-%d").fillna("")), rid, "_d")
            fb = pd.to_datetime(df.loc[hit, rid].map(per_rec), errors="coerce")
            d = d.fillna(fb)
        missing = d.isna()
        if missing.any():
            no_date[r.field] = no_date.get(r.field, 0) + int(missing.sum())
        idx = d[~missing].index
        if not len(idx):
            continue
        visit_date = d[~missing]
        event_date = visit_date + pd.to_timedelta(r.date_offset_days_int, unit="D")
        form_date_field = project.get("forms", {}).get(r.form, {}).get("date_field")
        src = f"{r.field}={r.choice_code}" if r.choice_code else r.field
        ev = pd.DataFrame({
            "person_id": df.loc[idx, "_person_id"].values,
            "date": event_date.values,
            "visit_date": visit_date.values,
            "concept_id": r.concept_id_int,
            "value_concept_id": r.value_concept_id_int,
            "unit_concept_id": r.unit_concept_id_int,
            "source_value": src[:50],
            "raw": df.loc[idx, r.field].values if r.field in df.columns else "",
            "form": r.form,
            "visit_ok": dcol == form_date_field,
        })
        if r.omop_table in ("MEASUREMENT", "OBSERVATION") and not r.choice_code \
                and not r.validation.startswith("date"):
            num = pd.to_numeric(ev["raw"], errors="coerce")
            if r.omop_table == "MEASUREMENT":
                bad = num.isna()
                if bad.any():
                    non_numeric[r.field] = int(bad.sum())
                ev = ev[~bad].assign(value_as_number=num[~bad])
            else:
                ev["value_as_number"] = num
                ev["value_as_string"] = ev["raw"].where(num.isna(), "").str[:60]
        events[r.omop_table].append(ev)

    for field, n in no_date.items():
        rep.warn(f"{field}: {n:,} answer(s) have no usable date and were left out")
    for field, n in non_numeric.items():
        rep.warn(f"{field}: {n:,} non-numeric value(s) cannot go in MEASUREMENT "
                 f"and were left out")

    all_dates = []
    for table, parts in events.items():
        fname, pre, dcol_out, dtcol = CLINICAL_COLS[table]
        if not parts:
            out[fname] = empty(fname)
            continue
        e = pd.concat(parts, ignore_index=True)
        e = e.sort_values(["person_id", "date", "concept_id"], kind="stable")
        e = e.reset_index(drop=True)
        all_dates.append(e[["person_id", "date"]])
        t = pd.DataFrame({f"{fname}_id": range(1, len(e) + 1),
                          "person_id": e["person_id"],
                          f"{pre}_concept_id": e["concept_id"],
                          dcol_out: fmt_date(e["date"]),
                          dtcol: e["date"].dt.strftime("%Y-%m-%d 00:00:00"),
                          f"{pre}_type_concept_id": type_concept,
                          f"{pre}_source_value": e["source_value"],
                          f"{pre}_source_concept_id": 0})
        if table == "CONDITION_OCCURRENCE":
            t["condition_status_concept_id"] = 0
        if table == "PROCEDURE_OCCURRENCE":
            t["modifier_concept_id"] = 0
        if table == "DRUG_EXPOSURE":
            # CDM v5.4 requires an end date; a registry tick gives one day.
            t["drug_exposure_end_date"] = t["drug_exposure_start_date"]
            t["drug_exposure_end_datetime"] = t["drug_exposure_start_datetime"]
            t["route_concept_id"] = 0
        if table in ("OBSERVATION", "MEASUREMENT"):
            t["value_as_number"] = e.get("value_as_number")
            t["value_as_concept_id"] = e["value_concept_id"]
            t["unit_concept_id"] = e["unit_concept_id"]
            t["value_source_value"] = e["raw"].astype(str).str[:50]
        if table == "OBSERVATION":
            t["value_as_string"] = e.get("value_as_string")
            t["qualifier_concept_id"] = 0
        if table == "MEASUREMENT":
            t["operator_concept_id"] = 0
        vids = [visit_key.get((pp, f, vd)) if ok else None for pp, f, vd, ok in
                zip(e["person_id"], e["form"], e["visit_date"], e["visit_ok"])]
        t["visit_occurrence_id"] = pd.array(vids, dtype="Int64")
        out[fname] = t.reindex(columns=CDM[fname])

    # ---- DEATH --------------------------------------------------------------
    death = project.get("death") or {}
    if death.get("flag_field") and death["flag_field"] in df.columns:
        flagged = df[same_code(df[death["flag_field"]], str(death.get("flag_code", "1")))]
        people = flagged["_person_id"].drop_duplicates()
        dd = pd.Series(pd.NaT, index=people.index)
        if death.get("date_field") and death["date_field"] in df.columns:
            d = dates(death["date_field"])
            per_person = (pd.DataFrame({"pid": df["_person_id"], "d": d}).dropna()
                          .sort_values("d").drop_duplicates("pid").set_index("pid")["d"])
            dd = people.map(per_person)
        dated = dd.notna()
        if (~dated).any():
            rep.warn(f"{int((~dated).sum()):,} person(s) are flagged deceased with no "
                     f"usable death date. CDM v5.4 requires death_date, so they are "
                     f"not in DEATH")
        dt = pd.DataFrame({"person_id": people[dated].values, "d": dd[dated].values})
        dt = dt.sort_values("person_id")
        out["death"] = pd.DataFrame({
            "person_id": dt["person_id"], "death_date": fmt_date(dt["d"]),
            "death_datetime": dt["d"].dt.strftime("%Y-%m-%d 00:00:00"),
            "death_type_concept_id": type_concept, "cause_concept_id": 0,
            "cause_source_concept_id": 0}).reindex(columns=CDM["death"])
        all_dates.append(dt.rename(columns={"d": "date"}))
    else:
        out["death"] = empty("death")

    # ---- OBSERVATION_PERIOD -------------------------------------------------
    if visit_rows:
        vv = pd.DataFrame(visit_rows)[["person_id", "visit_start_date"]]
        all_dates.append(vv.rename(columns={"visit_start_date": "date"}))
    if all_dates:
        a = pd.concat(all_dates, ignore_index=True).dropna()
        g = a.groupby("person_id")["date"].agg(["min", "max"]).reset_index()
        op = pd.DataFrame({"observation_period_id": range(1, len(g) + 1),
                           "person_id": g["person_id"],
                           "observation_period_start_date": fmt_date(g["min"]),
                           "observation_period_end_date": fmt_date(g["max"]),
                           "period_type_concept_id": type_concept})
        without = len(p) - len(g)
        if without:
            rep.note(f"{without:,} person(s) have no dated record, so no "
                     f"OBSERVATION_PERIOD")
        out["observation_period"] = op.reindex(columns=CDM["observation_period"])
    else:
        out["observation_period"] = empty("observation_period")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--mapping", type=Path, required=True)
    ap.add_argument("--export", type=Path, nargs="+", required=True)
    ap.add_argument("--output", type=Path, default=Path("omop_output"))
    ap.add_argument("--check", action="store_true",
                    help="validate the inputs and write nothing")
    ap.add_argument("--hide-source-ids", action="store_true",
                    help="leave person_source_value blank instead of the record id")
    args = ap.parse_args(argv)

    rep = Report()
    project = json.loads(args.project.read_text(encoding="utf-8-sig"))
    if project.get("format") != FORMAT:
        rep.warn(f"{args.project.name} does not declare format '{FORMAT}'")
    m = read_mapping(args.mapping, rep)
    df = read_exports(args.export)
    rep.note(f"export: {len(df):,} rows x {df.shape[1]:,} columns from "
             f"{len(args.export)} file(s)")
    if not rep.errors:
        preflight(project, m, df, rep)
    if rep.errors or args.check:
        print("\n".join(rep.lines()))
        print("\nBlocking problems found; nothing written." if rep.errors
              else "\nCheck passed. Run again without --check to write the tables.")
        return 1 if rep.errors else 0

    tables = build(project, m, df, rep, args.hide_source_ids)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, t in tables.items():
        t.to_csv(args.output / f"{name}.csv", index=False)
        rep.counts[name] = len(t)
    rep.write(args.output / "qc_report.md")
    print("\n".join(rep.lines()))
    print(f"\nWrote {len(tables)} tables and qc_report.md to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
