"""
run_wizard_tests.py: run the wizard's built-in self-test in a headless browser and
compare its results with the Python tools.

    python tests/run_wizard_tests.py [--browser PATH] [--vocab FOLDER]

Serves the repository on 127.0.0.1, opens docs/index.html?selftest=1 in headless
Chrome or Edge, and waits for the page to post its results. The page expands the
demo data dictionary, round-trips mapping.csv and project.json, and renders every
step. With --vocab (a full Athena download) it also streams CONCEPT.csv and
CONCEPT_RELATIONSHIP.csv over HTTP exactly as it streams a chosen file, and its
findings must match tools/check_concepts.py issue for issue. Without --vocab the
small test fixture is used.
"""
from __future__ import annotations

import argparse
import csv
import http.server
import importlib.util
import io
import json
import os
import queue
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS: "queue.Queue[dict]" = queue.Queue()
CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome", "chromium", "chromium-browser", "microsoft-edge",
]


def make_handler(vocab: Path, dd: Path | None):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(ROOT), **k)

        def translate_path(self, path):
            if path.startswith("/vocab/"):
                return str(vocab / path.split("?")[0][len("/vocab/"):])
            if path.startswith("/external_dd.csv") and dd:
                return str(dd)
            return super().translate_path(path)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            RESULTS.put(json.loads(body))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass
    return Handler


def find_browser(given: str | None) -> str:
    for c in ([given] if given else []) + CANDIDATES:
        if c and (Path(c).exists() or shutil.which(c)):
            return shutil.which(c) or c
    raise SystemExit("no Chrome or Edge found; pass --browser")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--browser")
    ap.add_argument("--vocab", type=Path, default=ROOT / "tests" / "fixtures" / "vocab")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--dd", type=Path, help="also load this data dictionary and compare "
                    "the browser's expansion with start_mapping.py's")
    args = ap.parse_args()

    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), make_handler(args.vocab, args.dd))
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = (f"http://127.0.0.1:{port}/docs/index.html?selftest=1"
           f"&vocab=/vocab/CONCEPT.csv&rel=/vocab/CONCEPT_RELATIONSHIP.csv"
           + ("&dd=/external_dd.csv" if args.dd else ""))
    profile = tempfile.mkdtemp(prefix="wizard-test-")
    browser = subprocess.Popen([find_browser(args.browser), "--headless=new", "--disable-gpu",
                                "--no-first-run", f"--user-data-dir={profile}",
                                "--remote-debugging-port=0", url],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        out = RESULTS.get(timeout=args.timeout)
    except queue.Empty:
        print("FAIL  the page never reported back")
        return 1
    finally:
        browser.kill()
        httpd.shutdown()
        shutil.rmtree(profile, ignore_errors=True)

    fails = 0
    for c in out["checks"]:
        print(f"{'PASS' if c['pass'] else 'FAIL'}  wizard: {c['name']}"
              + (f" ({c['detail']})" if c.get("detail") else ""))
        fails += not c["pass"]

    # The browser's expansion must equal start_mapping.py's.
    spec = importlib.util.spec_from_file_location("sm", ROOT / "tools" / "start_mapping.py")
    sm = importlib.util.module_from_spec(spec); spec.loader.exec_module(sm)
    py_rows = sm.expand(sm.read_text(ROOT / "examples" / "demo" / "data_dictionary.csv"))
    js_rows = list(csv.DictReader(io.StringIO(out["expanded_csv"])))
    same = [[r[c] for c in sm.MAPPING_COLUMNS] for r in py_rows] == \
           [[r[c] for c in sm.MAPPING_COLUMNS] for r in js_rows]
    print(f"{'PASS' if same else 'FAIL'}  browser and start_mapping.py expand the data dictionary identically")
    fails += not same

    if args.dd:
        py_rows = sm.expand(sm.read_text(args.dd))
        js_rows = list(csv.DictReader(io.StringIO(out.get("external_csv", ""))))
        same = [[r[c] for c in sm.MAPPING_COLUMNS] for r in py_rows] == \
               [[r[c] for c in sm.MAPPING_COLUMNS] for r in js_rows]
        print(f"{'PASS' if same else 'FAIL'}  {args.dd.name}: browser and start_mapping.py "
              f"agree on all {len(py_rows):,} rows")
        if not same:
            for a, b in zip(py_rows, js_rows):
                if [a[c] for c in sm.MAPPING_COLUMNS] != [b[c] for c in sm.MAPPING_COLUMNS]:
                    print("   first difference:", a, "\n   browser:", b)
                    break
        fails += not same

    # The browser's concept check must equal check_concepts.py's.
    spec = importlib.util.spec_from_file_location("cc", ROOT / "tools" / "check_concepts.py")
    cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
    with (ROOT / "examples" / "demo" / "mapping_with_mistakes.csv").open(encoding="utf-8") as fh:
        mapping = list(csv.DictReader(fh))
    project = json.loads((ROOT / "examples" / "demo" / "project.json").read_text(encoding="utf-8"))
    used = cc.uses(mapping, project)
    vocab = cc.read_concepts(args.vocab / "CONCEPT.csv", {u["concept_id"] for u in used})
    key = lambda i: (i["severity"], i["form"], i["field"], i["choice_code"], i["role"],
                     int(i["concept_id"]), i["message"])
    py = {key(i) for i in cc.check(used, vocab)}
    js = {key(i) for i in out.get("issues", [])}
    same = py == js
    print(f"{'PASS' if same else 'FAIL'}  browser and check_concepts.py report the same "
          f"{len(py)} issues ({out.get('check_seconds', 0):.0f} s in the browser)")
    if not same:
        print("   only Python:", sorted(py - js))
        print("   only browser:", sorted(js - py))
    fails += not same
    sugg = {i["concept_id"]: i.get("suggestion", "") for i in out.get("issues", [])}
    ok = sugg.get(766349, "").startswith("440383 Depressive disorder")
    print(f"{'PASS' if ok else 'FAIL'}  browser proposes the Maps to replacement")
    fails += not ok
    print("\nAll wizard tests passed" if not fails else f"\n{fails} wizard test(s) failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
