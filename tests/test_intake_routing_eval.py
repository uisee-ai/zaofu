"""The evaluation oracle must reject wrong decisions without running a Provider."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("eval_intake_routing", ROOT / "scripts/eval-intake-routing.py")
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)
CASES = {c["id"]: c for c in json.loads((ROOT / "tests/fixtures/intake-routing/cases.json").read_text())["cases"]}
CATALOG = {"routes": [{"route_id": "delivery:issue:default", "kind": "issue"},
                      {"route_id": "delivery:prd:default", "kind": "prd"}]}


def response(case, route="delivery:issue:default"):
    return json.dumps({"action_proposal": {
        "action": "create-task", "intent": {"decision": "propose_action", "source_quote": case["message"]},
        "payload": {"title": "处理请求", "contract": {"behavior": "处理请求", "verification": "pytest"},
                    "workflow_plan": {"question": "执行哪条路线？", "options": [
                        {"route_id": route, "label": "建议路线", "description": "按当前目标交付", "recommended": True},
                        {"mode": "defer", "label": "稍后执行", "description": "保持待处理"}]}},
        "reason": "用户请求创建任务"}}, ensure_ascii=False)


def test_oracle_accepts_correct_route_and_rejects_wrong_or_unregistered_route():
    case = CASES["negated_refactor"]
    assert evaluator.score(case, response(case), catalog=CATALOG)["passed"]
    assert not evaluator.score(case, response(case, "delivery:prd:default"), catalog=CATALOG)["passed"]
    assert not evaluator.score(case, response(case, "invented"), catalog=CATALOG)["passed"]


def test_negated_creation_cannot_score_as_correct_even_with_valid_proposal():
    case = CASES["negated_create"]
    assert evaluator.score(case, "创建按钮会提交任务提案。", catalog=CATALOG)["passed"]
    assert not evaluator.score(case, response(case), catalog=CATALOG)["passed"]


@pytest.mark.parametrize("reply", ["", '```json\n{"action_proposal":\n```'])
def test_empty_or_malformed_output_is_not_a_successful_answer(reply):
    assert not evaluator.score(CASES["explain"], reply, catalog=CATALOG)["passed"]


def test_tracking_only_cannot_add_workflow_plan():
    case = CASES["track_only"]
    assert not evaluator.score(case, response(case), catalog=CATALOG)["passed"]


def test_real_runner_interface_is_read_only_and_keeps_raw_evidence():
    calls = []

    class FakeAgent:
        def run_turn(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(ok=True, reply="解释当前机制。", status="completed", usage={"tokens": 3})

    rows = evaluator.evaluate([CASES["explain"], CASES["english_negation"]],
                              agent=FakeAgent(), backend="mock-test", catalog=CATALOG)
    assert all(r["passed"] for r in rows)
    assert all(c["permission_profile"] == "read_only" for c in calls)
    assert calls[0]["thread_key"] != calls[1]["thread_key"]
    assert calls[0]["context"]["workflow_route_catalog"] == CATALOG
    assert rows[0]["reply"] == "解释当前机制。" and rows[0]["usage"] == {"tokens": 3}


def test_provider_failure_and_unavailable_catalog_cannot_pass():
    class FailedAgent:
        def run_turn(self, **kwargs):
            return SimpleNamespace(ok=False, reply="", error="unavailable", status="failed")

    rows = evaluator.evaluate([CASES["explain"], CASES["new_feature"]],
                              agent=FailedAgent(), backend="mock-test", catalog={"routes": []})
    assert not any(r["passed"] for r in rows)
    assert rows[1]["status"] == "catalog_ineligible"


def test_multiturn_correction_uses_same_session_and_final_message_for_scoring():
    calls = []

    class Agent:
        def run_turn(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(ok=True, reply="解释登录机制。", status="completed")

    rows = evaluator.evaluate([CASES["scope_correction"]], agent=Agent(), backend="mock", catalog=CATALOG)
    assert len(calls) == 2 and calls[0]["thread_key"] == calls[1]["thread_key"]
    assert rows[0]["passed"] and len(rows[0]["turns"]) == 2


def test_cli_recorded_mode_does_not_claim_a_live_model_run(tmp_path, monkeypatch):
    from tests.test_control_actions_workflow_request import _service
    import sys

    _service(tmp_path)
    responses = tmp_path / "responses.json"
    responses.write_text(json.dumps({"explain": "解释机制。"}))
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["eval-intake-routing", "--project-root", str(tmp_path),
        "--responses", str(responses), "--case", "explain", "--output", str(output)])
    assert evaluator.main() == 0
    report = json.loads(output.read_text())
    assert report["mode"] == "recorded_responses" and report["cases_sha256"]
