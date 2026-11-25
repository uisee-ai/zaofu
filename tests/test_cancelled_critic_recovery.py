"""A cancelled review can only be replaced against its existing, fully bound evidence."""

from copy import deepcopy
import pytest

from tests.test_critic_handoff_retry import apply, checkpoint
from zf.core.events import ZfEvent
from zf.runtime import cancelled_critic_recovery as recovery
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.fanout import FanoutManifestProjector
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


@pytest.fixture
def stopped(checkpoint, monkeypatch):
    svc, req, old_payload, event = checkpoint
    first = apply(svc, req, old_payload)
    marker = first["critic_handoff_event_id"]
    log = svc.writer.event_log
    original = next(e for e in log.read_all() if e.id == marker)
    monkeypatch.setattr(
        recovery,
        "admitted_unissued_planner",
        lambda *a, **kw: {
            k: original.payload[k]
            for k in ("planner_admitted_event_id", "planner_result_ref", "planner_request_hash")
        },
    )

    def sidecar(body):
        return write_immutable_json_sidecar(
            svc.state_dir, body, root="checks", kind="test", schema_version="test.v1", created_by="test"
        )

    sources = {
        "sources": [
            {
                "source_id": "child-planner-control-result",
                "artifact_id": "planner",
                "allowed_paths": ["$"],
                "sha256": "digest",
            }
        ]
    }
    manifest_ref = sidecar(sources)
    oracle = {
        "planner_admitted_event_id": "admitted",
        "candidate_event_id": "freeze",
        "source_commit": "commit",
        "finding_ids": ["A", "B"],
    }
    request = {
        "workflow_run_id": "run",
        "role_instance": "critic",
        "stage_id": "plan",
        "fanout_id": "old",
        "output_profile_id": "plan-synth",
        "source_manifest_digest": manifest_ref["sha256"],
        "result_identity": {"diagnosis_review": oracle},
        "required_reads": [{"source_id": "planner", "json_path": "$"}],
    }
    ops = WorkflowOperationService(state_dir=svc.state_dir, event_log=log, event_writer=svc.writer)
    op = ops.ensure_operation(
        workflow_run_id="run",
        operation_id="critic-op",
        operation_type="fanout_synth",
        request=request,
        task_id="T",
        role_instance="critic",
        active_attempt_id="original-attempt",
        parent_stage_id="plan",
    )
    h = op.request_hash
    event(
        "fanout.synth.dispatched",
        "dispatch",
        fanout_id="old",
        operation_id="critic-op",
        request_hash=h,
        attempt_id="original-attempt",
        role_instance="critic",
        attempt_source_manifest=manifest_ref,
    )
    event(
        "fanout.cancelled",
        "wrong-cancel",
        fanout_id="old",
        superseded_by="wrong",
        source="superseded_reader_fanout_manifest_closeout",
        reason="superseded_by_newer_replan_attempt",
    )
    svc.writer.append(
        ZfEvent(
            id="stopped",
            type="worker.state.changed",
            origin="kernel",
            actor="critic",
            payload={
                "operation_id": "critic-op",
                "request_hash": h,
                "provider_stop_confirmed": True,
                "fanout_cancelled_event_id": "wrong-cancel",
            },
        )
    )
    cancel = ops.cancel(
        operation_id="critic-op",
        request_hash=h,
        workflow_run_id="run",
        task_id="T",
        reason="cancelled",
        causation_id="wrong-cancel",
        correlation_id="run",
    )
    event(
        "task.attempt.superseded",
        "attempt-ended",
        cause=cancel.id,
        operation_id="critic-op",
        dispatch_id="original-attempt",
    )
    payload = {
        "workflow_run_id": "run",
        "candidate_event_id": "freeze",
        "prior_critic_dispatch_event_id": "dispatch",
        "prior_critic_request_hash": h,
        "planner_admitted_event_id": "admitted",
        "critic_handoff_event_id": marker,
        "retry_cancelled_critic_event_id": cancel.id,
        "approval_ref": "owner-repair",
    }
    return svc, req, event, payload, request, sources


def test_exact_replacement_replays_once_and_retains_the_cancelled_operation(stopped):
    svc, req, _, payload, request, sources = stopped
    reply = apply(svc, req, payload)
    assert reply["ok"], reply.get("reason")
    replay = apply(svc, req, payload)
    assert (
        replay["replayed"]
        and replay["cancelled_critic_retry_event_id"] == reply["cancelled_critic_retry_event_id"]
    )
    events = svc.writer.event_log.read_all()
    assert reduce_workflow_operations(events)["critic-op"]["status"] == "cancelled"
    manifest = FanoutManifestProjector(svc.state_dir).rebuild("old", events)
    assert manifest["status"] == "running" and not manifest.get("synth")
    assert (
        manifest["trigger_event_id"] == "trigger"
        and manifest["critic_handoff_retry_event_id"] == payload["critic_handoff_event_id"]
    )
    assert manifest["children"][0]["result_event_id"] == "producer"
    recovery.verify_replacement_inputs(
        svc.state_dir, manifest["cancelled_critic_checkpoint"], request, sources
    )


