from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_pending_handoff_reconciliation import _make_orchestrator
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.goal_terminal_reconciliation import reconcile_goal_terminal_task_settlement
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package, package_event_payload, write_plan_artifact_package,
)
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot


def _fixture(tmp_path, *, mutation="", restart=False):
    orch, store, log = _make_orchestrator(tmp_path)
    state = orch.state_dir
    contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    contract["contract_digest"] = stable_json_sha256(contract)
    contract_ref = write_run_contract_snapshot(state, contract)
    original = {"task_id": "ORIGINAL", "title": "Preserve original behavior",
                "goal_claim_ids": ["C1"], "allowed_paths": ["api.py"]}

    def package(generation, rows, prior=None):
        task_map = {"schema_version": "task-map.v1", "tasks": rows}
        if prior:
            task_map["amend"] = {"schema_version": "task-map-amend.v1",
                                 "supersedes_task_map_ref": old_map["ref"],
                                 "gap_task_ids": ["GAP"],
                                 "superseded_task_ids": ["ORIGINAL"] if mutation == "superseded_task" else []}
        desc = write_immutable_json_sidecar(state, task_map, root="fixtures/maps",
                                           kind="task_map", schema_version="task-map.v1", created_by="test")
        body = build_plan_artifact_package(
            workflow_run_id="run-1", flow_kind="prd", producer_stage_id="prd-plan",
            run_contract=contract_ref, plan_revision=generation, task_map_generation=generation,
            produced=[{"logical_name": "task_map", "ref": desc["ref"], "sha256": desc["sha256"]}],
            required_ports=["task_map"], supersedes=prior,
        )
        ref = write_plan_artifact_package(state, body)
        log.append(ZfEvent(type="plan.artifact_package.admitted", correlation_id="run-1",
                           payload=package_event_payload(body, ref, status="admitted")))
        return desc, ref

    old_map, old_package = package("g1", [original])
    new_original = deepcopy(original)
    if mutation == "changed_row":
        new_original["title"] = "Different work"
    current_map, current_package = package("g2", [new_original, {"task_id": "GAP"}], old_package)
    store.add(Task(id="ORIGINAL", title=original["title"], status="in_progress", contract=TaskContract(
        feature_id="ROOT", goal_claim_ids=[] if mutation == "missing_claims" else ["C1"], evidence_contract={
            "workflow_run_id": "other-run" if mutation == "different_run" else "run-1",
            "source_refs": {"task_map_ref": old_map["ref"], "task_map_generation": "g3" if mutation == "canonical_generation" else "g1",
                            "plan_artifact_package_ref": old_package["ref"],
                            "plan_artifact_package_digest": old_package["sha256"]},
        },
    )))
    terminal = ZfEvent(type="run.goal.completed", correlation_id="run-1", payload={
        "run_id": "run-1", "workflow_run_id": "run-1", "pdd_id": "ROOT", "feature_id": "ROOT",
        "task_map_ref": current_map["ref"], "task_map_generation": "g2",
        "completed_task_ids": ["GAP"], "goal_coverage": [{"goal_claim_id": "C1",
            "status": "open" if mutation == "open_claim" else "closed"}],
    })
    log.append(terminal)
    if mutation == "later_generation":
        package("g3", [new_original, {"task_id": "GAP"}], current_package)
    if mutation == "later_blocker":
        log.append(ZfEvent(type="run.goal.blocked", payload={"run_id": "run-1", "reason": "later terminal verdict"}))
    if mutation == "tampered_map":
        (state / current_map["ref"]).write_text('{"tasks": []}')
    if restart:
        reconcile_goal_terminal_task_settlement(orch)
    else:
        orch._settle_candidate_tasks_done(terminal)
    return orch, store, log, terminal


@pytest.mark.parametrize("restart", [False, True])
def test_goal_terminal_settles_unchanged_inherited_task_without_redispatch(tmp_path, restart):
    orch, store, log, terminal = _fixture(tmp_path, restart=restart)
    assert store.get("ORIGINAL").status == "done"
    before = len(log.read_all())
    orch._settle_candidate_tasks_done(terminal)
    assert len(log.read_all()) == before
    assert not any(e.type == "task.dispatched" for e in log.read_all())


@pytest.mark.parametrize("mutation", ["changed_row", "different_run", "open_claim",
                                     "later_generation", "tampered_map", "canonical_generation",
                                     "missing_claims", "superseded_task", "later_blocker"])
def test_goal_terminal_keeps_unproven_or_superseded_inherited_task_active(tmp_path, mutation):
    _, store, _, _ = _fixture(tmp_path, mutation=mutation, restart=True)
    assert store.get("ORIGINAL").status == "in_progress"
