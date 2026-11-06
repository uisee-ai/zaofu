"""Run real CLI journeys and persist observations for the current checkout."""

import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def execute(command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True,
                            encoding="utf-8", timeout=55)
    return {"command": shlex.join(command), "argv": command,
            "exit_code": result.returncode, "stdout": result.stdout,
            "stderr": result.stderr}


def journey(scenario_id, text, expected=None, error=None):
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "input.json"
        source.write_text(text, encoding="utf-8")
        observation = execute(["python3", "export_csv.py", str(source)])
    assertions = {}
    if expected is not None:
        assertions["exit_zero"] = observation["exit_code"] == 0
        assertions["stderr_empty"] = observation["stderr"] == ""
        try:
            rows = list(csv.reader(io.StringIO(observation["stdout"])))
            assertions["exact_csv_rows"] = rows == expected
            observation["parsed_rows"] = rows
        except csv.Error as failure:
            assertions["exact_csv_rows"] = False
            observation["parse_error"] = str(failure)
    else:
        assertions["exit_nonzero"] = observation["exit_code"] != 0
        assertions["stdout_empty"] = observation["stdout"] == ""
        assertions["readable_error"] = bool(observation["stderr"].strip())
        assertions["expected_error"] = error in observation["stderr"]
    return {"scenario_id": scenario_id, "input": text, **observation,
            "assertions": assertions, "passed": all(assertions.values())}


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


def main():
    commit = execute(["git", "rev-parse", "HEAD"])
    unit = execute(["python3", "-m", "unittest", "discover", "-s", "tests", "-v"])
    output = unit["stdout"] + unit["stderr"]
    counts = re.findall(r"^Ran (\d+) tests? in ", output, re.MULTILINE)
    count = int(counts[-1]) if counts else 0
    unit_assertions = {
        "exit_zero": unit["exit_code"] == 0,
        "at_least_five_tests": count >= 5,
        "all_pass_no_skips": bool(re.search(r"^OK\s*$", output, re.MULTILINE))
                             and not re.search(r"FAIL|ERROR|skipped|expected failure", output),
    }
    ordered = [{"name": "Zoe", "email": "z@example.com"},
               {"name": "Amy", "email": "a@example.com"}]
    escaped = [{"name": '李四, "朋友"\n张三', "email": '"a,b"@example.com'}]
    header = [["name", "email"]]
    scenarios = [
        journey("SC-ORDER", encoded(ordered),
                header + [[r["name"], r["email"]] for r in ordered]),
        journey("SC-ESCAPE", encoded(escaped),
                header + [[r["name"], r["email"]] for r in escaped]),
        journey("SC-EMPTY", "[]", header),
        journey("SC-JSON", "[{", error="JSON"),
    ]
    for index, value in enumerate(({}, None, "contacts", 5)):
        scenarios.append(journey(f"SC-SHAPE-{index}", encoded(value), error="array"))
    for field in ("name", "email"):
        later = {"name": "Later", "email": "later@example.com"}
        del later[field]
        scenarios.append(journey(f"SC-MISSING-{field}",
                                 encoded([ordered[0], later]), error=field))
    invalid_records = [None, [], "bad", {"name": 1, "email": "a"},
                       {"name": "A", "email": None}]
    for index, record in enumerate(invalid_records):
        scenarios.append(journey(f"SC-TYPES-{index}", encoded([record]), error="record 1"))
    scenarios.append({"scenario_id": "SC-COUNT", "test_count": count,
                      "assertions": unit_assertions,
                      "passed": all(unit_assertions.values())})
    passed = (commit["exit_code"] == 0 and bool(commit["stdout"].strip())
              and all(s["passed"] for s in scenarios))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit": commit["stdout"].strip(),
        "verification_kind": "author self-check; independent verification pending",
        "runner_command": "python3 tests/run_acceptance.py",
        "test_count": count, "unittest": unit,
        "scenarios": scenarios, "overall_passed": passed,
    }
    destination = ROOT / "artifacts" / "demo" / "acceptance.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"{'PASS' if passed else 'FAIL'}: {count} tests, {len(scenarios)} scenarios; {destination}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