@pytest.mark.parametrize(
    "fault",
    [
        "different-freeze",
        "wrong-signature",
        "wrong-parent",
        "wrong-dispatch",
        "different-run",
        "scope",
        "unrelated-stage",
        "live-provider",
        "open-attempt",
        "changed-source",
        "late-admission",
        "wrong-operator",
    ],
)
def test_cannot_replace_a_different_or_unsettled_critic(stopped, fault, monkeypatch):
    svc, req, event, payload, _, _ = stopped
    if fault == "different-freeze":
        payload["candidate_event_id"] = "different"
    elif fault == "wrong-signature":
        payload["prior_critic_request_hash"] = "different"
    elif fault == "wrong-parent":
        payload["planner_admitted_event_id"] = "different"
    elif fault == "wrong-dispatch":
        payload["prior_critic_dispatch_event_id"] = "failure"
    elif fault == "different-run":
        payload["workflow_run_id"] = "different"
    elif fault == "scope":
        payload["operator_feedback"] = ["edited"]
    elif fault == "wrong-operator":
        svc.actor = "reader"
    elif fault == "unrelated-stage":
        event("fanout.started", "new-generation", fanout_id="different", stage_id="plan")
    elif fault == "late-admission":
        event("workflow.call.result.admitted", "late", operation_id="critic-op")
    else:
        log = svc.writer.event_log
        original = log.read_all
        discard = {
            "live-provider": "stopped",
            "open-attempt": "attempt-ended",
            "changed-source": payload["critic_handoff_event_id"],
        }[fault]
        monkeypatch.setattr(log, "read_all", lambda: [e for e in original() if e.id != discard])
    assert not apply(svc, req, payload)["ok"]


@pytest.mark.parametrize("fault", ["source-digest", "permissions", "required", "diagnosis", "write-scope"])
def test_compiled_replacement_must_preserve_the_signed_reads_and_sources(stopped, fault):
    svc, req, _, payload, request, sources = stopped
    marker = apply(svc, req, payload)
    assert marker["ok"], marker.get("reason")
    carrier = FanoutManifestProjector(svc.state_dir).rebuild("old", svc.writer.event_log.read_all())
    current = deepcopy(request)
    current_sources = deepcopy(sources)
    if fault == "source-digest":
        current_sources["sources"][0]["sha256"] = "x"
    if fault == "permissions":
        current_sources["sources"][0]["allowed_paths"] = ["$.other"]
    if fault == "required":
        current["required_reads"] = []
    if fault == "diagnosis":
        current["result_identity"]["diagnosis_review"]["finding_ids"] = ["other"]
    if fault == "write-scope":
        current["workdir_write_scopes"] = ["*"]
    with pytest.raises(ValueError):
        recovery.verify_replacement_inputs(
            svc.state_dir, carrier["cancelled_critic_checkpoint"], current, current_sources
        )


def test_replayed_cancel_stops_the_operations_owned_at_its_ledger_position(stopped):
    from types import SimpleNamespace
    from zf.runtime.cancelled_run_workers import reconcile_pending_stops

    svc, req, event, payload, _, _ = stopped
    assert apply(svc, req, payload)["ok"]
    event(
        "fanout.synth.dispatched",
        "replacement-dispatch",
        fanout_id="old",
        operation_id="replacement",
        attempt_id="replacement-attempt",
        role_instance="critic",
    )
    log = svc.writer.event_log
    stops = []
    runtime = SimpleNamespace(
        state_dir=svc.state_dir,
        _fanout_manifest=lambda fanout: FanoutManifestProjector(svc.state_dir).rebuild(
            fanout, log.read_all()
        ),
        _cancel_superseded_reader_fanout_operations=lambda **kw: stops.append(kw),
    )
    reconcile_pending_stops(runtime, log.read_all())
    assert len(stops) == 1
    assert stops[0]["cancellation_event"].id == "wrong-cancel"
    assert stops[0]["manifest"]["synth"]["last_event_id"] == "dispatch"
    event("fanout.cancelled", "new-cancel", fanout_id="old", reason="new kernel cancellation")
    reconcile_pending_stops(runtime, log.read_all())
    assert len(stops) == 2
    assert stops[1]["cancellation_event"].id == "new-cancel"
    assert stops[1]["manifest"]["synth"]["last_event_id"] == "replacement-dispatch"
