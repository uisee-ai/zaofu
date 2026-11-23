"""A frozen baseline audit may explicitly assign separate obligations to final Candidate."""
import copy
from types import SimpleNamespace

import pytest

from zf.core.task.schema import Task, TaskContract
from zf.runtime.candidate_verification_authority import _aggregate_contract_snapshot
from zf.runtime.plan_execution_review import review_execution
from zf.runtime.task_map import validate_task_map_payload
from zf.runtime.task_pipeline_briefing import verification_result_template
from zf.runtime.task_pipeline_targets import prepare_contract_snapshot
from zf.runtime.verification_result import VerificationResultError, validate_verification_acceptance
from zf.runtime.verification_commands import command_digest


def audit():
    names = ("MODULE", "PRODUCT")
    return {
        "task_id": "AUDIT", "title": "frozen module", "allowed_paths": [],
        "runtime_only": True, "write_free": True, "immutable_baseline": "git:" + "a" * 40,
        "evidence_contract": {"frozen_candidate_acceptance_ids": ["PRODUCT"]},
        "acceptance_criteria": [
            {"id": name, "statement": name, "verification_owner": "candidate_verify",
             "verification_tier": "runtime", "verification_command_ids": [name]}
            for name in names],
        "validation": {"commands": [
            {"id": name, "command": "check " + name, "owner": "candidate_verify",
             "tier": "runtime", "acceptance_ids": [name]} for name in names]},
    }


@pytest.mark.parametrize("with_reader", [True, False])
def test_frozen_audit_and_final_candidate_use_their_own_targets(tmp_path, with_reader, monkeypatch):
    row = audit()
    report = review_execution({"tasks": [row]}, ports={}, policy={},
                              metadata={"task_pipeline": {"mode": "blocking"}})
    initial = report["verification"]["task_requirements"][0]["initial_readonly_verify"]
    assert initial["mandatory_acceptance_ids"] == initial["command_ids"] == ["MODULE"]
    assert initial["verification_owner"] == "candidate_verify"
    task = Task(id="AUDIT", title="frozen module", contract=TaskContract(
        behavior="verify the module then the integrated product", scope=[],
        verification="check MODULE", verification_tiers=["runtime"],
        acceptance_criteria=row["acceptance_criteria"], validation=row["validation"],
        evidence_contract={**row["evidence_contract"], "task_pipeline_entry_mode": "verify_only",
                           "runtime_only": True, "write_free": True},
    ))
    stage = SimpleNamespace(trigger="candidate.ready", topology="fanout_reader", roles=["reader"],
                            aggregate=SimpleNamespace(child_success_event="verify.child.completed",
                                                      success_event="test.passed"))
    config = SimpleNamespace(runtime=SimpleNamespace(git=SimpleNamespace(task_ref_prefix="task")),
                             roles=[SimpleNamespace(instance_id="reader", role_kind="reader")],
                             workflow=SimpleNamespace(stages=[stage] if with_reader else []))
    runtime = SimpleNamespace(state_dir=tmp_path, config=config)
    def prepare():
        return prepare_contract_snapshot(
            runtime, task=task, workspace=SimpleNamespace(base_commit="a" * 40),
            generation_context={"workflow_run_id": "run", "task_map_generation": "gen",
                                "generation_admitted_event_id": "evt-plan"})
    if not with_reader:
        with pytest.raises(ValueError, match="downstream Candidate Verify"):
            prepare()
        return
    snapshot, _ = prepare()
    local = verification_result_template(snapshot)
    assert [probe["command_id"] for probe in local["probe_receipts"]] == ["MODULE"]
    validate_verification_acceptance(local, snapshot)
    product = next(item for item in local["requirement_results"] if item["acceptance_id"] == "PRODUCT")
    assert product["status"] == "not_applicable"
    for premature in ("passed", "waived"):
        product["status"] = premature
        with pytest.raises(VerificationResultError, match="belongs to later verification"):
            validate_verification_acceptance(local, snapshot)
    sibling = copy.deepcopy(snapshot)
    sibling["task_id"] = "SECOND-AUDIT"
    for command in sibling["verification_commands"]:
        command["command"] += " sibling"
        command["command_digest"] = command_digest(command["command"])
    aggregate = _aggregate_contract_snapshot(
        [snapshot, sibling], workflow_run_id="run", task_id="goal", contract_revision="rev",
        task_map_generation="gen", base_commit="b"*40, task_ref="candidate/goal",
        candidate_event_id="evt-freeze", freeze_descriptor={}, child_authorities=[],
        package_identity={})
    final = verification_result_template(aggregate)
    assert {probe["command"] for probe in final["probe_receipts"]} == {
        "check MODULE", "check PRODUCT", "check MODULE sibling", "check PRODUCT sibling"}
    assert {ac["source_acceptance_id"] for ac in aggregate["acceptance_criteria"]
            if ac["mandatory"]} == {"MODULE", "PRODUCT"}
    assert {(item["source_task_id"], item["source_acceptance_id"])
            for item in aggregate["acceptance_criteria"]} == {
                (task_id, ac) for task_id in ("AUDIT", "SECOND-AUDIT") for ac in ("MODULE", "PRODUCT")}
    validate_verification_acceptance(final, aggregate)
    from zf.runtime import task_host_manifest
    from zf.runtime.typed_result_admission import _passed_verification_command_issue

    request = {"target_commit": "head", "result_identity": {"contract_snapshot_digest": "digest"}}
    op = {"task_pipeline_stage": "verify", "task_id": "AUDIT"}
    monkeypatch.setattr(task_host_manifest, "binding", lambda service, operation: (op, request, snapshot, tmp_path))
    monkeypatch.setattr(task_host_manifest, "git", lambda root, *args: "" if args[0] in {"diff", "status"} else "head")
    monkeypatch.setattr(task_host_manifest, "source_files", lambda root: [])
    monkeypatch.setattr(task_host_manifest, "dependency_digest", lambda service, root: "deps")
    with pytest.raises(ValueError, match="different stage"):
        task_host_manifest.preview(runtime, operation_id="local", kind="verify", command_id="PRODUCT")
    monkeypatch.setattr(task_host_manifest, "binding", lambda service, operation: (op, request, aggregate, tmp_path))
    final_probe = next(cmd for cmd in aggregate["verification_commands"] if cmd["command"] == "check PRODUCT")
    manifest = task_host_manifest.preview(runtime, operation_id="final", kind="verify",
                                          command_id=final_probe["command_id"])
    assert manifest["command"]["command"] == "check PRODUCT"
    covered = [cmd for cmd in aggregate["verification_commands"] if cmd != final_probe]
    missing = _passed_verification_command_issue(
        {**final, "probe_receipts": []}, contract=aggregate, target={"target_commit": "head"},
        reused_receipts=[], host_commands=covered, verification_owner="candidate_verify")
    assert missing and final_probe["command_id"] in missing["message"]
    final["requirement_results"].pop()
    with pytest.raises(VerificationResultError, match="misses mandatory"):
        validate_verification_acceptance(final, aggregate)



