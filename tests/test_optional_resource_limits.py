"""Explicit unlimited settings survive configuration, dispatch and admission."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zf.autoresearch.triggers import TriggerPolicy, decide_trigger_for_signal
from zf.core.config import load_config
from zf.core.config.schema import ProviderSessionConfig, RoleConfig
from zf.core.events.model import ZfEvent
from zf.runtime.backend import get_adapter
from zf.runtime.call_result_limits import operation_budget_ceiling
from zf.runtime.call_result_runtime import prepare_call_operation
from zf.runtime.provider_operation_summary import validate_provider_operation_summary
from zf.runtime.provider_session_config import resolve_effective_provider_session
from zf.runtime.research_fanout_artifact import materialize_research_fanout_artifact
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_budget_guard import _exceeded_dimensions, _operation_limits
from zf.runtime.workflow_operation import load_workflow_operation
from tests.test_autoresearch_triggers import _signal
from tests.test_call_result_admission import _runtime as admission_runtime, _persisted_verification_payload
from tests.test_call_result_runtime import _runtime as call_runtime
from tests.test_research_artifact import _manifest, _synth_event, _provider_summary
from tests.test_task_pipeline_profile import _compile, _task_pipeline


def test_zero_trigger_limits_allow_more_than_daily_budget_and_same_instant(tmp_path):
    policy = TriggerPolicy(cooldown_minutes=0, max_triggers_per_hour=0, max_daily_runs=0)
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    history = []
    for _ in range(8):
        decision = decide_trigger_for_signal(_signal(), state_dir=tmp_path, policy=policy, history=history, now=now)
        assert decision.decision == "accepted"
        history.append(decision)
    capped = decide_trigger_for_signal(_signal(), state_dir=tmp_path, policy=TriggerPolicy(), history=history, now=now)
    assert capped.skip_reason == "hourly_budget"
    disabled = decide_trigger_for_signal(_signal(), state_dir=tmp_path, policy=TriggerPolicy(enabled=False, max_daily_runs=0), now=now)
    assert disabled.skip_reason == "disabled"


def test_loader_preserves_zero_resident_action_limit(tmp_path):
    path = tmp_path / "zf.yaml"
    path.write_text("version: '1.0'\nproject: {name: zero-limits}\nruntime:\n  autoresearch_resident:\n    enabled: true\n    max_actions_per_tick: 0\n")
    assert load_config(path).runtime.autoresearch_resident.max_actions_per_tick == 0


def test_risk_review_zero_limits_compile_without_enabling_review():
    body = _task_pipeline()
    body["integrationAdmission"] = {"riskReview": {
        "enabled": False, "timeoutSeconds": 0, "maxTurns": 0, "budgetUsd": 0,
    }}
    risk = _compile(body)["integration_admission"]["risk_review"]
    assert not risk["enabled"]
    assert (risk["timeout_seconds"], risk["max_turns"], risk["budget_usd"]) == (0, 0, 0)
    assert operation_budget_ceiling({"risk_review_budget_usd": "0", "budget_snapshot": {"remaining_usd": 1}}) is None


def test_explicit_zero_operation_limits_do_not_restore_profile_caps(tmp_path):
    _, service = admission_runtime(tmp_path)
    service.ensure_operation(workflow_run_id="run-1", operation_id="op-1", operation_type="task-stage", request={
        "operation_limits": {"timeout_seconds": 0, "cost_budget_usd": 0},
        "execution_profile": {"profile": {"limits": {"timeout_seconds": 1, "max_usage_samples": 1, "token_budget": 1, "cost_budget_usd": 1}}},
    })
    limits = _operation_limits(tmp_path, load_workflow_operation(service.event_log, "op-1"))
    assert all(value == 0 for value in limits.values())
    assert _exceeded_dimensions(limits, {"meter_available": True, "elapsed_seconds": 100, "usage_samples": 100, "total_tokens": 100, "total_usd": 100}) == []


def test_native_unlimited_config_preserves_provider_default_and_freezes_operation(tmp_path):
    runtime = call_runtime(tmp_path)
    role = RoleConfig(name="reader", backend="codex", provider_session=ProviderSessionConfig(max_parallel_agents=0))
    runtime.config.roles = [role]
    command = get_adapter("codex").build_command(role)
    assert not any("max_concurrent_threads_per_session" in value for value in command)
    snapshot = resolve_effective_provider_session(state_dir=runtime.state_dir, role=role)
    assert snapshot.snapshot["limits"]["harness_max_parallel_agents"] == 0
    payload = {"workflow_run_id": "run-1", "role_instance": "reader", "provider_session_max_parallel_agents": 3}
    prepared = prepare_call_operation(runtime, payload=payload, operation_type="fanout_reader_child", operation_key="reader", stage_id="scan", task_id="", dispatch_id="reader-dispatch")
    operation = load_workflow_operation(runtime.event_log, prepared.operation_id)
    assert operation["provider_session_max_parallel_agents"] == "0"
    request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload["request"]
    assert request["provider_session_max_parallel_agents"] == 0


@pytest.mark.parametrize("limit,admitted", [(0, True), (4, False), (None, False)])
def test_result_admission_uses_frozen_native_limit(tmp_path, limit, admitted):
    admission, service = admission_runtime(tmp_path)
    request = {} if limit is None else {"provider_session_max_parallel_agents": limit}
    service.ensure_operation(workflow_run_id="run-1", operation_id="op-1", operation_type="agent", request=request, task_id="T1")
    payload = _persisted_verification_payload(tmp_path, verdict="passed")
    summary = _provider_summary()
    summary.update(workflow_run_id="run-1", operation_id="op-1", child_count=9, peak_parallel_agents=9, child_status_counts={"completed": 9})
    payload["provider_operation_summary"] = summary
    # A result cannot grant itself an exemption from a finite or default limit.
    payload["provider_session_max_parallel_agents"] = 0
    operation = load_workflow_operation(service.event_log, "op-1")
    outcome = admission.report_legacy_result(ZfEvent(type="verify.child.completed", actor="verify-1", task_id="T1", payload=payload), mode="blocking", operation=operation)
    assert outcome.admitted is admitted


def test_unlimited_native_summary_keeps_identity_and_settlement_checks():
    summary = _provider_summary()
    summary.update(child_count=9, peak_parallel_agents=9, active_child_count=1, child_status_counts={"completed": 8, "running": 1})
    issues = validate_provider_operation_summary(summary, workflow_run_id="different-run", max_parallel_agents=0, require_terminal=True)
    codes = {issue["code"] for issue in issues}
    assert {"identity_mismatch", "terminal_has_active_children"} <= codes
    assert "concurrency_ceiling_exceeded" not in codes


@pytest.mark.parametrize("limit,available", [(0, True), (4, False)])
def test_research_artifact_uses_admitted_root_limit(tmp_path, limit, available):
    manifest, event = _manifest(), _synth_event()
    manifest["children"][0]["payload"] = {"provider_native_max_children": limit}
    event.payload["provider_operation_summary"].update(child_count=9, peak_parallel_agents=9, child_status_counts={"completed": 9})
    event.payload["provider_native_max_children"] = 0
    result = materialize_research_fanout_artifact(tmp_path, manifest=manifest, synth_event=event)
    assert (result["provider_operation_summary_status"] == "available") is available
