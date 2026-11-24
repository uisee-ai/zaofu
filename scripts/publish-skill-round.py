"""Build a distributable, replay-checked round from local frozen receipts; never call providers."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy

from skill_loading_comparison import replay, score_submission

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("submitted", "verdict_correct", "correct", "false_release", "false_block")


def load(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def calibration():
    cases = load(ROOT / "tests/fixtures/skill-loading/grader-cases.json")
    for case in cases:
        decision = score_submission(case["answer"], case["ref"])
        if [decision[k] for k in KEYS] != case["expected"]:
            raise ValueError(f"grader diverged from label for {case['id']}")
    return {
        "kind": "deterministic_output_grader",
        "criteria": KEYS,
        "cases": cases,
        "passed": len(cases),
    }


def checked_trials(directory):
    frozen = (
        load(directory / "frozen.json")
        if (directory / "frozen.json").exists()
        else {
            "suite": load(directory / "cases.json"),
            "identity": load(directory / "manifest.json"),
        }
    )
    rows = []
    for path in sorted((directory / "runs").glob("*/observation.json")):
        row = load(path)
        attempt = path.parent
        if attempt.name != "{case}-{repeat}-{arm}".format(**row):
            raise ValueError("trial directory mismatch")
        for file, key in (
            ("prompt.txt", "prompt_digest"),
            ("trace.jsonl", "trace_digest"),
        ):
            if digest(attempt / file) != row[key]:
                raise ValueError(f"changed {file} in {attempt.name}")
        result = load(attempt / "result.json")
        events = [
            json.loads(line)
            for line in (attempt / "trace.jsonl").read_text().splitlines()
        ]
        messages = [
            e["item"]["text"]
            for e in events
            if e.get("type") == "item.completed"
            and e["item"].get("type") == "agent_message"
        ]
        try:
            answer = json.loads(messages[-1])
        except (ValueError, IndexError):
            answer = None
        usage = next(
            (
                e.get("usage", {})
                for e in reversed(events)
                if e.get("type") == "turn.completed"
            ),
            {},
        )
        commands = [
            e["item"]
            for e in events
            if e.get("type") == "item.completed"
            and e["item"].get("type") == "command_execution"
        ]
        if (
            row["answer"] != answer
            or row["usage"] != usage
            or row["seconds"] != result["seconds"]
        ):
            raise ValueError(f"trial receipt mismatch in {attempt.name}")
        complete = bool(
            result["turn_completed"]
            and not result["errors"]
            and not result["returncode"]
            and not result["timed_out"]
            and not result["changed_paths"]
        )
        if complete != row["complete"] or result["trace_sha256"] != row["trace_digest"]:
            raise ValueError("transport receipt mismatch")
        row["tool_calls"] = len(commands)
        rows.append(row)
    return frozen["suite"], frozen["identity"], rows


def compatibility_report(directory):
    summary = load(directory / "report.json")
    catalogs = {}
    for path in sorted(
        directory.glob("*/*/state/workdirs/*/runtime/skills-manifest.json")
    ):
        pack = load(path)
        catalogs[(pack["role"], pack["backend"])] = pack["skills"]
    for row in summary["rows"]:
        catalog = catalogs[(row["role"], row["backend"])]
        item = next(s for s in catalog if s["name"] == row["name"])
        if item["status"] != row["materialization"] or item["sha256"] != row["digest"]:
            raise ValueError("static report differs from real materialization")
        row["required_by"] = sorted(
            s["name"] for s in catalog if row["name"] in s["dependencies"]
        )
    return summary


def publish(planner, interaction, verify, compat):
    p = runpy.run_path(str(ROOT / "scripts/eval-planner-manifest.py"))
    suite, registry, rows = checked_trials(planner)
    for r in rows:
        directory = planner / "runs" / "{case}-{repeat}-{arm}".format(**r)
        project = directory / "project"
        case = next(c for c in suite["cases"] if c["id"] == r["case"])
        required = ["inputs/contract.json", "inputs/scan.json"]
        if case.get("lookup") or case.get("missing"):
            required.append(
                "inputs/artifacts/" + p["sha"](case["id"].encode())[:12] + ".json"
            )
        if (
            sorted(required) != r["required"]
            or digest(project / "inputs/manifest.json") != r["manifest_digest"]
        ):
            raise ValueError("required set or frozen manifest mismatch")
        missing = [n for n in required if not (project / n).exists()]
        if len(missing) != int(bool(case.get("missing"))):
            raise ValueError("wrong missing-source fixture")
        existing = [n for n in required if n not in missing]
        seen = p["observed_bodies"](
            directory / "trace.jsonl",
            project,
            existing,
            missing[0] if missing else None,
        )
        if (
            seen != r["observed"]
            or sorted(set(required) - set(seen)) != r["missing_reads"]
            or p["score"](case, r["answer"]) != (r["handoff_correct"], r["false_ready"])
        ):
            raise ValueError("Planner handoff or read evidence mismatch")
    a = p["report"](suite, registry, rows)
    q = runpy.run_path(str(ROOT / "scripts/eval-verify-interaction.py"))
    suite, registry, rows = checked_trials(interaction)
    for r in rows:
        case = next(c for c in suite["cases"] if c["id"] == r["case"])
        trace = (
            interaction / "runs" / "{case}-{repeat}-{arm}".format(**r) / "trace.jsonl"
        )
        seen = {n: q["BASE"]["witnessed_read"](trace, n) for n in q["PAIR"]}
        if seen != r["reads"] or any(
            r[k] != v
            for k, v in score_submission(r["answer"], case["expected"]).items()
        ):
            raise ValueError("interaction scoring or read evidence mismatch")
    b = {**registry, "splits": q["summarize"](suite, rows), "observations": rows}
    for directory, report in ((planner, a), (interaction, b)):
        if load(directory / "report.json")["splits"] != report["splits"]:
            raise ValueError("online and replayed metrics differ")
    verify_suite, _, verify_rows = checked_trials(verify)
    for row in verify_rows:
        trace = verify / "runs" / "{case}-{repeat}-{arm}".format(**row) / "trace.jsonl"
        if q["BASE"]["witnessed_read"](trace, verify_suite["skill"]) != row["read"]:
            raise ValueError("Verify read evidence mismatch")
    return {
        "schema_version": "skill-policy-round.v1",
        "calibration": calibration(),
        "planner": a,
        "interaction": b,
        "verify": replay(verify),
        "compatibility": compatibility_report(compat),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("planner", "interaction", "verify", "compat"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(**vars(args)), ensure_ascii=False, indent=2))