@pytest.mark.parametrize("ids", [["MISSING"], ["MODULE","PRODUCT"], ["PRODUCT","PRODUCT"]])
def test_invalid_final_scope_prevents_approval(ids):
    row = audit()
    row["evidence_contract"]["frozen_candidate_acceptance_ids"] = ids
    assert any("frozen_candidate_acceptance_ids" in e for e in validate_task_map_payload({"tasks":[row]}).errors)


def test_final_scope_cannot_defer_local_owner_or_an_unbound_check():
    row = audit()
    row["validation"]["commands"][1]["owner"] = "task_verify"
    assert any("frozen_candidate_acceptance_ids" in e for e in validate_task_map_payload({"tasks":[row]}).errors)


def test_shared_probe_stays_on_baseline_and_pure_candidate_probe_stays_deferred():
    row = audit()
    row["acceptance_criteria"][1]["verification_command_ids"].append("MODULE")
    row["validation"]["commands"][0]["acceptance_ids"].append("PRODUCT")
    initial = review_execution({"tasks": [row]}, ports={}, policy={},
                               metadata={"task_pipeline": {"mode": "blocking"}})
    scope = initial["verification"]["task_requirements"][0]["initial_readonly_verify"]
    assert scope["command_ids"] == ["MODULE"]
    assert scope["frozen_candidate_acceptance_ids"] == ["PRODUCT"]


def test_local_criterion_cannot_only_reference_another_tasks_probe():
    row = audit()
    row["validation"]["commands"][0]["producer_task_id"] = "SIBLING"
    issues = validate_task_map_payload({"tasks": [row]}).errors
    assert any("no independently executable" in issue for issue in issues)

def test_task_verify_probe_cannot_silently_switch_off_the_only_baseline_ac():
    row = audit()
    row["validation"]["commands"][0]["owner"] = "task_verify"
    issues = validate_task_map_payload({"tasks": [row]}).errors
    assert any("no mandatory criterion for the initial verification route" in issue for issue in issues)


@pytest.mark.parametrize("reader", [True, False])
def test_preflight_checks_deferred_criteria_against_the_pinned_workflow(tmp_path, reader):
    from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight

    report = {"task_map": {"tasks": [audit()]}, "plan_md": "# Plan"}
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path, project_root=tmp_path, reports=[{"report": report}],
        manifest={"trigger_payload": {"flow_kind": "issue"}}, metadata={"flow_kind": "issue"},
        writer_policy={"frozen_candidate_reader": reader},
    )
    issues = [e for e in result["errors"] if e["code"] == "frozen_candidate_consumer_missing"]
    assert bool(issues) is (not reader)
