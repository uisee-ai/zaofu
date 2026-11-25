import pytest

from tests.test_candidate_mixed_repair import partition
from tests.test_candidate_task_repair_decision import incident, submit
from zf.core.events import ZfEvent
from zf.runtime.artifact_read_requirements import build_canonical_required_reads
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_handoff import attach_task_handoff


@pytest.fixture
def repair_call(tmp_path, monkeypatch):
    runtime, request, repair = incident(tmp_path, monkeypatch, canonical=True, peer=True)
    repair["finding_dispositions"] = partition(runtime, request)
    _, outcome = submit(runtime, request, repair)
    assert outcome["applied"]
    ready, = [e for e in runtime.event_log.read_all() if e.type == "task_map.ready"]
    ctx = {"trigger_event_id": ready.id, "dispatch_base_commit": ready.payload["source_commit"],
           **{k: ready.payload[k] for k in ("task_map_ref", "task_map_digest", "task_map_generation", "workflow_run_id")}}
    contract = {"task_id": "TASK-2-FIX", "workflow_run_id": ctx["workflow_run_id"],
                "task_map_generation": ctx["task_map_generation"], "acceptance_criteria": [{"acceptance_id": "AC-5"}]}
    def call(stage, *, context=None, snapshot=None):
        payload = {"task_pipeline_stage": stage, "role_instance": "dev-a" if stage == "impl" else "verify-0",
                   "workflow_run_id": ctx["workflow_run_id"], "task_id": contract["task_id"], "operation_id": "op-" + stage}
        attach_task_handoff(runtime, payload, snapshot or contract, generation_context=context or ctx)
        return payload
    return runtime, ctx, contract, call


def test_new_successor_impl_and_independent_verify_receive_only_the_bound_product_finding(repair_call):
    runtime, _, _, call = repair_call
    receipts = []
    for stage, profile in (("impl", "implementation"), ("verify", "task-verify")):
        payload = call(stage)
        source, = payload["input_refs"]
        receipt = hydrate_sidecar_ref(runtime.state_dir, source).payload
        assert [(f["acceptance_id"], f["disposition"]["classification"]) for f in receipt["findings"]] == [
            ("AC-5", "product_defect")]
        assert receipt["predecessor_task_id"] == "TASK-2" and receipt["candidate_commit"] == "c" * 40
        assert receipt["decision_ref"]["sha256"]
        assert not payload.get("task_handoff_ref")
        reads = build_canonical_required_reads({"sources": [source]}, output_profile_id=profile)
        assert [(r["source_id"], r["json_path"], r["artifact_sha256"]) for r in reads] == [
            ("candidate-repair-feedback", "$", source["sha256"])]
        receipts.append(payload["candidate_repair_feedback_ref"])
    assert receipts[0] == receipts[1]


@pytest.mark.parametrize("fault", ["run", "task", "ac", "base", "map"])
def test_mismatched_successor_authority_does_not_deliver(repair_call, fault):
    _, ctx, contract, call = repair_call
    if fault == "run":
        contract["workflow_run_id"] = "other"
    elif fault == "task":
        contract = {**contract, "task_id": "other"}
    elif fault == "ac":
        contract["acceptance_criteria"] = [{"acceptance_id": "AC-99"}]
    elif fault == "base":
        ctx["dispatch_base_commit"] = "d" * 40
    else:
        ctx["task_map_digest"] = "d" * 64
    with pytest.raises(ValueError, match="successor"):
        call("impl", context=ctx, snapshot=contract)


@pytest.mark.parametrize("legacy", [True, False])
def test_replaying_old_operations_preserves_original_input_binding(repair_call, legacy):
    runtime, _, _, call = repair_call
    original = {} if legacy else {"candidate_repair_feedback_ref": call("impl")["candidate_repair_feedback_ref"]}
    ref = write_immutable_json_sidecar(runtime.state_dir, {"request": original}, root="requests",
        kind="request", schema_version="request.v1", created_by="kernel")
    runtime.event_writer.append(ZfEvent(type="workflow.operation.requested", origin="kernel",
        payload={"operation_id": "op-impl", "request_ref": ref}))
    assert call("impl").get("candidate_repair_feedback_ref") == original.get("candidate_repair_feedback_ref")


def test_task_stage_dispatch_reaches_feedback_producer_for_first_impl_and_verify(repair_call, tmp_path, monkeypatch):
    from tests.test_task_pipeline_capsule import dispatch_fixture
    from zf.core.task.schema import Task, TaskContract
    from zf.runtime.task_doc import compute_task_capsule_revisions
    from zf.runtime.task_pipeline_dispatch import dispatch_task_pipeline_stage

    publication, ctx, contract, _ = repair_call
    transport_root = tmp_path / "dispatch-environment"
    transport_root.mkdir()
    runtime, _, snapshot, prepared, _ = dispatch_fixture.__wrapped__(transport_root, monkeypatch)
    from types import SimpleNamespace
    from zf.runtime import call_result_runtime

    def capture_request(*_args, **kwargs):
        prepared.append(kwargs)
        return SimpleNamespace(should_dispatch=False, operation_id=kwargs["payload"]["operation_id"])

    monkeypatch.setattr(call_result_runtime, "prepare_call_operation", capture_request)
    runtime.event_log, runtime.event_writer, runtime.state_dir = publication.event_log, publication.event_writer, publication.state_dir
    runtime.config = publication.config
    task = Task(id=contract["task_id"], title="repair AC-5", contract=TaskContract(behavior="repair AC-5"))
    runtime.task_store.add(task)
    snapshot.update(contract, **compute_task_capsule_revisions(task))
    context = {**ctx, **{k: snapshot[k] for k in ("plan_artifact_package_ref", "plan_artifact_package_digest",
                                                    "plan_artifact_package_id")}, "flow_kind": "prd"}
    for stage in ("impl", "verify"):
        dispatch_task_pipeline_stage(runtime, task=task, policy={}, generation_context=context,
            assignment={"stage": stage, "role_instance": "dev-0", "operation_generation": 1},
            operation_rows=[], attempt_rows=[])
        frozen = prepared[-1]["payload"]
        assert frozen["candidate_repair_feedback_ref"]["sha256"] == frozen["input_refs"][0]["sha256"]
        assert hydrate_sidecar_ref(runtime.state_dir, frozen["input_refs"][0]).payload["task_id"] == task.id
