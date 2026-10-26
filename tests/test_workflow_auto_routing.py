"""Auto intake collects requirements without making a semantic route decision."""

import json
from pathlib import Path

import pytest

from zf.cli.flow import build_flow_intent
from zf.runtime.workflow_intake import build_flow_intake
from zf.runtime.workflow_requests import load_workflow_request
from tests.test_control_actions_workflow_request import _execute, _service


@pytest.mark.parametrize("objective", [
    "不要重构，只修复登录失败", "不需要调研，修复登录问题",
    "新增订单导出功能", "解释实现，不创建任务",
])
def test_auto_does_not_choose_a_delivery_kind_or_matrix(tmp_path, objective):
    result = build_flow_intake(kind="auto", objective=objective,
                              output=tmp_path / "docs/intake/input.md")
    manifest = json.loads(Path(result["workflow_input_manifest_ref"]).read_text())
    assert result["effective_kind"] == "auto"
    assert manifest["requested_lanes"] == 0
    assert "task_map_ref" not in manifest
    intent = build_flow_intent(intake_path=tmp_path / "docs/intake/input.md")
    assert intent["kind"] == "auto"
    assert intent["next_action"] == "clarify"


def test_auto_controlled_request_requires_semantic_selection(tmp_path):
    service, log = _service(tmp_path)
    result = _execute(service, "workflow-request", {
        "request_id": "auto-1", "objective": "不要重构，只修复登录失败",
        "backend": "mock", "allow_missing_env": True,
    })
    assert result["status"] == "synthesis_queued"
    assert result["synthesis_operation_id"]
    assert load_workflow_request(service.state_dir, result["request_id"])["kind"] == "auto"
    assert not result.get("proposal_ref")
    assert not any(e.type == "workflow.invoke.requested" for e in log.read_all())


def test_explicit_kind_and_legacy_replay_keep_their_decision(tmp_path):
    service, _ = _service(tmp_path)
    result = build_flow_intake(kind="issue", objective="不要重构，只修复登录失败",
                              request_id="legacy", output=tmp_path / "docs/intake/legacy.md")
    replay = build_flow_intake(kind="auto", objective="需求内容中包含 research",
                              request_id="legacy", output=tmp_path / "docs/intake/legacy.md")
    assert result["effective_kind"] == replay["effective_kind"] == "issue"
    assert load_workflow_request(service.state_dir, "legacy")["revision"] == 1


@pytest.mark.parametrize("objective", ["新增订单导出功能", "新增订单导出功能\n并补回归测试"])
def test_auto_synthesis_repeated_request_reuses_operation(tmp_path, objective):
    service, log = _service(tmp_path)
    payload = {"request_id": "auto-synth", "objective": objective,
               "synthesis_backend": "mock", "backend": "mock", "allow_missing_env": True}
    first = _execute(service, "workflow-request", payload)
    second = _execute(service, "workflow-request", payload)
    assert first["status"] == second["status"] == "synthesis_queued"
    assert first["request_revision"] == second["request_revision"]
    assert first["synthesis_operation_id"] == second["synthesis_operation_id"]
    assert len([e for e in log.read_all() if e.type == "workflow.synthesis.queued"]) == 1


def test_explicit_selection_resolves_pending_auto(tmp_path):
    service, _ = _service(tmp_path)
    payload = {"request_id": "choose", "objective": "修复登录失败", "allow_missing_env": True}
    _execute(service, "workflow-request", payload)
    chosen = _execute(service, "workflow-request", {**payload, "kind": "issue"})
    assert chosen["status"] == "proposal_ready"
    assert load_workflow_request(service.state_dir, "choose")["kind"] == "issue"


def test_cli_explicit_classification_updates_registered_requirement(tmp_path):
    service, _ = _service(tmp_path)
    intake = tmp_path / "docs/intake/classify.md"
    build_flow_intake(kind="auto", objective="修复登录", request_id="classify", output=intake)
    build_flow_intent(intake_path=intake, explicit_kind="issue")
    assert load_workflow_request(service.state_dir, "classify")["kind"] == "issue"


def test_standalone_legacy_intake_keeps_explicit_kind_on_replay(tmp_path):
    intake = tmp_path / "docs/intake/legacy.md"
    build_flow_intake(kind="issue", objective="修复登录", request_id="legacy", output=intake)
    assert build_flow_intake(kind="auto", objective="不要重构", request_id="legacy",
                             output=intake)["effective_kind"] == "issue"


def test_selecting_refactor_from_auto_requires_source_and_target(tmp_path):
    from zf.runtime.workflow_delivery import build_flow_submit_preview

    _service(tmp_path)
    intake = tmp_path / "docs/intake/select.md"
    build_flow_intake(kind="auto", objective="迁移代码", request_id="select", output=intake)
    preview = build_flow_submit_preview(config_path=tmp_path / "zf.yaml", intake_path=intake,
                                        flow_kind="refactor", allow_missing_env=True)
    assert any(b["kind"] == "workflow_selected_route_inputs_missing" for b in preview["blockers"])
