"""Compare completed, identity-matched Skill loading trials against frozen reference answers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from statistics import mean, median

from zf.runtime.evolution_contracts import stable_digest
from zf.runtime.evolution_skill_claim import paired_skill_lifts
from zf.runtime.evolution_skill_eval import compare_skill_treatment_identities

ARMS = ("raw", "current", "candidate")
FIELDS = ("verdict", "action", "receipt")


def score_submission(value, ref):
    answer = value if isinstance(value, dict) else {}
    submitted = (
        set(answer) == set(FIELDS)
        and answer["verdict"] in ("release", "withhold", "outside")
        and answer["action"]
        in ("accept", "request_evidence", "return_to_planner", "no_verification")
        and isinstance(answer["receipt"], str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", answer["receipt"]) is not None
    )
    return {
        "submitted": submitted,
        "verdict_correct": answer.get("verdict") == ref[0],
        "correct": submitted and [answer[k] for k in FIELDS] == ref,
        "false_release": ref[0] != "release" and answer.get("verdict") == "release",
        "false_block": ref[0] == "release" and answer.get("verdict") == "withhold",
    }


def loading_report(manifest: dict, suite: dict, observations: list[dict]) -> dict:
    comparison = compare_skill_treatment_identities(
        [manifest["treatments"][arm] for arm in ARMS]
    )
    target_modes = {"raw": "absent", "current": "required", "candidate": "on_demand"}
    for arm, mode in target_modes.items():
        trial = manifest["treatments"][arm]
        target = trial["target_skill"]
        if (
            trial["arm"] != arm
            or target.get("loading_mode") != mode
            or target["name"] != suite["skill"]
            or manifest["skill"] != suite["skill"]
            or (arm != "raw" and target["digest"] != manifest["skill_digest"])
        ):
            raise ValueError("loading trial does not match its declared target or mode")
    if (
        not comparison["comparable"]
        or manifest["suite_digest"] != stable_digest(suite)
        or manifest["policy"] != suite["policy"]
    ):
        raise ValueError("frozen suite or treatment identity mismatch")
    policy = suite["policy"]
    cases = {case["id"]: case for case in suite["cases"]}
    count = policy["replicates"]
    rows = {}
    for row in observations:
        key = row["case"], row["repeat"], row["arm"]
        if (
            key in rows
            or key[0] not in cases
            or key[2] not in ARMS
            or not 1 <= key[1] <= count
        ):
            raise ValueError("unexpected or duplicate trial observation")
        ref = cases[key[0]]["expected"]
        rows[key] = {
            **row,
            "reference": ref,
            "route": cases[key[0]]["route"],
            "split": cases[key[0]]["split"],
            **score_submission(row["answer"], ref),
        }
    splits = {}
    for split in ("selection", "test"):
        keys = [
            (case["id"], rep)
            for case in cases.values()
            if case["split"] == split
            for rep in range(1, count + 1)
        ]
        matched = [
            key
            for key in keys
            if all(rows.get((*key, arm), {}).get("complete") for arm in ARMS)
        ]
        arms = {}
        paired = {}
        for arm in ARMS:
            trials = [rows[(*key, arm)] for key in matched]
            costs = [
                r["usage"]["input_tokens"]
                for r in trials
                if type(r["usage"].get("input_tokens")) is int
            ]
            token_totals = {
                k: sum(r["usage"][k] for r in trials)
                if trials and all(type(r["usage"].get(k)) is int for r in trials)
                else None
                for k in ("input_tokens", "cached_input_tokens", "output_tokens")
            }
            arms[arm] = {
                "complete": sum(
                    bool(rows.get((*key, arm), {}).get("complete")) for key in keys
                ),
                "timeouts": sum(
                    bool(rows.get((*key, arm), {}).get("timed_out")) for key in keys
                ),
                "correct": sum(r["correct"] for r in trials),
                "verdict_correct": sum(r["verdict_correct"] for r in trials),
                "first_valid_submissions": sum(r["submitted"] for r in trials),
                "false_releases": sum(r["false_release"] for r in trials),
                "false_blocks": sum(r["false_block"] for r in trials),
                "correct_blocks": sum(
                    r["reference"][0] == "withhold" and r["correct"] for r in trials
                ),
                "observed_reads": sum(r["read"] for r in trials),
                "missed_required_reads": sum(
                    r["route"] == "required" and not r["read"] for r in trials
                ),
                "unrelated_reads": sum(
                    r["route"] == "forbidden" and r["read"] for r in trials
                ),
                "mean_input_tokens": round(mean(costs))
                if costs and len(costs) == len(trials)
                else None,
                "median_seconds": round(median(r["seconds"] for r in trials), 2)
                if trials
                else None,
                **token_totals,
            }
            paired[arm] = [
                {
                    "pairing": {"replicate": r["repeat"]},
                    "case_results": [
                        {"case_id": r["case"], "score": 100 if r["correct"] else 0}
                    ],
                }
                for r in trials
            ]
        regressions = sum(
            rows[(*key, "current")]["correct"]
            and not rows[(*key, "candidate")]["correct"]
            for key in matched
        )
        splits[split] = {
            "expected_pairs": len(keys),
            "matched_pairs": len(matched),
            "arms": arms,
            "quality_regressions": regressions,
            "lifts": paired_skill_lifts(
                baseline=paired["current"],
                candidate=paired["candidate"],
                control=paired["raw"],
            ),
        }
    reasons = []
    if (
        count < 2
        or len({case["id"] for case in cases.values() if case["split"] == "test"}) < 3
    ):
        reasons.append("coverage")
    for split, metric in splits.items():
        a = metric["arms"]["candidate"]
        current = metric["arms"]["current"]
        if (
            metric["matched_pairs"] != metric["expected_pairs"]
            or not metric["matched_pairs"]
        ):
            reasons.append(split + ":missing_pairs")
        if metric["quality_regressions"] > policy["max_quality_regressions"]:
            reasons.append(split + ":quality_regression")
        for field, limit in (
            ("false_releases", "max_critical_false_releases"),
            ("missed_required_reads", "max_missed_required_reads"),
            ("unrelated_reads", "max_unrelated_reads"),
        ):
            if a[field] > policy[limit]:
                reasons.append(split + ":" + field)
        if current["observed_reads"] != metric["matched_pairs"]:
            reasons.append(split + ":required_loading_unconfirmed")
        if metric["arms"]["raw"]["observed_reads"]:
            reasons.append(split + ":raw_contamination")
        cost, baseline = a["input_tokens"], current["input_tokens"]
        savings = (
            round(100 * (baseline - cost) / baseline, 2)
            if baseline and cost is not None
            else None
        )
        metric["input_token_saving_percent"] = savings
        if (
            savings is None
            or 100 * (baseline - cost)
            < baseline * policy["minimum_input_token_saving_percent"]
        ):
            reasons.append(split + ":cost_goal")
    return {
        "schema_version": "skill-loading-comparison.v1",
        "scope": "Task Verify",
        "skill": manifest["skill"],
        "skill_digest": manifest["skill_digest"],
        "model": manifest["model"],
        "effort": manifest["effort"],
        "client": manifest["client"],
        "suite_digest": manifest["suite_digest"],
        "image": manifest["image"],
        "common_fingerprint": comparison["common_fingerprint"],
        "treatment_fingerprints": {
            arm: manifest["treatments"][arm]["treatment_fingerprint"] for arm in ARMS
        },
        "manifest_digests": manifest["manifest_digests"],
        "policy": policy,
        "decision": "retain_required" if reasons else "canary_candidate",
        "reasons": reasons,
        "splits": splits,
        "observations": [
            {
                k: r[k]
                for k in (
                    "case",
                    "repeat",
                    "arm",
                    "split",
                    "route",
                    "complete",
                    "submitted",
                    "reference",
                    "answer",
                    "verdict_correct",
                    "correct",
                    "false_release",
                    "read",
                    "usage",
                    "seconds",
                    "trace_digest",
                    "prompt_digest",
                )
            }
            for _, r in sorted(rows.items())
        ],
    }


def replay(directory: Path) -> dict:
    """Recalculate from the frozen suite and receipts without making model calls."""
    manifest = json.loads((directory / "manifest.json").read_text())
    suite = json.loads((directory / "cases.json").read_text())
    for arm, digest in manifest["manifest_digests"].items():
        data = (directory / "templates" / arm / "manifest.json").read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("materialization manifest changed since trial")
    observations = []
    for path in sorted((directory / "runs").glob("*/observation.json")):
        row = json.loads(path.read_text())
        if path.parent.name != "{case}-{repeat}-{arm}".format(**row):
            raise ValueError("observation in wrong trial directory")
        for file, field in (
            ("trace.jsonl", "trace_digest"),
            ("prompt.txt", "prompt_digest"),
        ):
            if (
                hashlib.sha256((path.parent / file).read_bytes()).hexdigest()
                != row[field]
            ):
                raise ValueError(f"{file} changed since trial")
        observations.append(row)
    return loading_report(manifest, suite, observations)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Recalculate an isolated Skill loading trial report."
    )
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(args.directory), ensure_ascii=False, indent=2))
