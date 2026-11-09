"""Independent acceptance against a clean exported commit, never a live worker tree."""
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

root = Path(sys.argv[1]).resolve()
commit = sys.argv[2]
output = Path(sys.argv[3])
resolved = subprocess.check_output(["git", "-C", str(root), "rev-parse", commit], text=True).strip()
archive = subprocess.check_output(["git", "-C", str(root), "archive", resolved])
results = []
with tempfile.TemporaryDirectory(prefix="zf-200-independent-") as scratch:
    checkout = Path(scratch)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(checkout, filter="data")
    cli = checkout / "export_csv.py"
    assert cli.is_file(), "delivery is missing export_csv.py"
    def invoke(value, raw=False):
        data = checkout / "independent-input.json"
        data.write_text(value if raw else json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return subprocess.run([sys.executable, str(cli), str(data)], cwd=checkout,
            text=True, capture_output=True, timeout=15)
    for name, rows in [("order", [{"name":"甲", "email":"a@example.invalid"}, {"name":"乙", "email":"b@example.invalid"}]),
                       ("escaping", [{"name":"客户, \"甲\"", "email":"comma,quote\"@example.invalid"}]),
                       ("empty", [])]:
        done = invoke(rows)
        assert done.returncode == 0, (name, done.stderr)
        assert list(csv.reader(io.StringIO(done.stdout))) == [["name", "email"]] + [[r["name"], r["email"]] for r in rows]
        results.append({"check":name, "passed":True})
    for name, value, raw in [("invalid_json", "{", True), ("non_array", {"name":"x"}, False),
                             ("missing_field", [{"name":"x"}], False),
                             ("partial_output", [{"name":"x","email":"x@example.invalid"},{"name":"broken"}], False),
                             ("non_string", [{"name":None,"email":"x@example.invalid"}], False)]:
        done = invoke(value, raw)
        assert done.returncode != 0 and done.stderr.strip() and not done.stdout, (name, done)
        results.append({"check":name, "passed":True})
    suite = unittest.TestLoader().discover(str(checkout / "tests"))
    count = suite.countTestCases()
    assert count >= 5, f"only {count} discovered tests"
    done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=checkout, text=True, capture_output=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    result = {"commit":resolved, "checks":results, "discovered_tests":count,
        "test_output":done.stdout + done.stderr, "export_csv_sha256":hashlib.sha256(cli.read_bytes()).hexdigest(),
        "passed":True}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k:v for k,v in result.items() if k != "test_output"}, ensure_ascii=False))
