# REDCap to OMOP Mapping Kit

Map a REDCap registry to the [OMOP Common Data Model v5.4](https://ohdsi.github.io/CommonDataModel/cdm54.html),
check every concept id against your own vocabulary download, and turn a REDCap
export into OMOP tables.

- **A mapping wizard** that runs in the browser: open `docs/index.html`, or the
  repository's GitHub Pages site. It reads your REDCap data dictionary, helps you
  assign an OMOP table and concept to every field and answer, checks each id
  against the vocabulary, and exports `mapping.csv` and `project.json`. Nothing is
  uploaded; it works offline from the single file.
- **A runner**, `runner/redcap_to_omop.py`, that turns a REDCap export into OMOP
  CSV tables from those two files. It needs only Python and pandas.
- **Command-line tools** for the same steps without the browser:
  `tools/start_mapping.py` and `tools/check_concepts.py`.
- **A worked demo** with a fictional registry, its data dictionary, a fictional
  export, a finished mapping, and a copy with planted mistakes for the check to find.

Start with [GUIDE.md](GUIDE.md).

## Try it in two minutes

```bash
pip install -r requirements.txt

# map nothing yourself: run the finished demo mapping on the fictional export
python runner/redcap_to_omop.py --project examples/demo/project.json \
    --mapping examples/demo/mapping.csv --export examples/demo/export.csv \
    --output omop_output
```

Then open `docs/index.html` in a browser and press **Demo with mistakes**.

## Use it for your registry

1. Download your REDCap data dictionary and load it in the wizard (step 1).
2. Check the project settings it guessed (step 2).
3. Map each field and answer, or leave it undecided (step 3).
4. Check the concept ids against `CONCEPT.csv` from [Athena](https://athena.ohdsi.org/vocabulary/list) (step 4).
5. Export `mapping.csv` and `project.json`, and run the runner where your data is (step 5).

To start your own copy of the kit, use GitHub's **Use this template** button.

## Layout

```
docs/index.html            the wizard: one HTML file, no dependencies
runner/redcap_to_omop.py   REDCap export + mapping -> OMOP CDM v5.4 CSVs
tools/start_mapping.py     data dictionary -> blank mapping.csv
tools/check_concepts.py    concept check against an Athena download
tools/embed_demo.py        copies the demo files into the wizard
examples/demo/             fictional registry: dictionary, export, mappings, project.json
tests/                     end-to-end tests, and a 27-concept vocabulary fixture
GUIDE.md                   how to map a registry, step by step
```

## Tests

```bash
python tests/run_tests.py            # runner, tools and demo; runs in CI
python tests/run_wizard_tests.py     # the wizard, in headless Chrome or Edge
python tests/run_wizard_tests.py --vocab /path/to/athena_download   # full-size check
```

The wizard and the Python tools share their rules; the wizard test checks that both
expand the data dictionary identically and report identical issues.

## Caution

- The concept check confirms that ids exist, are standard and sit in the right
  table. It is not clinical review of whether each concept is the right one.
- No OMOP vocabulary is included. Each user downloads it from Athena under its
  terms.
- The runner's output is as identifiable as the export it reads. Run it where the
  export is allowed to be.

## License

Not chosen yet. Until a license file is added, others may read the code but have no
permission to reuse it.

*Documentation drafted with AI assistance.*
