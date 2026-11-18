"""Candidate reader retries retain frozen authority, not the anchor Task contract."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
from tests.test_reader_fanout_runtime import _RecordingTransport
from zf.core.config.schema import ProjectConfig, RoleConfig, WorkflowConfig, ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.fanout import FanoutChild, FanoutContext
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.reader_fanout_retry import prepare_fresh_reader_retry
from zf.runtime.task_contract_snapshot import TaskContractSnapshotError


def _prepared_candidate(tmp_path):
    seeded_runtime = _runtime(tmp_path)
    seeded = _seed_frozen_candidate(seeded_runtime)
    role = RoleConfig(name="verify", backend="mock", role_kind="reader")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(seeded_runtime.state_dir)),
        roles=[role],
        workflow=WorkflowConfig(flow_metadata={"result_protocol": {"mode": "blocking"}}),
    )
    runtime = Orchestrator(
        seeded_runtime.state_dir, config, _RecordingTransport(),
        project_root=seeded_runtime.project_root,
    )
    payload = {
        "workflow_run_id": seeded["run_id"], "task_id": "FLOW-ANCHOR",
        "output_profile_id": "candidate-verify", "target_commit": seeded["candidate_head"],
        "candidate_snapshot_event_id": "evt-candidate-ready",
    }
    context = FanoutContext(
        fanout_id="fanout-candidate", stage_id="issue-lanes-verify",
        topology="fanout_reader", trace_id=seeded["run_id"],
        trigger_event_id="evt-candidate-ready", target_ref=seeded["candidate_head"],
        expected_children=[],
    )
    child = FanoutChild(child_id="verify", role_instance="verify",
                        target_ref=seeded["candidate_head"], payload=payload)
    prepared = runtime._prepare_reader_fanout_child_operation(
        context=context, child=child, role=role, causation_id="evt-candidate-ready",
    )
    assert not prepared["skip"]
    return runtime, role, context, payload


def test_fresh_candidate_reader_retry_preserves_freeze_and_replaces_call(tmp_path):
    runtime, role, context, original = _prepared_candidate(tmp_path)
    original = deepcopy(original)
    child = {"child_id": "verify", "target_ref": context.target_ref,
             "payload": deepcopy(original)}
    prepared = prepare_fresh_reader_retry(
        runtime, manifest={"fanout_id": context.fanout_id, "stage_id": context.stage_id,
                           "trace_id": context.trace_id,
                           "trigger_event_id": context.trigger_event_id,
                           "target_ref": context.target_ref},
        child=child, role=role, attempt=1,
        previous_dispatch=ZfEvent(type="fanout.child.dispatched", payload=original),
    )
    assert prepared is not None
    new = child["payload"]
    for field in ("contract_snapshot_ref", "contract_snapshot_digest",
                  "target_snapshot_ref", "target_snapshot_digest", "target_commit",
                  "task_map_generation", "candidate_snapshot_event_id"):
        assert new[field] == original[field]
    for field in ("operation_id", "attempt_id", "result_scratch_ref"):
        assert new[field] != original[field]
    assert new["parent_operation_id"] == original["operation_id"]


@pytest.mark.parametrize("field", [
    "workflow_run_id", "task_id", "task_map_generation", "target_commit",
    "contract_snapshot_digest", "target_snapshot_digest", "plan_artifact_package_id",
])
def test_candidate_reader_retry_rejects_changed_identity(tmp_path, field):
    runtime, _, context, payload = _prepared_candidate(tmp_path)
    payload[field] = "foreign-value"
    with pytest.raises(TaskContractSnapshotError):
        runtime._prepare_reader_contract_target(
            SimpleNamespace(payload=payload, target_ref=context.target_ref),
        )


def test_candidate_reader_retry_rejects_superseded_freeze(tmp_path):
    runtime, _, context, payload = _prepared_candidate(tmp_path)
    frozen = next(e for e in runtime.event_log.read_all() if e.id == "evt-candidate-ready")
    runtime.event_log.append(ZfEvent(
        type="candidate.ready", correlation_id=context.trace_id,
        payload={**frozen.payload, "candidate_head_commit": "d" * 40},
    ))
    with pytest.raises(TaskContractSnapshotError):
        runtime._prepare_reader_contract_target(
            SimpleNamespace(payload=payload, target_ref=context.target_ref),
        )
