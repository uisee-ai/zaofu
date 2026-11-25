"""Shared, immutable Candidate registration faults must have one actionable owner."""
from dataclasses import replace

import pytest

from tests.test_reader_fanout_runtime import _RecordingTransport, _config
from zf.core.config.schema import FanoutChildConfig, RoleConfig
from zf.core.events import ZfEvent
from zf.runtime.candidate_infrastructure_recovery import pending_candidate_infrastructure_diagnoses
from zf.runtime.orchestrator import Orchestrator


@pytest.fixture
def batch(tmp_path):
    config = _config()
    config.workflow.flow_metadata = {"result_protocol": {"mode": "blocking"}}
    config.roles = [RoleConfig(name=f"verify-{i}", backend="mock", role_kind="reader") for i in range(8)]
    stage = config.workflow.stages[0]
    stage.roles = []
    stage.target_ref = ""
    stage.children = [FanoutChildConfig(role_instance=r.instance_id,
        payload={"task_id": "ANCHOR", "output_profile_id": "candidate-verify"}) for r in config.roles]
    state = tmp_path / "state"
    state.mkdir()
    transport = _RecordingTransport()
    orch = Orchestrator(state, config, transport)
    event = ZfEvent(type="candidate.ready", origin="kernel", actor="zf-cli", correlation_id="run-1",
        payload={"schema_version": "candidate-freeze-receipt.v1", "workflow_run_id": "run-1",
                 "task_id": "ANCHOR", "pdd_id": "ANCHOR", "freeze_id": "bad-freeze", "freeze_receipt_digest": "a"*64,
                 "candidate_head_commit": "c"*40, "candidate_ref":"refs/heads/candidate/test",
                 "task_map_generation": "map-2"})
    orch.event_log.append(event)
    return orch, event, transport


def test_shared_registration_rollback_routes_once_and_corrected_freeze_registers(batch, monkeypatch):
    orch, event, transport = batch
    calls = []
    original = orch._prepare_reader_fanout_child_operation
    # Mock the Git materialization boundary, keep authority, registration and sending real.
    monkeypatch.setattr(orch, "_pin_reader_target_or_reject", lambda **kwargs: True)
    def prepare(**kwargs):
        calls.append((kwargs["context"].trigger_event_id, kwargs["child"].child_id))
        return original(**kwargs)
    monkeypatch.setattr(orch, "_prepare_reader_fanout_child_operation", prepare)
    assert orch._maybe_start_reader_fanout(event)
    assert orch._maybe_start_reader_fanout(event)
    rows = orch.event_log.read_all()
    assert len(calls) == 1
    failure, = [e for e in rows if e.type == "fanout.cancelled"]
    assert failure.payload["candidate_snapshot_event_id"] == event.id
    assert not transport.sent
    assert not any(e.type in {"fanout.child.failed","test.failed","review.rejected","candidate.rework.requested"} for e in rows)
    action, = pending_candidate_infrastructure_diagnoses(rows)
    assert (action["action"], action["suggested_route"], action["source_event_id"]) == (
        "diagnose-attention", "run_manager_resident_agent", failure.id)
    from tests.test_candidate_verification_authority import _seed_frozen_candidate
    _seed_frozen_candidate(orch, run_id="run-1", generation="map-2", candidate_head=event.payload["candidate_head_commit"])
    corrected = next(e for e in orch.event_log.read_all() if e.id == "evt-candidate-ready")
    assert orch._maybe_start_reader_fanout(corrected)
    assert len([c for c in calls if c[0] == corrected.id]) == 8
    all_events = orch.event_log.read_all()
    requests = [e for e in all_events if e.type == "workflow.operation.requested"]
    assert len(requests) == len(transport.sent) == 8, [(e.type, e.payload.get("reason")) for e in all_events[-24:]]
    assert not pending_candidate_infrastructure_diagnoses(all_events)



def test_role_specific_assignment_error_does_not_cancel_other_readers(batch, monkeypatch):
    orch, event, _ = batch
    calls = []
    def prepare(*, child, **kw):
        calls.append(child.child_id)
        if child.role_instance == "verify-0":
            raise ValueError("Candidate Reader has no assignment for its own role")
        return {"skip": True}
    monkeypatch.setattr(orch, "_prepare_reader_fanout_child_operation", prepare)
    assert orch._maybe_start_reader_fanout(event)
    assert len(calls) == 8
    rows = orch.event_log.read_all()
    assert not any(e.type == "fanout.cancelled" for e in rows)
    failed, = [e for e in rows if e.type == "fanout.child.failed"]
    assert failed.payload["role_instance"] == "verify-0"


