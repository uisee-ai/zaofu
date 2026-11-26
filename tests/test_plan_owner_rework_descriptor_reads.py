"""A museum catalog revision exercises the real owner and partition evidence chain."""
from copy import deepcopy
import json

import pytest

from tests.test_call_result_runtime import _runtime
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, materialize_attempt_source_ref, read_attempt_artifact
from zf.runtime.artifact_read_snapshot import pin_read_selections
from zf.runtime.artifact_source_versions import pin_source_versions
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.call_result_runtime import prepare_call_operation
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_replan_generation import escalation_generation_id


def museum(tmp_path, fault="", monkeypatch=None):
    runtime = _runtime(tmp_path)
    state, run, stage = runtime.state_dir, "museum", "catalog-plan"

    def save(body, kind):
        return write_immutable_json_sidecar(state, body, root="museum", kind=kind, schema_version="v1", created_by="test")

    def event(etype, eid, payload, cause=None, actor="zf-cli", origin="kernel"):
        e = ZfEvent(id=eid, type=etype, correlation_id=run, causation_id=cause, actor=actor, origin=origin,
                    payload={"workflow_run_id": run, **payload})
        runtime.event_log.append(e)
        return e

    event("run.started", "start", {"run_id": run})
    shared = [{"accession": str(i), "catalogue": "文物登记簿"} for i in range(100)]
    rows = []
    for name, edition in (("child-result-curator", "curator"), ("child-artifact-curator-editorial", "editorial")):
        original = save({"objects": shared, "edition": edition}, "catalog")
        rows.append(materialize_attempt_source_ref(state_dir=state, project_root=runtime.project_root,
                    ref=original["ref"], source_id=name, kind="catalog"))
    historical = build_attempt_source_manifest(workflow_run_id=run, task_id="", attempt_id="prior",
        dispatch_id="prior", sources=rows, metadata={"source_event_id": "museum-draft"})
    history = historical["sources"]
    pin_source_versions(state, history)
    pin_read_selections(state, historical, [])
    bindings = [{k: row[k] for k in ("source_id", "sha256", "ref", "artifact_id")} for row in history]
    ids = {"workflow_run_id": run, "stage_id": stage, "plan_revision": "revision-a", "fanout_id": "prior-fanout"}
    contract = save({**ids, "schema_version": "plan-synth-contract.v1", "source_bindings": bindings,
                     "child_bindings": [{"child_id": "curator", **bindings[0]}]}, "contract")
    result = save({"execution_status": "completed", "verdict": "passed"}, "review")
    operation = {"operation_id": "catalog-review", "attempt_id": "prior", "request_hash": "f" * 64,
                 "control_result_ref": result}
    event("fanout.synth.dispatched", "prior-review", {**ids, **operation, "run_id": "prior",
          "attempt_source_manifest": save(historical, "manifest")})
    synth = event("fanout.synth.completed", "synth", {**ids, **operation, "reviewed_plan_candidate_digest": contract["sha256"],
                  "plan_synth_contract_ref": contract["ref"], "plan_synth_contract_digest": contract["sha256"]})
    if fault != "missing_admission":
        event("workflow.call.result.admitted", "admitted", {**operation, "admission_status": "admitted",
              "source_event_id": synth.id, "semantic_verdict": "passed"})
    ready = event("task_map.ready", "ready", ids, synth.id)
    failure = event("prd.plan.failed", "failure", {"trigger_event_id": ready.id}, ready.id)
    escalation = event("human.escalate", "ask", {"source_event_id": failure.id, "failure_class": "stage_replan_cap_exhausted"})
    feedback = ["保留藏品编号", "核对借展馆名称"]
    request = {"workflow_run_id": run, "escalation_event_id": escalation.id, "approval_ref": "catalog-owner",
               "operator_feedback": feedback}
    owner = event("web.action.requested", "owner", {"action": "stage-replan-new-generation", "request": request}, actor="web")
    gen = {"stage_replan_generation": escalation_generation_id(run, escalation.id),
           "stage_replan_generation_source_event_id": escalation.id}
    goal = event("run.goal.updated", "goal", {**gen, "reason": "authorized_stage_replan_new_generation"}, owner.id)
    scope = {"operator_feedback": feedback if fault != "different_owner" else ["替换藏品编号"],
             "rework_feedback": [{"summary": "藏品来源待核"}], "previous_plan_candidate_refs": deepcopy(history),
             "rework_attempt": 1, "rework_of": ready.id}
    if fault == "signed_descriptor":
        scope["previous_plan_candidate_refs"][1]["source_event_id"] = "unrelated"
    trigger = event("catalog.input.ready", "trigger", {**scope, **gen, "stage_id": stage,
                    "operator_authorized": True, "operator_authorization_ref": request["approval_ref"]}, owner.id)
    event("runtime.action.completed", "applied", {"action": "stage-replan-new-generation", "status": "applied",
          "run_id": run, "stage_trigger_event_id": trigger.id, "source_failure_event_id": failure.id,
          "run_goal_update_event_id": goal.id, "escalation_event_id": escalation.id,
          "stage_replan_generation": gen["stage_replan_generation"]}, trigger.id, actor="web")
    start = event("fanout.started", "fanout", {"trigger_event_id": trigger.id, "stage_id": stage}, trigger.id)
    if fault in {"forged_alias", "false_equivalence", "scope"}:
        from zf.runtime import plan_rework_input_overlap
        original = plan_rework_input_overlap.partition_repeated_candidate_bodies
        def corrupt(state_dir, sources):
            result = original(state_dir, sources)
            if fault == "scope":
                next(s for s in result if s["source_id"] == "previous-plan-candidate-1")["access_scope"] = {"purposes": ["other"]}
                return result
            partition = next(s for s in result if s.get("kind") == "plan_rework_candidate_partition")
            alias = partition["access_scope"]["read_equivalents"][0]
            if fault == "forged_alias":
                alias["content_sha256"] = "c" * 64
            else:
                # Even agreement between the two navigation indexes cannot
                # make a different field into an equivalent evidence donor.
                alias["source_json_path"] = "$.edition"
                context = next(s for s in result if s["source_id"] == "plan-rework-context")
                body = hydrate_sidecar_ref(state_dir, context).payload
                body["read_equivalents"][0]["read_equivalents"][0] = dict(alias)
                context.update(save(body, "plan_rework_context"))
            return result
        monkeypatch.setattr(plan_rework_input_overlap, "partition_repeated_candidate_bodies", corrupt)
    report = {**save({"location": "外借藏品清单"}, "museum-report"), "source_id": "loan-registry",
              "artifact_id": "loans.json"}
    explicit = [{"source_id": report["source_id"], "artifact_id": report["artifact_id"],
                 "artifact_sha256": report["sha256"], "json_path": "$", "min_returned_bytes": 1}]
    def prepare(key, declared):
        payload = {"workflow_run_id": run, "role_instance": "curator", "output_profile_id": "workflow-read",
                   "trigger_payload": deepcopy(scope), "input_refs": [deepcopy(report)],
                   "required_reads": deepcopy(declared), "canonical_success_event": "fanout.child.completed",
                   "canonical_failure_event": "fanout.child.failed"}
        result = prepare_call_operation(runtime, payload=payload, operation_type="fanout_reader_child",
            operation_key=key, stage_id=stage, task_id="", dispatch_id=key, causation_id=start.id)
        return result, payload
    return runtime, prepare, explicit, scope


