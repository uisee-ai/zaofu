"""A previously completed task can leave the frozen roster only through verified successors."""
from copy import deepcopy
from dataclasses import replace

import pytest

from tests.test_plan_artifact_package import _package
from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.completed_task_replacements import completed_task_replacements, validate_completed_task_replacements
from zf.runtime.plan_artifact_package import write_plan_artifact_package
from zf.runtime.task_contract_snapshot import write_task_contract_snapshot


def replacement_case(state, fault=""):
    events = []
    previous = {}

    def event(kind, body, child=None):
        row = ZfEvent(type=kind, origin="kernel", task_id=child,
                      payload={"workflow_run_id": "run-1", **body})
        events.append(row)
        return row

    def sidecar(body):
        return write_immutable_json_sidecar(state, body, root="fixtures/proofs",
            kind="test", schema_version="test.v1", created_by="test")

    def generation(index, ids, predecessors):
        base = str(index) * 40
        head = str(index + 1) * 40
        generation_id = "g" + str(index)
        rows = [{"task_id": tid, "supersedes_task_ids": predecessors, "base_commit": base,
                 "source_refs": ["git:" + base]} for tid in ids]
        amended = ({"supersedes_task_map_ref": previous["task_map_ref"],
                    "superseded_task_ids": [] if fault == "undeclared" and index == 2 else predecessors}
                   if predecessors else {})
        task_map = sidecar({"tasks": rows, "amend": amended})
        package = _package(state, generation=generation_id)
        next(port for port in package["produced"] if port["logical_name"] == "task_map").update(
            ref=task_map["ref"], sha256=task_map["sha256"])
        if previous and not (fault == "orphan" and index == 2):
            package.update(supersedes_package_ref=previous["plan_artifact_package_ref"],
                           supersedes_package_digest=previous["plan_artifact_package_digest"])
        pd = write_plan_artifact_package(state, package)
        binding = {"workflow_run_id": "run-1", "generation_id": generation_id,
            "task_map_generation": generation_id, "plan_artifact_package_ref": pd["ref"],
            "plan_artifact_package_digest": pd["sha256"], "task_map_ref": task_map["ref"],
            "task_map_digest": task_map["sha256"], "candidate_ref": "refs/heads/candidate/product"}
        event("task.pipeline.generation.admitted", {**binding, "task_ids": ids,
              "schema_version": "task-pipeline-generation.v1"})
        for tid in ids:
            snap = write_task_contract_snapshot(state, {**binding, "schema_version": "task-contract-snapshot.v1",
                "task_id": tid, "task_ref": "task/" + tid, "base_commit": base, "contract_revision": "r",
                "acceptance_criteria": [{"statement":"checked", "acceptance_id":"a", "verification_tier":"task_non_smoke",
                                        "verification_owner":"task_verify"}], "verification_commands": []})
            result = sidecar({"verdict": "passed"})
            call = {"request_hash": "request-" + tid, "operation_id": "op-" + tid, "control_result_ref": result}
            admission = event("workflow.call.result.admitted", {**call, "semantic_verdict": "passed"})
            if not (tid == "Y" and fault == "unverified_cosuccessor"):
                event("task.pipeline.verify.completed", {**binding, **call, "contract_revision":"r",
                    "target_commit":head, "semantic_submit_admission_event_id":admission.id,
                    "call_result_envelope_ref": sidecar({"schema_version":"call-result-envelope.v1"}),
                    "contract_snapshot_ref":snap["ref"],"contract_snapshot_digest":snap["sha256"]}, tid)
            receipt = sidecar({**binding, "schema_version":"task-integration-receipt.v1", "task_id":tid,
                               "task_commit":head, "contract_revision":"r", "new_candidate_head":head,
                               "status":"integrated"})
            event("task.done", {**binding, "candidate_head":head, "integration_receipt_ref":receipt}, tid)
        native = {**binding, "schema_version":"candidate-freeze-receipt.v1","status":"frozen",
                  "freeze_id":"freeze-" + generation_id,"candidate_head_commit":head,
                  "completed_task_ids":ids}
        frozen = sidecar(native)
        event("candidate.ready", {**native,"freeze_receipt_digest":frozen["sha256"],"freeze_receipt_ref":frozen})
        previous.update(binding)
        return binding

    generation(1, ["OLD"], [])
    generation(2, ["X", "Y"], ["OLD"])
    latest = generation(3, ["FINAL"], ["X", "Y"])
    return latest, events


