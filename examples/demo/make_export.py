"""
make_export.py: write export.csv, a FICTIONAL REDCap export for the demo registry.

    python examples/demo/make_export.py

Every value is generated from a fixed random seed. No record describes a real
person. The layout is what REDCap writes for a classic project with one
repeating instrument (follow_up): one row per record for the non-repeating
forms, then one row per follow-up instance, and checkbox fields as one 0/1
column per choice (comorbid___1, comorbid___2, ...).

A few records carry the problems a real export has, so the demo shows how
they are reported: two have no date of birth, one has a mistyped year, and one
is marked deceased with no date of death.
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20261027
N = 40
CHOICES = ["1", "2", "3", "4", "99"]
COLUMNS = (
    ["record_id", "redcap_repeat_instrument", "redcap_repeat_instance",
     "dob", "sex", "enroll_date", "primary_dx", "dx_date", "enrollment_complete",
     "base_date"]
    + [f"comorbid___{c}" for c in CHOICES] + [f"meds___{c}" for c in CHOICES]
    + ["meds_other", "weight", "height", "smoker", "eeg_done", "mri_done",
       "sz_per_month", "baseline_complete", "fu_date"]
    + [f"fu_new_meds___{c}" for c in CHOICES]
    + ["fu_weight", "fu_hosp", "follow_up_complete", "deceased", "death_date",
       "study_notes", "end_of_study_complete"]
)


def day(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def main() -> None:
    rng = random.Random(SEED)
    rows = []
    for i in range(1, N + 1):
        base = {c: "" for c in COLUMNS}
        enroll = day(rng, date(2019, 1, 1), date(2023, 12, 31))
        dx = rng.choices(["1", "2", "3", "4"], weights=[60, 20, 15, 5])[0]
        weight = round(rng.uniform(50, 110), 1)
        base.update(
            record_id=str(i),
            dob="" if i in (7, 23) else day(rng, date(1950, 1, 1), date(2004, 12, 31)).isoformat(),
            sex="3" if i == 11 else rng.choice(["1", "2"]),
            enroll_date=enroll.isoformat(),
            primary_dx=dx,
            dx_date="" if rng.random() < 0.1 else (enroll - timedelta(days=rng.randint(30, 3650))).isoformat(),
            enrollment_complete="2",
            base_date="1506-02-01" if i == 31 else (enroll + timedelta(days=rng.randint(0, 30))).isoformat(),
            weight=str(weight),
            height=str(rng.randint(150, 195)),
            smoker=rng.choice(["0", "0", "0", "1"]),
            eeg_done=rng.choice(["0", "1"]) if dx != "1" else "1",
            mri_done=rng.choice(["0", "1"]),
            sz_per_month=str(rng.randint(0, 20)) if dx == "1" else "",
            baseline_complete="2",
            deceased="1" if i in (5, 18, 36) else "0",
            death_date={5: "2024-06-14", 18: "2025-01-03"}.get(i, ""),
            study_notes="" if rng.random() < 0.8 else "Fictional demo record.",
            end_of_study_complete="2",
        )
        for c in CHOICES:
            base[f"comorbid___{c}"] = "1" if rng.random() < (0.2 if c != "99" else 0.05) else "0"
        aeds = rng.sample(["1", "2", "3", "4"], k=rng.choice([1, 1, 2])) if dx == "1" else \
            (["4"] if rng.random() < 0.3 else [])
        other = rng.random() < 0.1
        for c in CHOICES:
            base[f"meds___{c}"] = "1" if c in aeds or (c == "99" and other) else "0"
        base["meds_other"] = "clobazam" if other else ""
        rows.append(base)

        fu = enroll
        for k in range(1, rng.choice([0, 1, 1, 2, 2, 3]) + 1):
            fu = fu + timedelta(days=rng.randint(120, 240))
            r = {c: "" for c in COLUMNS}
            weight = round(weight + rng.uniform(-3, 3), 1)
            r.update(record_id=str(i), redcap_repeat_instrument="follow_up",
                     redcap_repeat_instance=str(k), fu_date=fu.isoformat(),
                     fu_weight=str(weight), fu_hosp=rng.choice(["0", "0", "0", "1"]),
                     follow_up_complete="2")
            new = rng.choice(["1", "2", "3", "4"]) if rng.random() < 0.25 else None
            for c in CHOICES:
                r[f"fu_new_meds___{c}"] = "1" if c == new else "0"
            rows.append(r)

    with (HERE / "export.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote export.csv: {len(rows)} rows for {N} fictional records")


if __name__ == "__main__":
    main()
