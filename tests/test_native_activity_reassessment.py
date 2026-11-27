"""Legacy samples enter only the newly granted Reader request as historical evidence."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_candidate_reader_reassessment import setup_scope
from tests.test_orchestrator_agent_semantic_failure import _port
from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
from tests import test_milestone_provider_activity as native_fixture
from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import RoleConfig
from zf.core.events import ZfEvent
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.candidate_reader_reassessment import (
    dispatch_pending_reassessments, request_reader_reassessment, validate_submission)
from zf.runtime.native_activity_reassessment import IDENTITY
from zf.runtime.plan_artifact_package import build_plan_artifact_package, package_event_payload, write_plan_artifact_package
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot
from zf.runtime.provider_usage_reconciliation import build_disk_usage_event
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def incident(runtime, monkeypatch, fault, package_digest):
    then = datetime.now(timezone.utc) - timedelta(hours=1)
    stamp = lambda s: (then + timedelta(seconds=s)).isoformat()
    monkeypatch.setattr(native_fixture, "stamp", stamp)
    stage, hook, usage = native_fixture.dispatch_input(runtime.state_dir, "TASK-1", "verify", start=5, end=11,
        run="run-candidate-authority", generation="generation-1", package=package_digest)
    p = stage.payload
    p["task_pipeline_entry_mode"] = "verify_only"
    identity = {k: p[k] for k in IDENTITY}
    path, = runtime.state_dir.glob("workdirs/*/codex-home/sessions/*/*/*/*.jsonl")
    hook.payload["transcript_path"] = str(path)
    session = hook.payload["session_id"]
    role = RoleConfig(name=hook.actor, backend="codex")
    observed = native_fixture.read(runtime.state_dir, hook.actor, session, path)
    sample = build_disk_usage_event(role=role, usage=observed, config=None)
    sample.origin, sample.ts = "kernel", usage.ts
    sample.payload["usage_binding_status"] = "unattributed"
    sent = ZfEvent(type="task.attempt.sent", origin="kernel", actor="orchestrator", task_id=p["task_id"], ts=stamp(1),
        payload={**identity, "placement_epoch": "1", "role_instance": hook.actor,
                 "schema_version": "task-attempt.v1", "identity_version": "operation-v2"})
    started = ZfEvent(type="workflow.operation.started", origin="kernel", ts=stamp(2), task_id=p["task_id"],
        payload={**identity, "request_hash": p["request_hash"], "role_instance": hook.actor,
                 "active_attempt_id": p["attempt_id"]})
    closed = ZfEvent(type="workflow.operation.settled", origin="kernel", task_id=p["task_id"],
        payload={**identity, "request_hash": p["request_hash"]}, ts=stamp(15))
    if fault == "forged":
        sent.origin = "worker"
    if fault == "lease":
        sent.payload["lease_id"] = "different"
    if fault == "usage":
        sample.payload["usage"]["input_tokens"] += 1
    if fault == "symlink":
        original = path.rename(path.with_suffix(".secret"))
        path.symlink_to(original)
    events = [sent, started, stage, hook, sample, closed]
    if fault == "expired":
        events.insert(-2, ZfEvent(type="task.attempt.failed", origin="kernel", actor="orchestrator", ts=stamp(8),
            payload={**identity, "failure_class": "lease_expired"}))
    if fault == "blocked":
        events.insert(-2, ZfEvent(type="workflow.operation.blocked", origin="kernel", task_id=p["task_id"],
                                 ts=stamp(8), payload={**identity, "request_hash": p["request_hash"]}))
    if fault == "ended":
        events = [sent, started, stage, hook, closed, sample]
    for event in events:
        runtime.event_log.append(event)
    return sample


@pytest.mark.parametrize("fault", ["", "no_selection", "no_original_grant", "revoked", "revoked_before_dispatch", "forged", "lease", "usage", "ended", "expired", "blocked", "symlink"])
def test_reassessment_binds_legacy_native_activity_only_with_both_grants(tmp_path, monkeypatch, fault):
    runtime = _runtime(tmp_path)
    runtime.config.workflow.flow_metadata["milestone_handoff"] = True
    run = "run-candidate-authority"
    contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    contract["contract_digest"] = stable_json_sha256(contract)
    ports = [_port(runtime.state_dir, name) for name in ("goal_claim_set", "task_map", "planning_result")]
    package = build_plan_artifact_package(workflow_run_id=run, flow_kind="prd", producer_stage_id="prd-plan",
        run_contract=write_run_contract_snapshot(runtime.state_dir, contract), plan_revision="r1",
        task_map_generation="generation-1", produced=ports, required_ports=[p["logical_name"] for p in ports])
    package_ref = write_plan_artifact_package(runtime.state_dir, package)
    runtime.event_writer.emit("plan.artifact_package.admitted", correlation_id=run,
                              payload=package_event_payload(package, package_ref, status="admitted"))
    sample = incident(runtime, monkeypatch, fault, package_ref["sha256"])
    seeded = _seed_frozen_candidate(runtime, package_fields={"plan_artifact_package_id": package_ref["package_id"],
        "plan_artifact_package_ref": package_ref["ref"], "plan_artifact_package_digest": package_ref["sha256"]})
    runtime, marker, old, request, _, _ = setup_scope(
        (runtime, seeded, None, None, None, None), monkeypatch, native=fault != "no_original_grant",
        next_action="inspect_host_receipt" if fault == "no_selection" else "reassess_native_activity")
    policy = runtime.config.workflow.orchestration
    if fault == "no_original_grant":
        policy.autonomous_actions.append("reassess_native_activity")
    if fault == "revoked":
        policy.autonomous_actions.remove("reassess_native_activity")
    original = hydrate_sidecar_ref(runtime.state_dir, request["milestone_handoff_ref"]).payload
    ref = original["task_pipeline_lifecycles_ref"]
    lifecycle = hydrate_sidecar_ref(runtime.state_dir, ref).payload
    assert lifecycle["summary"]["provider_activity"]["unobserved_task_ids"] == ["TASK-1"]
    args = dict(marker=marker, operation_id=old["operation_id"], finding_ids=["missing-evidence"])
    recorded = request_reader_reassessment(runtime, **args)
    scope = hydrate_sidecar_ref(runtime.state_dir, recorded.payload["evidence_scope_ref"]).payload
    if fault and fault != "revoked_before_dispatch":
        assert "native_reassessment" not in scope
        return
    supplemental = scope["native_reassessment"]
    body = hydrate_sidecar_ref(runtime.state_dir, supplemental["source"]).payload
    assert body["summary"]["original_lifecycle_digest"] == ref["sha256"]
    assert body["summary"]["witnesses"][0]["usage_event_id"] == sample.id
    assert len(body["summary"]["witnesses"][0]["prefix_sha256"]) == 64
    assert next(iter(supplemental["windows"].values()))["peak_window"]["stage_counts"] == {"verify": 1}
    assert str(tmp_path) not in json.dumps(body) and "private transcript text" not in json.dumps(body)
    assert hydrate_sidecar_ref(runtime.state_dir, ref).payload == lifecycle
    assert request_reader_reassessment(runtime, **args).id == recorded.id
    assert [e.id for e in runtime.event_log.read_all() if e.type == "agent.usage"] == [sample.id]

    role = SimpleNamespace(instance_id="reader-1", name="verify", role_kind="reader", skills=[])
    runtime.all_roles = lambda: [role]
    runtime._ensure_fanout_role_dispatchable = lambda **kw: True
    runtime._record_skill_provenance = lambda **kw: []
    sends = []
    runtime._dispatch_reader_fanout_child = lambda **kw: sends.append(kw)
    if fault == "revoked_before_dispatch":
        policy.autonomous_actions.remove("reassess_native_activity")
        dispatch_pending_reassessments(runtime)
        assert not sends
        assert any(e.type == "candidate.reader.reassessment.blocked" and e.causation_id == recorded.id
                   for e in runtime.event_log.read_all())
        return
    dispatch_pending_reassessments(runtime)
    dispatch, = sends
    payload = dispatch["child"].payload
    manifest = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
    op = reduce_workflow_operations(runtime.event_log.read_all())[dispatch["prepared_dispatch"]["prepared_call"].operation_id]
    signed = hydrate_sidecar_ref(runtime.state_dir, op["request_ref"]).payload["request"]
    result = {"finding_results":[{"finding_id":"missing-evidence", "explanation":"Read the supplemental measurement",
        "outcome":"supported", "lifecycle_window_ids":list(supplemental["windows"])}]}
    for r in payload["required_reads"]:
        read_attempt_artifact(runtime.state_dir, manifest=manifest, role=role.instance_id, actor=role.instance_id,
                              source_id=r["source_id"], artifact_id=r["artifact_id"], json_path=r["json_path"])
    with pytest.raises(ValueError, match="retrospective-native-activity"):
        validate_submission(runtime.state_dir, signed, result, runtime.event_log.read_all())
    source = supplemental["source"]
    read_attempt_artifact(runtime.state_dir, manifest=manifest, role=role.instance_id, actor=role.instance_id,
                          source_id=source["source_id"], artifact_id=source["artifact_id"], json_path="$.summary")
    validate_submission(runtime.state_dir, signed, result, runtime.event_log.read_all())


def test_native_reassessment_config_requires_reader_authority():
    profile = Path(__file__).resolve().parents[1] / "zf-prd-v5.yaml"
    original = profile.read_text()
    entry = "autonomous_actions: [delegate, continue, rework, reassess_reader_evidence]"
    both = "autonomous_actions: [delegate, continue, rework, reassess_reader_evidence, reassess_native_activity]"
    assert original.count(entry) == 1
    config = load_config(profile, source_text=original.replace(entry, both))
    assert "reassess_native_activity" in config.workflow.orchestration.flow_policies["prd"].autonomous_actions
    with pytest.raises(ConfigError, match="requires reassess_reader_evidence"):
        load_config(profile, source_text=original.replace(entry, "autonomous_actions: [reassess_native_activity]"))