def test_split_merge_replacement_keeps_all_historical_edges(tmp_path):
    latest, events = replacement_case(tmp_path)
    every = {"OLD", "X", "Y", "FINAL"}
    proofs = completed_task_replacements(tmp_path, events, latest, every)
    assert set(proofs) == {"OLD", "X", "Y"}
    assert [link["edges"] for link in proofs["OLD"]] == [
        {"OLD":["X","Y"]}, {"X":["FINAL"],"Y":["FINAL"]}]
    candidate = {**latest, "completed_task_ids":["FINAL"], "superseded_task_ids":sorted(proofs),
                 "completed_task_replacements":proofs}
    validate_completed_task_replacements(tmp_path, events, candidate)
    with pytest.raises(ValueError, match="frozen roster"):
        validate_completed_task_replacements(tmp_path, events, {
            **candidate, "completed_task_ids":[], "superseded_task_ids":sorted(every)})
    wrong = deepcopy(candidate)
    del wrong["completed_task_replacements"]["OLD"]
    with pytest.raises(ValueError, match="frozen roster"):
        validate_completed_task_replacements(tmp_path, events, wrong)
    with pytest.raises(ValueError, match="frozen roster"):
        validate_completed_task_replacements(tmp_path, events, {**candidate, "completed_task_ids":sorted(every)})


@pytest.mark.parametrize("fault", ["undeclared", "orphan", "unverified_cosuccessor", "foreign_base", "damaged_freeze"])
def test_invalid_lineage_never_drops_historical_acceptance(tmp_path, fault):
    latest, events = replacement_case(tmp_path, fault)
    oldest = next(e for e in events if e.type == "candidate.ready")
    if fault == "foreign_base":
        events[events.index(oldest)] = replace(oldest, payload={**oldest.payload,"workflow_run_id":"another"})
    if fault == "damaged_freeze":
        (tmp_path / oldest.payload["freeze_receipt_ref"]["ref"]).write_text("{}")
    with pytest.raises(ValueError):
        completed_task_replacements(tmp_path, events, latest, {"OLD","X","Y","FINAL"})


def test_refreeze_surfaces_one_bound_diagnostic_and_preserves_original_receipt(tmp_path, monkeypatch):
    from tests.test_candidate_freeze_receipts import mixed_generation_freeze_case
    from zf.runtime import completed_task_replacements as resolver
    from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
    from zf.runtime.task_pipeline_terminal import reconcile_task_pipeline_freeze

    runtime = mixed_generation_freeze_case(tmp_path)
    contexts = task_pipeline_generation_contexts(runtime.event_log.read_all())
    before = next(e for e in runtime.event_log.read_all() if e.type == "candidate.ready")
    def fault(*args, **kwargs):
        raise ValueError("missing immutable parent Task Map")
    monkeypatch.setattr(resolver, "completed_task_replacements", fault)
    for _ in range(2):
        assert not reconcile_task_pipeline_freeze(runtime, generation_contexts=contexts)
    events = runtime.event_log.read_all()
    assert [e for e in events if e.type == "candidate.ready"] == [before]
    blocked, = [e for e in events if e.type == "candidate.freeze.blocked"]
    assert blocked.correlation_id == "run-1" and blocked.causation_id == contexts["TASK-B"]["generation_admitted_event_id"]
    assert blocked.payload["candidate_head_commit"] == before.payload["candidate_head_commit"]
    assert blocked.payload["diagnosis_owner"] == "run_manager" and blocked.payload["reason"] == "missing immutable parent Task Map"
