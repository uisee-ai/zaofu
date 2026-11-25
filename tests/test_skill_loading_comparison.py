from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.skill_loading_comparison import ARMS, FIELDS, loading_report
from tests.test_evolution_skill_trust import SHA, _common_identity
from zf.runtime.evolution_contracts import stable_digest
from zf.runtime.evolution_skill_eval import build_skill_treatment_identity


@pytest.fixture()
def comparison():
    suite = json.loads(
        (Path(__file__).parent / "fixtures/skill-loading/task-verify.json").read_text()
    )
    targets = {}
    for arm, mode in zip(ARMS, ("absent", "required", "on_demand")):
        target = {
            "name": suite["skill"],
            "available": arm != "raw",
            "loading_mode": mode,
        }
        if arm != "raw":
            target.update(
                version="v1", digest=SHA["a"], materialized_path_digest=SHA["f"]
            )
        targets[arm] = build_skill_treatment_identity(
            arm=arm,
            target_skill=target,
            common_identity=_common_identity(SHA["e"]),
            evaluation_purpose="natural_lift",
        )
    manifest = dict(
        skill=suite["skill"],
        skill_digest=SHA["a"],
        treatments=targets,
        suite_digest=stable_digest(suite),
        policy=suite["policy"],
        model="m",
        client="client",
        effort="high",
        image=SHA["e"],
        manifest_digests={arm: SHA["d"] for arm in ARMS},
    )
    rows = [
        dict(
            case=case["id"],
            repeat=rep,
            arm=arm,
            complete=True,
            timed_out=False,
            seconds=1,
            read=arm == "current"
            or (arm == "candidate" and case["route"] == "required"),
            usage={"input_tokens": 90 if arm == "candidate" else 100},
            answer=dict(zip(FIELDS, case["expected"])),
            trace_digest=SHA["a"],
            prompt_digest=SHA["b"],
        )
        for case in suite["cases"]
        for rep in (1, 2)
        for arm in ARMS
    ]
    return manifest, suite, rows


def test_only_complete_quality_preserving_pairs_can_recommend_a_canary(comparison):
    manifest, suite, rows = comparison
    assert loading_report(manifest, suite, rows)["decision"] == "canary_candidate"
    row = next(r for r in rows if r["arm"] == "candidate" and r["case"] == "median-red")
    row["answer"]["extra"] = "missing RED"
    report = loading_report(manifest, suite, rows)
    assert (
        report["decision"] == "retain_required"
        and "test:quality_regression" in report["reasons"]
    )
    test = report["splits"]["test"]["arms"]["candidate"]
    assert (
        test["verdict_correct"] == 10
        and test["correct"] == 9
        and test["false_releases"] == 0
    )
    row["answer"] = {"verdict": "release", "action": "accept", "receipt": "none"}
    assert "test:false_releases" in loading_report(manifest, suite, rows)["reasons"]
    row["complete"] = False
    assert "test:missing_pairs" in loading_report(manifest, suite, rows)["reasons"]
    with pytest.raises(ValueError, match="duplicate"):
        loading_report(manifest, suite, [*rows, rows[0]])


def test_reject_mislabeled_target_mode_and_common_identity(comparison):
    manifest, suite, rows = comparison
    wrong = deepcopy(manifest)
    wrong["treatments"]["candidate"]["target_skill"]["loading_mode"] = "required"
    with pytest.raises(ValueError, match="declared target or mode"):
        loading_report(wrong, suite, rows)
    wrong = deepcopy(manifest)
    wrong["treatments"]["candidate"]["provider_digest"] = SHA["b"]
    with pytest.raises(ValueError, match="identity mismatch"):
        loading_report(wrong, suite, rows)
