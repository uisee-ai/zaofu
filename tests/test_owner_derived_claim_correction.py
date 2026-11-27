from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from tests.test_paused_owner_plan_redrive import HEADERS, URL, plan
from zf.core.events import EventWriter
from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar
from zf.runtime.goal_claim_set import hydrate_pinned_goal_claim_set
from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError, admit_plan_artifact_package_for_payload, hydrate_plan_artifact_package,
)
from zf.runtime.plan_claim_continuity import candidate_claim_errors
from zf.runtime.plan_derived_claim_correction import KEY, correction_sources
from zf.runtime.run_contract import stable_json_sha256, write_run_contract
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_text
from zf.runtime.workflow_operation import operation_request_hash


@pytest.fixture
def checkpoint(plan):
    api, log, emit, original, redrive = plan
    state, run = log.path.parent, redrive["workflow_run_id"]
    writer = EventWriter(log, default_origin="kernel")
    generation = original.payload["stage_replan_generation"]
    def pin(body, root):
        return write_immutable_json_sidecar(state, body, kind="fixture", schema_version="fixture.v1",
                                            created_by="test", root=root)
    contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    write_run_contract(state, {**contract, "contract_digest": stable_json_sha256(contract)})
    evidence = write_sidecar_text(state, "artifacts/requirements/inventory.md", "Use gross mass for the manifest.",
                                  kind="requirement_authority", schema_version="markdown", created_by="test")
    objective = pin({"acceptance": [{"id": "AC-REQUIREMENT", "text": "Report keeps its approved title."}]}, "objective")
    task_map = {"schema_version": "task-map.v1", "tasks": [{"task_id": "carton", "acceptance_criteria": [
        {"id": "AC-MASS", "text": "Use net mass."}, {"id": "AC-CURRENCY", "text": "Keep currency."}]}]}
    def admit(body, revision):
        descriptor = pin(body, "taskmaps")
        return admit_plan_artifact_package_for_payload(state_dir=state, project_root=state.parent, event_writer=writer,
            events=log.read_all(), workflow_run_id=run, goal_id=run, producer_stage_id="review-plan",
            flow_kind="prd", metadata={"artifact_package": {"mode": "blocking"}}, source_event_id=original.id,
            payload={"task_map_ref": descriptor["ref"], "task_map_generation": revision,
                     "requirement_spec_ref": objective["ref"], "stage_replan_generation": generation})
    first = admit(task_map, "revision-one")
    scope = {**first,"workflow_run_id":run, "stage_replan_generation":generation, "requirement_spec_ref": objective["ref"]}
    package = hydrate_plan_artifact_package(state, {"ref": first["plan_artifact_package_ref"],
        "sha256": first["plan_artifact_package_digest"]})
    attempt = "inventory-author"
    manifest = {"attempt_id": attempt, "sources": [{**evidence, "allowed_paths": ["$"]}]}
    hashed = canonical_json_sha256(manifest)
    pin(manifest, f"attempts/{attempt}/source-manifests")
    envelope = {"workflow_run_id": run, "operation_id": "inventory-operation", "active_attempt_id": attempt,
                "attempt_domain": "plan", "request": {"source_manifest_digest": hashed,
                "plan_candidate_validation": {"manifest": {"trigger_payload": scope}}}}
    operation = emit("workflow.operation.requested", {"request_hash": operation_request_hash(envelope),
                     "request_ref": pin(envelope, "requests"), "operation_id": envelope["operation_id"],
                     "active_attempt_id": attempt})
    patch = {"package_digest": first["plan_artifact_package_digest"],
             "claim_set_sha256":first["goal_claim_set_digest"], "goal_claim_id":"AC-MASS",
             "before":"Use net mass.", "after":"Use gross mass.", "operation_request_event_id":operation.id,
             "requirement_sha256":evidence["sha256"], "quote":"gross mass"}
    body = {"workflow_run_id":run, "stage_replan_generation":generation, "escalation_event_id":redrive["escalation_event_id"],
            "approval_ref":"owner:audited-carton-rule", KEY:patch}
    return api, log, emit, body, scope, package, task_map, admit


