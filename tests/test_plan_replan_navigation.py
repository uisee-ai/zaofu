"""Replan navigation is current scoped review, never historical approval."""
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_plan_input_read_selection import fixture, read_rows
from zf.runtime.artifact_read_requirements import build_canonical_required_reads
from zf.runtime.plan_input_read_selection import select_plan_input_reads
from zf.runtime.plan_review_selection_cost import choose_review_reads


def replan_fixture(state, fault=""):
    manifest, _, report, matrix = fixture(state)
    def add(name, body, artifact=None):
        path = state / "artifacts" / f"{name}.json"
        path.write_text(json.dumps(body))
        source = {"source_id": name, "artifact_id": artifact or name,
                  "ref": path.relative_to(state).as_posix(),
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "allowed_paths": ["$"]}
        manifest["sources"].append(source)
        return source
    context = {"schema_version": "plan-rework-context.v1", "rework_of": "rejected-1",
               "rework_feedback": ["旧 finding 必须关闭"], "changed_task_ids": ["T1"]}
    if fault in {"operator_override", "human_resolution", "owner_decision_resolution", "requirement_amendment_ref"}:
        context[fault] = "present"
    if fault == "unknown_schema": context["schema_version"] = "future"
    if fault == "missing_rework": context.pop("rework_of")
    context_source = add("plan-rework-context", context)
    add("previous-plan-candidate-1", {"old_unique_failure": "旧失败不变项", "plan": "旧版完整正文"})
    if fault == "amendment": add("owner-requirement-amendment", {"new": "requirement"})
    if fault in {"certificate", "unchanged"}:
        add("child-artifact-certificate", {}, "scoped_plan_review.json" if fault == "certificate" else "unchanged-plan-review.json")
    if fault == "digest": (state / context_source["ref"]).write_text("{}")
    if fault == "missing_context": manifest["sources"].remove(context_source);add("plan-rework-package", {})
    if fault == "orphan_previous": manifest["sources"].remove(context_source)
    required = build_canonical_required_reads(manifest, output_profile_id="plan-synth")
    if fault == "partial_context":
        for row in required:
            if row["source_id"] == "plan-rework-context": row["json_path"] = "$.rework_of"
    return manifest, required, report, matrix


@pytest.mark.parametrize("fault", ["", "full", "explicit", "no_focus", "operator_override",
    "human_resolution", "owner_decision_resolution", "requirement_amendment_ref", "amendment",
    "unknown_schema", "missing_rework", "certificate", "unchanged", "digest", "missing_context", "partial_context", "orphan_previous"])
def test_replan_scope_preserves_original_and_failed_inheritance(state_dir, fault):
    manifest, required, report, matrix = replan_fixture(state_dir, fault)
    policy = {"mode": "full" if fault == "full" else "targeted", "focus": [] if fault == "no_focus" else ["验收责任"]}
    selected, strategy = select_plan_input_reads(state_dir, manifest, required, policy=policy, explicit=fault == "explicit")
    assert any(row["source_id"] == matrix["source_id"] for row in selected) == bool(fault)
    assert any(row["source_id"] == "previous-plan-candidate-1" for row in selected)
    assert any(row["source_id"] == "requirement" for row in selected)
    if not fault:
        assert strategy == "targeted_replan_navigation"
        reads = read_rows(state_dir, manifest, selected)
        assert any("旧 finding 必须关闭" in value["content"] for value in reads)
        assert any("旧失败不变项" in value["content"] for value in reads)
        assert matrix in manifest["sources"]
        chosen, diagnostic = choose_review_reads(state_dir, manifest, before=required, candidate=selected,
                                                explicit=False, strategy=strategy)
        assert chosen == selected
        assert diagnostic["mode"] == "targeted"
        assert diagnostic["inherited_approval"] is False


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path


def test_replan_actual_dispatch_pins_current_scope_and_old_findings(tmp_path):
    from tests.test_call_result_runtime import _runtime
    from zf.runtime.call_result_runtime import prepare_call_operation
    runtime = _runtime(tmp_path)
    manifest, _, _, matrix = replan_fixture(runtime.state_dir)
    runtime.config.workflow.flow_metadata["plan_review"] = {"mode": "targeted", "focus": ["failure"], "reason": "Owner"}
    payload = {"workflow_run_id": "run", "role_instance": "critic", "output_profile_id": "plan-synth",
               "input_refs": manifest["sources"], "canonical_success_event": "fanout.synth.completed",
               "canonical_failure_event": "fanout.synth.completed"}
    prepared = prepare_call_operation(runtime, payload=payload, operation_type="fanout_synth",
        operation_key="critic-replan", stage_id="plan", task_id="", dispatch_id="attempt-replan")
    assert prepared.should_dispatch
    assert payload["plan_review_optimization"]["strategy"] == "targeted_replan_navigation"
    assert not payload["plan_review_optimization"]["inherited_approval"]
    assert not any(row["source_id"] == matrix["source_id"] for row in payload["required_reads"])
    assert {"plan-rework-context", "previous-plan-candidate-1", "requirement"} <= {row["source_id"] for row in payload["required_reads"]}
    pinned = json.loads((runtime.state_dir / payload["attempt_source_manifest_ref"]).read_text())
    assert any(row["sha256"] == matrix["sha256"] for row in pinned["sources"])


def test_old_report_body_is_required_even_when_equal_to_current_optional_matrix(tmp_path):
    manifest, _, report, _ = replan_fixture(tmp_path)
    old = next(source for source in manifest["sources"] if source["source_id"] == "previous-plan-candidate-1")
    body = json.loads((tmp_path / report["ref"]).read_text())
    body["old_unique_failure"] = "旧版 finding 仍须重新核对"
    (tmp_path / old["ref"]).write_text(json.dumps(body))
    old["sha256"] = hashlib.sha256((tmp_path / old["ref"]).read_bytes()).hexdigest()
    required = build_canonical_required_reads(manifest, output_profile_id="plan-synth")
    rows, _ = select_plan_input_reads(tmp_path, manifest, required,
        policy={"mode": "targeted", "focus": ["failure"]})
    old_reads = read_rows(tmp_path, manifest, [row for row in rows if row["source_id"] == old["source_id"]])
    assert any("unchanged" in row["content"] for row in old_reads)
    assert any("旧版 finding" in row["content"] for row in old_reads)


@pytest.mark.parametrize("budget", [None, "200000", "400000"])
def test_v5_compaction_budget_applies_to_plan_and_replan_without_model_window_override(monkeypatch, budget):
    from zf.core.config.loader import load_config
    from zf.runtime.backend import CodexAdapter
    if budget is None: monkeypatch.delenv("ZF_CODEX_CONTEXT_WINDOW_TOKENS", raising=False)
    else: monkeypatch.setenv("ZF_CODEX_CONTEXT_WINDOW_TOKENS", budget)
    cfg = load_config(Path(__file__).parents[1] / "zf-prd-v5.yaml")
    roles = [role for role in cfg.roles if role.name in {"planner", "plan-critic"}]
    assert {role.name for role in roles} == {"planner", "plan-critic"}
    for role in roles:
        assert role.context_window_tokens == int(budget or "120000")
        for resume in (False, True):
            command = CodexAdapter().build_command(role, session_id="test-session", is_resume=resume)
            assert f"model_auto_compact_token_limit={int(int(budget or '120000') * 0.7)}" in command
            assert not any(arg.startswith("model_context_window=") for arg in command)