def context_paths(payload):
    return [r["json_path"] for r in payload["required_reads"] if r["source_id"] == "plan-rework-context"]


def test_museum_owner_feedback_and_charter_survive_new_selection_and_replay(tmp_path):
    runtime, prepare, declared, scope = museum(tmp_path)
    issued, payload = prepare("new", declared)
    assert context_paths(payload) == ["$.schema_version", "$.rework_of", "$.rework_attempt",
                                      "$.rework_feedback", "$.operator_feedback", "$.read_equivalents"]
    assert declared[0] in payload["required_reads"]
    manifest = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
    source = next(s for s in manifest["sources"] if s["source_id"] == "plan-rework-context")
    read = read_attempt_artifact(runtime.state_dir, manifest=manifest, source_id=source["source_id"],
        artifact_id=source["artifact_id"], artifact_sha256=source["sha256"], json_path="$", record=False)
    body = json.loads(read["content"])
    assert body["previous_plan_candidate_refs"] == scope["previous_plan_candidate_refs"]
    assert body["operator_feedback"] == scope["operator_feedback"]
    assert body["read_equivalents"][0]["read_equivalents"][0]["source_json_path"] == "$.objects"
    for retry in (declared, payload["required_reads"]):
        replay, repeated = prepare("new", retry)
        assert replay.request_hash == issued.request_hash
        assert repeated["required_reads"] == payload["required_reads"]
    force = [{"source_id": source["source_id"], "artifact_id": source["artifact_id"],
              "artifact_sha256": source["sha256"], "json_path": "$", "min_returned_bytes": 1}]
    assert context_paths(prepare("owner-explicit", [*declared, *force])[1]) == ["$"]


@pytest.mark.parametrize("fault", ["different_owner", "signed_descriptor", "missing_admission", "forged_alias", "false_equivalence", "scope"])
def test_unproven_history_and_partition_preserve_root(tmp_path, fault, monkeypatch):
    _, prepare, declared, _ = museum(tmp_path, fault, monkeypatch)
    assert context_paths(prepare("fallback", declared)[1]) == ["$"]


def test_preupgrade_owner_request_with_charter_keeps_signed_root(tmp_path, monkeypatch):
    from zf.runtime import plan_rework_input_overlap

    _, prepare, charter, _ = museum(tmp_path)
    with monkeypatch.context() as old:
        old.setattr(plan_rework_input_overlap, "rework_context_paths", lambda *args, **kwargs: ())
        issued, policy = prepare("old", charter)
    assert context_paths(policy) == ["$"]
    for replay_input in (charter, policy["required_reads"]):
        replay, observed = prepare("old", replay_input)
        assert replay.request_hash == issued.request_hash
        assert observed["required_reads"] == policy["required_reads"]
