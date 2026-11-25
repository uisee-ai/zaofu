"""Run a blinded local reviewer on frozen labeled cases and report every outcome."""

import argparse
import http.client
import json
from pathlib import Path

from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.skill_review_model import model_identity, review_once
from zf.runtime.skill_semantic_review import RULE

DEFAULT = Path(__file__).resolve().parents[1] / "tests/fixtures/skill-semantic-review/calibration.json"


def calibrate(cases, model, timeout):
    identity = model_identity(model)
    rows = []
    for case in cases:
        facts = {"H1": {"output_excerpt": case["host"], "exit_code": case["exit"]}} if case["host"] else {}
        packet = {
            "rule": RULE,
            "task_outcome": case["outcome"],
            "obligations": [{"id": "AC1", "statement": case["obligation"]}],
            "claims": [{"id": "AC1", "findings": [case["worker"]]}],
            "host_facts": facts,
        }
        try:
            response = review_once(packet, identity, timeout)
            actual = response["decision"]["result"]
        except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError) as exc:
            response, actual = {"error": type(exc).__name__}, "pending"
        rows.append(
            {
                "case": case["case"],
                "reference": case["reference"],
                "actual": actual,
                "packet_digest": canonical_json_sha256(packet),
                "review": response,
            }
        )
    return summarize(cases, rows, identity)


def summarize(cases, rows, identity):
    expected_alerts = {c["case"] for c in cases if c["outcome"] == "passed" and c["reference"] == "fail"}
    observed_alerts = {
        r["case"] for r in rows if r["review"].get("decision", {}).get("critical_false_release") is True
    }
    return {
        "rubric": RULE,
        "reviewer": identity,
        "fixture_digest": canonical_json_sha256(cases),
        "rows": rows,
        "agreement": sum(r["reference"] == r["actual"] for r in rows),
        "disagreements": [r["case"] for r in rows if r["reference"] != r["actual"]],
        "abstentions": [r["case"] for r in rows if r["actual"] == "unknown"],
        "pending": [r["case"] for r in rows if r["actual"] == "pending"],
        "critical_false_release_cases": sorted(expected_alerts),
        "missed_critical_false_releases": sorted(expected_alerts - observed_alerts),
        "unconfirmed_critical_alerts": sorted(observed_alerts - expected_alerts),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--suite", choices=["calibration", "holdout", "challenge"], default="calibration")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    print(
        json.dumps(
            calibrate(
                json.loads(DEFAULT.with_name(args.suite + ".json").read_text()), args.model, args.timeout
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
