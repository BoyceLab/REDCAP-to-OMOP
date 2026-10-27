# Guide: mapping a REDCap registry to OMOP

This kit takes a REDCap project to the OMOP Common Data Model v5.4 in five steps.
The wizard (`docs/index.html`) does steps 1 to 5 in a browser; every step also has
a command-line equivalent for people who prefer a spreadsheet and a terminal.

The worked example throughout is the fictional demo registry in `examples/demo/`.

*Text drafted with AI assistance.*

## What you need

| | Where it comes from | Holds patient data? |
|---|---|---|
| REDCap data dictionary | Project Setup, Data Dictionary, *Download the current Data Dictionary* | No |
| REDCap data export | Data Exports, Reports, and Stats, *All data*, **CSV / Microsoft Excel (raw data)** | Yes |
| OMOP vocabulary | [athena.ohdsi.org](https://athena.ohdsi.org/vocabulary/list): free account, choose vocabularies, download link by email | No |
| Python 3.9 or newer with pandas | `pip install -r requirements.txt` | |

The mapping work (steps 1 to 4) needs only the data dictionary and the vocabulary.
The patient data is needed only in step 5, so the mapping can be built anywhere and
the runner taken to wherever the data is allowed to be.

For the vocabulary download, SNOMED, LOINC, RxNorm, UCUM, Gender, Race, Ethnicity,
Visit and Type Concept cover most registries. Add ICD10CM or other source
vocabularies only if you want to look codes up; the mapping itself needs standard
concepts.

## Step 1: start the mapping

Load the data dictionary in the wizard, or:

```bash
python tools/start_mapping.py --dd data_dictionary.csv --output mapping.csv
```

Either way you get one row per field, and one row per answer of a radio, dropdown,
yes/no or checkbox field. The first field, the record id, is marked `SKIP`.
Descriptive and file-upload fields are left out because they hold no answers.

One row per answer is the point. In OMOP a recorded answer is usually a fact about
the person (*has hypertension*, *takes lamotrigine*), and each answer needs its own
concept. For checkbox fields this also matches what REDCap exports: one column per
answer, `comorbid___1`, `comorbid___2`, holding 0 or 1. The runner reads those
columns directly, so export exactly as REDCap writes it and rename nothing.

## Step 2: project settings

`project.json` says how to build PERSON and how to date everything else. The
wizard guesses it from field names and labels; check every guess.

```json
{
  "format": "redcap-omop-mapping-kit/1",
  "registry_name": "Demo neurology registry (fictional)",
  "record_id_field": "record_id",
  "type_concept_id": 32879,
  "min_year": 1900,
  "person": {
    "birth_date_field": "dob",
    "sex_field": "sex",
    "sex_concepts": {"1": 8507, "2": 8532, "3": 0}
  },
  "forms": {
    "enrollment":   {"date_field": "enroll_date", "visit_concept_id": 0},
    "baseline":     {"date_field": "base_date",   "visit_concept_id": 9202},
    "follow_up":    {"date_field": "fu_date",     "visit_concept_id": 9202},
    "end_of_study": {"date_field": "",            "visit_concept_id": 0}
  },
  "death": {"flag_field": "deceased", "flag_code": "1", "date_field": "death_date"}
}
```

| Key | Meaning |
|---|---|
| `record_id_field` | The REDCap record id; one PERSON per distinct value. |
| `person.birth_date_field` or `person.birth_year_field` | OMOP requires a year of birth. Records without one are left out of every table, and counted. |
| `person.sex_field`, `person.sex_concepts` | Code to concept: 8507 MALE, 8532 FEMALE. Anything else is 0, with the code kept in `gender_source_value`. |
| `person.race_field`, `race_concepts`, `ethnicity_field`, `ethnicity_concepts` | Optional, same pattern. |
| `forms.<form>.date_field` | The date every answer on that form gets. A mapped field with no date produces nothing, so every form with mapped fields needs one (or each row a `date_field`). |
| `forms.<form>.visit_concept_id` | Non-zero makes each dated form a VISIT_OCCURRENCE (9202 Outpatient Visit, 9201 Inpatient Visit) and links its records to it. |
| `death` | Who is deceased and when. CDM v5.4 requires a date of death, so a flag without a date is counted and left out of DEATH. |
| `type_concept_id` | Provenance of every record; 32879 is *Registry*. |
| `min_year` | Dates before this year, and dates in the future, are treated as typing errors. |

## Step 3: map each row

For each row choose an OMOP table and a concept id, or `SKIP`, or leave it
undecided. A row produces records only when it has a table and a concept id above 0.

**The table follows the concept's domain.** A Condition-domain concept goes to
CONDITION_OCCURRENCE, a Drug concept to DRUG_EXPOSURE, and so on, whatever the
question felt like. Seizure types, for example, are SNOMED clinical findings in the
Condition domain, so they belong in CONDITION_OCCURRENCE, not OBSERVATION. The check
in step 4 enforces this.

**Use standard concepts only.** Search Athena with *Standard* ticked; the wizard's
"Search Athena" link sets that filter for you. Source codes (ICD-10-CM, local codes)
are non-standard and do not belong in `*_concept_id` columns.

Patterns that cover most registry fields:

| REDCap field | Typical mapping | Demo example |
|---|---|---|
| checkbox list of conditions or drugs | one concept per answer; the answer ticked means the fact is recorded | `comorbid = 1` Hypertension, 316866 Hypertensive disorder |
| radio diagnosis | one concept per answer, dated by a diagnosis date | `primary_dx = 1` Epilepsy, 380378, `date_field` dx_date |
| yes/no "was X done" | map Yes to the procedure or finding; SKIP No | `eeg_done = 1` 4181917 Electroencephalogram |
| yes/no or radio status question | question concept with an answer concept in `value_concept_id` | `smoker` 43054909 Tobacco smoking status, value 4188539 Yes or 4188540 No |
| number | MEASUREMENT (or OBSERVATION) with `unit_concept_id` | `weight` 3025315 Body weight, unit 9529 kilogram |
| date of an event | the field itself, mapped field-level; it is its own date | |
| free text, "Other (specify)" | SKIP, or 0 until someone reviews the text | `meds_other` |
| "Other" answer | 0: there is rarely one standard concept for "other" | `comorbid = 99` |

**Undecided or 0 is better than a guess.** An unmapped answer is simply absent from
the CDM, and a cohort built on it under-counts. A wrong concept puts the answer in
the CDM under another meaning, and nothing downstream can tell. The runner and the
check both report unmapped rows, so they stay visible as open work.

**Dates.** Every row is dated by its form's date field unless the row sets
`date_field`, or the field is itself a date. `date_offset_days` shifts the date,
for example `-365` for "history of" items asked at enrollment, so they do not appear
to start on the day of the visit. Choose offsets deliberately and write them down
in `notes`.

**Same answer list, same concepts.** The wizard's "Copy mappings to fields with the
same answer list" fills undecided rows from a field with an identical answer list,
for example a follow-up medication list that repeats the baseline one. It refuses
lists made of vague answers (Yes, No, Current, Past, Don't know), because those
mean something different on every question.

## Step 4: check every concept id

In the wizard, choose `CONCEPT.csv` from your Athena download (and, optionally and
first, `CONCEPT_RELATIONSHIP.csv`). The page reads the file from your disk as a
stream and keeps only the ids the mapping uses; the full release takes about a
minute. Or:

```bash
python tools/check_concepts.py --mapping mapping.csv --project project.json \
    --vocab /path/to/athena_download --replacements --write-names mapping_named.csv
```

Both apply the same rules:

| Severity | Rule |
|---|---|
| error | the id is not in this vocabulary release |
| error | the id is deprecated or upgraded (`invalid_reason` D or U) |
| error | the id is non-standard |
| error | the id's domain does not match the table the row is written to |
| warning | the same id is used for answers with different labels |
| review | the row's label and the vocabulary's name share no words |

After a check, the name columns carry the vocabulary's own name for each id. Keep
it that way: a name column that records what the id was *meant* to be hides a
wrong id, and a name taken from the vocabulary shows it.

The demo file `examples/demo/mapping_with_mistakes.csv` has six planted mistakes;
"Demo with mistakes" in the wizard loads it. The check finds a Race concept used as
a drug, a blood pressure measurement used as a diagnosis, an id that does not
exist, an ICD-10-CM code (with its standard replacement proposed), one concept
reused for two different answers, and a Measurement concept sent to OBSERVATION.

Run the check again whenever you change ids, and whenever you move to a new
vocabulary release: concepts are retired and replaced between releases.

## Step 5: run it

On the computer where the data is allowed to be:

```bash
pip install -r requirements.txt
python runner/redcap_to_omop.py --project project.json --mapping mapping.csv \
    --export export.csv --check
python runner/redcap_to_omop.py --project project.json --mapping mapping.csv \
    --export export.csv --output omop_output
```

`--check` validates and writes nothing. It stops on a labels export, a project
field missing from the export, or a mapped field with no date, and warns about mapped
columns the export does not have and answer codes the dictionary does not list.

The run writes PERSON, OBSERVATION_PERIOD, VISIT_OCCURRENCE, CONDITION_OCCURRENCE,
DRUG_EXPOSURE, PROCEDURE_OCCURRENCE, OBSERVATION, MEASUREMENT and DEATH as CSV with
the CDM v5.4 columns, every table even when empty, plus `qc_report.md` listing
everything left out and why. `--hide-source-ids` leaves `person_source_value` blank
instead of writing the REDCap record id.

To try it end to end on the demo:

```bash
python runner/redcap_to_omop.py --project examples/demo/project.json \
    --mapping examples/demo/mapping.csv --export examples/demo/export.csv \
    --output omop_output
```

The demo's QC report shows the problems planted in its fictional export: two
records with no date of birth, one mistyped year (1506), and one person flagged
deceased with no date.

## Traps worth knowing

Found while mapping a real clinical registry; each produced wrong or missing data
without any error message.

- **A labels export.** REDCap can export "Yes" and "Checked" instead of 1. Every
  code comparison then fails and the tables come out nearly empty. The runner
  refuses such an export.
- **Checkbox columns.** A mapping that names `comorbid` matches nothing in an
  export that has `comorbid___1` to `comorbid___99`. This kit maps each answer to
  its own column.
- **Answer codes that change meaning.** In one registry, code 1 meant *Yes (past)*
  and 2 meant *Yes (current)*. Map answers by their label, not by assuming 1 is Yes.
- **Ids that look plausible.** Ids were found pointing at a race concept, an airbag
  injury, and a lymph-node finding, while the name column beside them recorded the
  intended drug. Only a check against the vocabulary finds these.
- **One id reused as a bucket.** A single concept reused for dozens of unrelated
  fields because it was "close enough". The reuse warning catches it.
- **Concepts retired between releases.** An id that was standard in one release is
  replaced in the next. Check against the release you will load into.
- **Impossible dates.** A mistyped year (1506 for 2006) parses as a valid date.
  The runner ignores dates before `min_year` and in the future, and counts them.
- **"Don't know" written as Yes.** A hard-coded answer concept that was the LOINC
  answer *Yes*. Answer concepts belong in the mapping, where the check sees them.
- **Death without a date.** CDM v5.4 requires one. Decide how to handle flags
  without dates; the runner reports them rather than inventing a date.

## Reference: mapping.csv

| Column | Filled by | Meaning |
|---|---|---|
| `form`, `field`, `field_type`, `validation`, `field_label` | step 1 | from the data dictionary |
| `choice_code`, `choice_label` | step 1 | the answer, for choice fields; blank for a field-level row |
| `omop_table` | you | CONDITION_OCCURRENCE, DRUG_EXPOSURE, PROCEDURE_OCCURRENCE, OBSERVATION, MEASUREMENT, SKIP, or blank (undecided) |
| `concept_id` | you | standard concept; 0 or blank produces nothing |
| `value_concept_id` | you | OBSERVATION and MEASUREMENT: `value_as_concept_id`, for an answer concept |
| `unit_concept_id` | you | OBSERVATION and MEASUREMENT: `unit_concept_id` |
| `*_concept_name` | the check | the vocabulary's name for each id |
| `date_field` | you | overrides the form's date field for this row |
| `date_offset_days` | you | shifts the date by this many days |
| `notes` | you | why; read by people, not by the runner |

A choice row produces a record when the answer is selected (for a checkbox, when
its column holds 1). A field-level row produces a record whenever the field is not
blank: numeric values go to `value_as_number`, other OBSERVATION values to
`value_as_string`, and MEASUREMENT rows skip non-numeric values and count them.

## Caution

- **The check is not clinical review.** It confirms an id exists, is standard, and
  sits in the right table, and flags labels that do not resemble the concept's name.
  Whether the concept is the right one for the question, at the right level of
  detail, is a clinical decision.
- **The runner covers the common cases, not every REDCap design.** It does not read
  free text, doses, routes, providers or care sites, and it dates each answer by one
  date field. Projects that need more can use the mapping file as the input to their
  own ETL.
- **No vocabulary is included.** Each user downloads the OMOP vocabularies from
  Athena under its license terms. The test fixture holds only the 27 concepts the
  demo names.
- **The output is identifiable data** when the export is. `person_source_value` holds
  the REDCap record id unless `--hide-source-ids` is used, and dates are real dates.
  Keep the output where the export is allowed to be.
