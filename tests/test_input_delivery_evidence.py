"""Delivery evidence must bind real body, source occurrence and consumer."""
import json

import pytest

from tests.test_input_delivery_admission import _operation, _deliver
from zf.runtime.artifact_read_ledger import seal_read_ledger
from zf.runtime.artifact_read_ledger_validation import validate_read_rows
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.mark.parametrize("fault", ["actor", "operation", "dispatch", "occurrence", "path",
    "range", "source_hash", "body_hash", "session", "call", "time", "inline_witness", "witness_file"])
def test_invalid_rows_cannot_supply_coverage_but_valid_replacement_can(tmp_path, fault):
    runtime, payload, prepared, _, _ = _operation(tmp_path)
    _deliver(runtime, payload, prepared)
    descriptor = seal_read_ledger(runtime.state_dir, "attempt-1")
    rows = [json.loads(line) for line in hydrate_sidecar_ref(runtime.state_dir, descriptor).payload.splitlines()]
    policy = payload["input_consumption_policy"]
    assert not validate_read_rows(runtime.state_dir, policy=policy, rows=rows)
    original = dict(rows[0]); row = rows[0]
    key = {"actor": "consumer_actor", "operation": "operation_id", "dispatch": "dispatch_id",
           "occurrence": "occurrence_id", "source_hash": "artifact_sha256", "body_hash": "output_sha256",
           "session": "provider_session_id", "call": "provider_call_id"}.get(fault)
    if key:
        row[key] = "foreign"
    elif fault == "path": row["json_path"] = "$.missing"
    elif fault == "range": row["page_end"] += 1
    elif fault == "time": row["read_at"] = "9999-01-01T00:00:00+00:00"
    elif fault == "inline_witness":
        row["_provider_observation"] = hydrate_sidecar_ref(runtime.state_dir, row.pop("provider_observation_ref")).payload
    elif fault == "witness_file":
        (runtime.state_dir / row["provider_observation_ref"]["ref"]).write_text('{}')
    assert validate_read_rows(runtime.state_dir, policy=policy, rows=rows)
    if fault != "witness_file":
        assert not validate_read_rows(runtime.state_dir, policy=policy, rows=[*rows, original])


def test_recovery_does_not_treat_host_only_read_as_complete_delivery(tmp_path):
    from zf.runtime.artifact_input_recovery import build_input_recovery
    from zf.runtime.artifact_read_ledger import active_read_ledger_path

    runtime, payload, prepared, _, _ = _operation(tmp_path)
    _deliver(runtime, payload, prepared, output="summary")
    manifest = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
    identity = {"actor": "dev-1", "role": "dev", "provider": "codex",
                "purpose": "implementation", "operation_id": prepared.operation_id}
    def recover():
        reads = [json.loads(line) for line in active_read_ledger_path(runtime.state_dir, "attempt-1").read_text().splitlines()]
        return build_input_recovery(state_dir=runtime.state_dir, manifest=manifest, identity=identity,
            events=runtime.event_log.read_all(), required=payload["required_reads"], reads=reads)
    before = {str(p): p.read_bytes() for p in runtime.state_dir.rglob("*") if p.is_file()}
    report = recover()
    assert report["historical_missing"] == 0
    assert report["provider_delivery"]["missing_count"] == 1
    assert report["next_action"] == "repair_provider_delivery"
    assert {str(p): p.read_bytes() for p in runtime.state_dir.rglob("*") if p.is_file()} == before
    _deliver(runtime, payload, prepared)
    assert recover()["provider_delivery"]["status"] == "complete"


def test_current_task_and_handoff_keep_delivery_diagnosis(tmp_path):
    from zf.core.state.task_attempts import TaskAttemptStore
    from zf.core.task.schema import Task, TaskExecutionBinding
    from zf.core.task.store import TaskStore
    from zf.runtime.artifact_input_recovery import current_task_input_recovery
    from zf.runtime.handoff_summary import project_handoff_summary

    runtime, payload, prepared, _, _ = _operation(tmp_path)
    _deliver(runtime, payload, prepared, output="summary")
    task = Task(id="T1", title="Implement", status="in_progress", assigned_to="dev-1",
        active_dispatch_id="attempt-1", execution_binding=TaskExecutionBinding(workflow_run_id="run-1"))
    TaskStore(runtime.state_dir / "kanban.json").add(task)
    TaskAttemptStore(runtime.state_dir / "task_attempts.json").ensure_for_dispatch(
        run_id="run-1", task_id="T1", dispatch_id="attempt-1", role="dev", instance_id="dev-1",
        operation_id=prepared.operation_id, briefing_ref="", created_at="2026-11-22T00:00:00+00:00",
        lease_expires_at="", max_attempts=3)
    report = current_task_input_recovery(runtime.state_dir, runtime.event_log.read_all(), task)
    assert report["provider_delivery"]["missing_count"] == 1
    summary = project_handoff_summary(runtime.state_dir, "T1", task=task)
    assert summary["input_recovery"]["provider_delivery"]["missing_count"] == 1
    assert any(row["kind"] == "provider_input_delivery" for row in summary["missing_evidence"])