def test_refused_freeze_retires_only_on_native_receipt_for_same_binding():
    from zf.runtime.candidate_registration_failure import is_freeze_failure

    identity = {"workflow_run_id": "run-1", "generation_id": "generation",
                "task_map_generation": "task-map", "plan_artifact_package_digest": "package",
                "candidate_head_commit": "a"*40}
    failure = ZfEvent(type="candidate.freeze.blocked", origin="kernel", causation_id="evt-admitted",
        correlation_id="run-1", payload={**identity, "generation_admitted_event_id": "evt-admitted",
            "source":"task_pipeline_freeze", "failure_kind":"infra", "failure_scope":"candidate",
            "failure_class":"candidate_authority_invalid", "diagnosis_owner":"run_manager"})
    assert is_freeze_failure(failure)
    action, = pending_candidate_infrastructure_diagnoses([failure])
    assert action["source_event_id"] == failure.id and action["suggested_route"] == "run_manager_resident_agent"
    ready = ZfEvent(type="candidate.ready", origin="kernel", correlation_id="run-1", payload={**identity,
        "schema_version":"candidate-freeze-receipt.v1", "freeze_receipt_digest":"digest"})
    assert pending_candidate_infrastructure_diagnoses([failure, replace(ready, payload={**ready.payload, "workflow_run_id":"other"})])
    assert not pending_candidate_infrastructure_diagnoses([failure,ready])


@pytest.mark.parametrize("variant,blocks_replay", [
    ("old-roster", True), ("transport", False), ("legacy-row", False), ("missing-file", False), ("different-reason", False),
])
def test_legacy_repeat_requires_evidence_of_obsolete_completion(tmp_path, variant, blocks_replay):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.candidate_registration_failure import obsolete_roster_failure
    from zf.runtime.workflow_resume import build_workflow_batch_resume_checkpoints

    rows = ["NEXT"] if variant == "legacy-row" else [{"task_id": "NEXT", "supersedes_task_ids": ["OLD"]}]
    def sidecar(kind, payload):
        return write_immutable_json_sidecar(tmp_path, payload, root="test-artifacts", kind=kind,
                                           schema_version="fixture.v1", created_by="test")
    m = sidecar("task_map",{"tasks": rows, "amend": {"superseded_task_ids":["OLD"]}})
    body = {"schema_version": "candidate-freeze-receipt.v1", "freeze_id": "f1", "workflow_run_id": "r1",
            "task_map_generation": "g", "completed_task_ids": ["OLD", "NEXT"],
            "task_map_ref": m["ref"], "task_map_digest": m["sha256"]}
    f = sidecar("candidate_freeze_receipt",body)
    if variant == "missing-file":
        (tmp_path / m["ref"]).unlink()
    candidate = ZfEvent(type="candidate.ready", origin="kernel", correlation_id="r1",
        payload={**body, "freeze_receipt_ref": f, "freeze_receipt_digest": f["sha256"]})
    base = {"workflow_run_id":"r1", "fanout_id":"batch", "stage_id":"verify"}
    start = ZfEvent(type="fanout.started", origin="kernel", correlation_id="r1", payload={**base,
        "trigger_event_id":candidate.id, "expected_children":[{"child_id":"x"}, {"child_id":"y"}]})
    aggregate = ZfEvent(type="fanout.aggregate.completed", origin="kernel", correlation_id="r1", payload={**base,
        "status":"failed", "failure_kind":"infra", "failed_children":["x", "y"], "failure_event":"test.failed"})
    reason = "durable operation preregistration failed: retained Task missing or duplicated in Plan: OLD"
    failures = [ZfEvent(type="fanout.child.failed", origin="kernel", causation_id=start.id, correlation_id="r1",
        payload={**base, "failure_kind":"infra", "failure_scope":"operation_preregistration", "child_id":role,
                 "reason":"temporary transport error" if variant == "transport" or (role == "y" and variant == "different-reason") else reason})
        for role in ("x", "y")]
    events = [candidate,start,*failures,aggregate]
    assert obsolete_roster_failure(tmp_path, events, aggregate, candidate) is blocks_replay
    checkpoints = build_workflow_batch_resume_checkpoints(tmp_path, events=events)
    assert (not checkpoints) is blocks_replay
