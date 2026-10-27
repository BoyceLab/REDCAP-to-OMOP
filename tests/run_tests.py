"""
run_tests.py: end-to-end tests of the kit on the fictional demo registry.

    python tests/run_tests.py

Needs only Python and pandas, so it runs in CI on every push. The wizard has its
own browser test, tests/run_wizard_tests.py, which needs a Chromium browser.
The vocabulary in tests/fixtures/vocab/ holds only the 27 concepts the demo
names, cut from an Athena download, so the concept check can be tested without
the full release.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo"
FIX = ROOT / "tests" / "fixtures" / "vocab"
PY = sys.executable
FAILS: list[str] = []


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runner = load(ROOT / "runner" / "redcap_to_omop.py")
start = load(ROOT / "tools" / "start_mapping.py")
checker = load(ROOT / "tools" / "check_concepts.py")


def test(name):
    def wrap(fn):
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            FAILS.append(name)
            print(f"FAIL  {name}: {e}")
        except Exception as e:                      # noqa: BLE001
            FAILS.append(name)
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
        return fn
    return wrap


def run(args: list[str]) -> tuple[int, str]:
    p = subprocess.run([PY, *args], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


RUNNER_ARGS = ["runner/redcap_to_omop.py", "--project", "examples/demo/project.json",
               "--mapping", "examples/demo/mapping.csv", "--export", "examples/demo/export.csv"]


@test("start_mapping.py turns the data dictionary into mapping_blank.csv")
def _():
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "m.csv"
        code, msg = run(["tools/start_mapping.py", "--dd", "examples/demo/data_dictionary.csv",
                         "--output", str(out)])
        assert code == 0, msg
        assert out.read_bytes() == (DEMO / "mapping_blank.csv").read_bytes(), \
            "regenerated mapping differs from examples/demo/mapping_blank.csv"


@test("the demo mapping changes only the decision columns of the blank one")
def _():
    blank, done = rows(DEMO / "mapping_blank.csv"), rows(DEMO / "mapping.csv")
    keys = ["form", "field", "field_type", "validation", "field_label", "choice_code", "choice_label"]
    assert [[r[k] for k in keys] for r in blank] == [[r[k] for k in keys] for r in done]


@test("the wizard's embedded copy of the demo is current")
def _():
    code, msg = run(["tools/embed_demo.py", "--check"])
    assert code == 0, msg


@test("make_export.py reproduces export.csv exactly")
def _():
    before = hashlib.sha256((DEMO / "export.csv").read_bytes()).hexdigest()
    code, msg = run(["examples/demo/make_export.py"])
    assert code == 0, msg
    after = hashlib.sha256((DEMO / "export.csv").read_bytes()).hexdigest()
    assert before == after, "export.csv changed when regenerated"


@test("runner --check passes on the demo")
def _():
    code, msg = run(RUNNER_ARGS + ["--check"])
    assert code == 0, msg


EXPECTED = {"person": 38, "observation_period": 38, "visit_occurrence": 80,
            "condition_occurrence": 65, "drug_exposure": 60, "procedure_occurrence": 54,
            "observation": 37, "measurement": 117, "death": 2}


@test("runner writes the expected OMOP tables for the demo")
def _():
    with tempfile.TemporaryDirectory() as t:
        code, msg = run(RUNNER_ARGS + ["--output", t])
        assert code == 0, msg
        got = {}
        for table, cols in runner.CDM.items():
            r = rows(Path(t) / f"{table}.csv")
            with (Path(t) / f"{table}.csv").open(encoding="utf-8") as fh:
                assert fh.readline().strip().split(",") == cols, f"{table}: columns differ from CDM v5.4"
            got[table] = len(r)
        assert got == EXPECTED, f"row counts {got}"
        person = rows(Path(t) / "person.csv")
        assert len({p["person_id"] for p in person}) == len(person), "person_id not unique"
        assert all(p["year_of_birth"].isdigit() for p in person), "year_of_birth not an integer"
        drugs = rows(Path(t) / "drug_exposure.csv")
        assert all(d["drug_exposure_end_date"] == d["drug_exposure_start_date"] for d in drugs)
        assert {d["drug_concept_id"] for d in drugs} == {"711584", "705103", "745466", "742267"}
        for table in ("condition_occurrence", "drug_exposure", "procedure_occurrence",
                      "observation", "measurement", "visit_occurrence"):
            for r in rows(Path(t) / f"{table}.csv"):
                d = next(v for k, v in r.items() if k.endswith(("_start_date", "_date")) and v)
                assert "1900-01-01" <= d <= "2100-01-01", f"{table}: implausible date {d}"
        meas = rows(Path(t) / "measurement.csv")
        assert {m["unit_concept_id"] for m in meas} == {"9529", "8582"}
        obs = rows(Path(t) / "observation.csv")
        assert {o["value_as_concept_id"] for o in obs} == {"4188539", "4188540"}
        report = (Path(t) / "qc_report.md").read_text(encoding="utf-8")
        for expect in ("no usable birth date", "before 1900", "flagged deceased with no usable death date"):
            assert expect in report, f"qc_report.md does not mention '{expect}'"


@test("runner refuses a labels export")
def _():
    with tempfile.TemporaryDirectory() as t:
        src = rows(DEMO / "export.csv")
        for r in src:
            r["sex"] = {"1": "Male", "2": "Female", "3": "Other"}.get(r["sex"], r["sex"])
            for k in r:
                if k.startswith("comorbid___"):
                    r[k] = {"1": "Checked", "0": "Unchecked"}.get(r[k], r[k])
        out = Path(t) / "labels.csv"
        with out.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(src[0].keys()))
            w.writeheader(); w.writerows(src)
        code, msg = run(["runner/redcap_to_omop.py", "--project", "examples/demo/project.json",
                         "--mapping", "examples/demo/mapping.csv", "--export", str(out), "--check"])
        assert code == 1 and "LABELS export" in msg, msg


@test("runner reports a mapped column missing from the export")
def _():
    with tempfile.TemporaryDirectory() as t:
        src = rows(DEMO / "export.csv")
        cols = [c for c in src[0] if not c.startswith("meds___")]
        out = Path(t) / "nomeds.csv"
        with out.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader(); w.writerows(src)
        code, msg = run(["runner/redcap_to_omop.py", "--project", "examples/demo/project.json",
                         "--mapping", "examples/demo/mapping.csv", "--export", str(out), "--check"])
        assert code == 0 and "meds___1" in msg and "not in the export" in msg, msg


def check_with_fixture(mapping: str) -> list[dict]:
    with (DEMO / mapping).open(encoding="utf-8") as fh:
        m = list(csv.DictReader(fh))
    import json
    project = json.loads((DEMO / "project.json").read_text(encoding="utf-8"))
    used = checker.uses(m, project)
    vocab = checker.read_concepts(FIX / "CONCEPT.csv", {u["concept_id"] for u in used})
    return checker.check(used, vocab)


@test("check_concepts finds nothing wrong with the demo mapping")
def _():
    issues = check_with_fixture("mapping.csv")
    assert not issues, [i["message"] for i in issues]


@test("check_concepts finds every planted mistake, and only those")
def _():
    got = {(i["severity"], i["field"], i["choice_code"], i["concept_id"]) for i in
           check_with_fixture("mapping_with_mistakes.csv")}
    want = {("error", "primary_dx", "2", 99999999), ("error", "comorbid", "1", 4152194),
            ("error", "comorbid", "3", 766349), ("error", "meds", "1", 1546440),
            ("error", "weight", "", 3025315), ("warning", "comorbid", "4", 442077),
            ("warning", "comorbid", "99", 442077), ("review", "comorbid", "1", 4152194),
            ("review", "meds", "1", 1546440)}
    assert got == want, f"missing {want - got}, unexpected {got - want}"


@test("check_concepts proposes the Maps to target of a non-standard id")
def _():
    with tempfile.TemporaryDirectory() as t:
        rep = Path(t) / "r.csv"
        code, msg = run(["tools/check_concepts.py", "--mapping", "examples/demo/mapping_with_mistakes.csv",
                         "--vocab", str(FIX), "--replacements", "--report", str(rep)])
        assert code == 1, msg
        sugg = {r["concept_id"]: r["suggestion"] for r in rows(rep)}
        assert sugg.get("766349", "").startswith("440383 Depressive disorder"), sugg


if FAILS:
    print(f"\n{len(FAILS)} test(s) failed")
    sys.exit(1)
print("\nAll tests passed")