def test_exact_owner_change_reaches_candidate_package_and_goal(checkpoint):
    api, log, _, body, scope, package, task_map, admit = checkpoint
    state = log.path.parent
    corrected = deepcopy(task_map)
    criterion = corrected["tasks"][0]["acceptance_criteria"][0]
    criterion["text"] = body[KEY]["after"]
    def errors(draft):
        return [e["code"] for e in candidate_claim_errors(state, state.parent, package, draft, scope)]
    assert errors(corrected) == ["mandatory_claim_rewritten"]
    assert api.post(URL, headers=HEADERS, json=body).status_code == 200
    assert not errors(corrected)
    assert errors(task_map) == ["mandatory_claim_rewritten"]
    with pytest.raises(PlanArtifactPackageError, match="AC-MASS"):
        admit(task_map, "revision-two")
    criterion["mandatory"] = False
    assert errors(corrected) == ["mandatory_claim_origin_changed"]
    criterion.pop("mandatory")
    corrected["tasks"][0]["acceptance_criteria"][1]["text"] = "Drop currency."
    assert errors(corrected) == ["mandatory_claim_rewritten"]
    corrected["tasks"][0]["acceptance_criteria"][1]["text"] = "Keep currency."
    second = admit(corrected, "revision-two")
    pinned = hydrate_pinned_goal_claim_set(state_dir=state, events=log.read_all(), workflow_run_id=scope["workflow_run_id"],
                                          goal_id=scope["workflow_run_id"], task_map_generation="revision-two")
    assert pinned["claim_set_digest"]
    rows = {c["goal_claim_id"]: c for c in pinned["claims"]}
    assert rows["AC-MASS"] == {"goal_claim_id":"AC-MASS","text":"Use gross mass.","mandatory":True,"source_ref":"task-map:carton"}
    assert rows["AC-CURRENCY"]["text"] == "Keep currency."
    assert rows["AC-REQUIREMENT"]["text"] == "Report keeps its approved title."
    assert scope["plan_artifact_package_digest"] != second["plan_artifact_package_digest"]
    current = hydrate_plan_artifact_package(state, {"ref":second["plan_artifact_package_ref"],"sha256":second["plan_artifact_package_digest"]})
    ports = {p["logical_name"]:p for p in current["produced"]}
    assert hydrate_sidecar_ref(state, ports["task_map"]).payload == corrected
    assert hydrate_sidecar_ref(state, ports["goal_claim_set"]).payload == pinned


def test_one_owner_decision_under_concurrency_and_signed_operations_are_immutable(checkpoint):
    api, log, _, request, scope, _, _, _ = checkpoint
    def post(after):
        body = {**request, KEY:{**request[KEY], "after":after}}
        return api.post(URL, headers=HEADERS, json=body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(post, ["Use gross mass.", "Use tare mass."]))
    assert sorted(r.status_code for r in outcomes) == [200, 409]
    receipts = [e for e in log.read_all() if e.type == "runtime.action.completed" and KEY in e.payload]
    assert len(receipts) == 1 and receipts[0].id in next(r.text for r in outcomes if r.status_code == 409)
    state = log.path.parent
    probe = lambda op: correction_sources(state, scope, operation_id=op, attempt_id="", read_events=log.read_all)
    assert probe("inventory-operation") == []
    [source] = probe("a-new-reader")
    row = hydrate_sidecar_ref(state, source).payload["corrections"][0]
    assert row["receipt_event_id"] == receipts[0].id and row["owner_request_event_id"] == receipts[0].causation_id
    assert row["after"] == receipts[0].payload[KEY]["after"] and row["requirement_sha256"] == request[KEY]["requirement_sha256"]
    assert source["source_id"] == "critical-owner-claim-corrections" and source["allowed_paths"] == ["$"]
    assert receipts[0].id in post(row["after"]).text


@pytest.mark.parametrize("invalid", ["unauthorized","claim-set","package","goal","old","source","quote","operation","generation","approved","no-op"])
def test_public_owner_correction_rejects_invalid_authority_without_changing_the_baseline(checkpoint, invalid):
    api, log, emit, body, _, _, _, _ = checkpoint
    body = deepcopy(body)
    change = body[KEY]
    if invalid in {"claim-set","package","source","operation","quote","old","no-op"}:
        field = {"claim-set":"claim_set_sha256","package":"package_digest","source":"requirement_sha256",
                 "operation":"operation_request_event_id","quote":"quote","old":"before","no-op":"after"}[invalid]
        change[field] = change["before"] if invalid=="no-op" else "wrong"
    elif invalid == "goal":
        change.update(goal_claim_id="AC-REQUIREMENT", before="Report keeps its approved title.")
    elif invalid == "generation":
        body["stage_replan_generation"] = "other-generation"
    elif invalid == "approved":
        emit("plan.approved", {"plan_id":"approved-business-plan"}, actor="operator")
    res = api.post(URL, headers={} if invalid=="unauthorized" else HEADERS, json=body)
    assert res.status_code in {401,403,409}, res.text
    assert not any(e.type=="runtime.action.completed" and KEY in e.payload for e in log.read_all())
