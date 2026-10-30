from pathlib import Path
import json

import pytest

from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_query.handoff import CanonicalHandoffResolver
from zf.runtime.artifact_read_ledger import ArtifactReadError, read_attempt_artifact
from zf.runtime.artifact_read_requirements import build_canonical_required_reads
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import build_plan_artifact_package, package_event_payload, write_plan_artifact_package
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot
from zf.runtime.task_plan_consumption import selected_plan_ports
from zf.runtime.task_map_required_ports import plan_input_declaration_errors
from zf.runtime.task_contract_snapshot import (
    build_task_contract_snapshot, current_task_contract_identity, write_task_contract_snapshot,
)


@pytest.fixture
def case(tmp_path: Path):
    state = tmp_path / ".zf"
    state.mkdir()
    policy = {"schema_version": "task-plan-input-selection.v1",
              "implementation": ["task_execution_input"], "task-verify": ["task_execution_input"]}
    bodies = {
        "task_map": {"tasks": [{"task_id": "T1", "evidence_contract": {"plan_input_selection": policy}}]},
        "planning_result": {"tasks": [{"task_id": "T1"}]},
        "acceptance_matrix": {"acceptance": [{"id": "AC1"}]},
        "test_matrix": {"tests": [{"id": "TEST1"}]},
        "real_e2e_matrix": {"rows": [{"id": "E2E1"}]},
        "execution_spec": {"schema_version": "task-scoped-plan-input.v1", "shared": {"rule": "R"},
                           "tasks": [{"task_id": "T1", "body": {"acceptance": "AC1"}},
                                     {"task_id": "T2", "body": {"acceptance": "AC2"}}]},
        "task_execution_input": {"schema_version": "task-scoped-plan-input.v1", "shared": {"rule": "R"},
                                 "tasks": [{"task_id": "T1", "body": {"acceptance": "AC1"}},
                                           {"task_id": "T2", "body": {"acceptance": "AC2"}}]},
    }
    ports = []
    for name, body in bodies.items():
        ref = write_immutable_json_sidecar(state, body, root="fixtures", kind=name,
                                           schema_version="v1", created_by="test")
        ports.append({"logical_name": name, "artifact_kind": name, "schema_version": "v1",
                      "producer_stage_id": "plan", "ref": ref["ref"], "sha256": ref["sha256"]})
    body = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    contract = write_run_contract_snapshot(state, {**body, "contract_digest": stable_json_sha256(body)})
    package = build_plan_artifact_package(workflow_run_id="run", flow_kind="prd", producer_stage_id="plan",
                                          run_contract=contract, plan_revision="r1", task_map_generation="g1",
                                          produced=ports, required_ports=list(bodies))
    descriptor = write_plan_artifact_package(state, package)
    EventLog(state / "events.jsonl").append(ZfEvent(
        type="plan.artifact_package.admitted", correlation_id="run",
        payload=package_event_payload(package, descriptor, status="admitted"),
    ))
    task = Task(id="T1", status="in_progress", contract=TaskContract(evidence_contract={
        "plan_input_selection": policy, "required_plan_ports": list(bodies),
    }))
    TaskStore(state / "kanban.json").add(task)
    resolver = CanonicalHandoffResolver(state_dir=state, project_root=tmp_path, config=None)
    identity = {"task_map_generation": "g1", "plan_artifact_package_ref": descriptor["ref"],
                "plan_artifact_package_digest": descriptor["sha256"],
                "plan_artifact_package_id": descriptor["package_id"]}
    return state, resolver, identity, policy


def test_task_consumers_select_pinned_scope_and_candidate_keeps_full_package(case):
    _, resolver, identity, _ = case
    for profile in ("implementation", "task-verify"):
        sources = resolver._current_plan_port_sources(workflow_run_id="run", task_id="T1",
                                                       currentness=identity, profile=profile)
        assert [s["source_id"] for s in sources] == ["plan-port-task_execution_input"]
        assert sources[0]["allowed_paths"] == ["$.shared", "$.tasks[0]"]
    candidate = resolver._current_plan_port_sources(workflow_run_id="run", task_id="T1",
                                                     currentness=identity, candidate=True, profile="candidate-verify")
    assert {s["artifact_id"] for s in candidate} == {"task_map", "planning_result", "acceptance_matrix", "test_matrix", "real_e2e_matrix", "execution_spec", "task_execution_input"}
    assert all(s["allowed_paths"] == ["$"] for s in candidate)


def test_unapproved_contract_selection_is_rejected(case):
    state, resolver, identity, policy = case
    store = TaskStore(state / "kanban.json")
    task = store.get("T1")
    task.contract.evidence_contract["plan_input_selection"] = {**policy, "implementation": ["acceptance_matrix"]}
    store.update("T1", contract=task.contract)
    with pytest.raises(ArtifactReadError, match="differs from pinned task_map"):
        resolver._current_plan_port_sources(workflow_run_id="run", task_id="T1", currentness=identity,
                                            profile="implementation")


def test_legacy_consumer_keeps_all_ports(case):
    state, resolver, identity, _ = case
    store = TaskStore(state / "kanban.json")
    task = store.get("T1")
    task.contract.evidence_contract.pop("plan_input_selection")
    store.update("T1", contract=task.contract)
    sources = resolver._current_plan_port_sources(workflow_run_id="run", task_id="T1", currentness=identity,
                                                   profile="implementation")
    assert len(sources) == 7


@pytest.mark.parametrize("selection", [[], ["unknown"], ["task_execution_input", "task_execution_input"], ["acceptance_matrix"]])
def test_selection_rejects_empty_unknown_duplicate_and_unscoped_only(selection, monkeypatch, tmp_path):
    policy = {"schema_version": "task-plan-input-selection.v1", "implementation": selection,
              "task-verify": ["task_execution_input"]}
    body = {"tasks": [{"task_id": "T1", "evidence_contract": {"plan_input_selection": policy}}]}
    from types import SimpleNamespace
    monkeypatch.setattr("zf.runtime.task_plan_consumption.hydrate_sidecar_ref", lambda *a: SimpleNamespace(payload=body))
    with pytest.raises(ValueError):
        selected_plan_ports(state_dir=tmp_path, task_id="T1", profile="implementation",
                            evidence={"plan_input_selection": policy},
                            ports={"task_map": {"ref": "map", "sha256": "a"}},
                            required=["task_map", "execution_spec", "task_execution_input", "acceptance_matrix", "test_matrix", "real_e2e_matrix"])


def test_plan_validation_rejects_selection_before_dispatch():
    ports = ["execution_spec", "task_execution_input", "acceptance_matrix", "test_matrix", "real_e2e_matrix"]
    policy = {"schema_version": "task-plan-input-selection.v1", "implementation": ["task_execution_input"],
              "task-verify": ["task_execution_input"]}
    payload = {"required_plan_ports": ports, "tasks": [{"task_id": "T1", "evidence_contract": {"plan_input_selection": policy}}]}
    assert plan_input_declaration_errors(payload) == []
    policy["task-verify"] = ["unbound"]
    assert any("task-verify" in e for e in plan_input_declaration_errors(payload))


def test_resolve_payload_binds_selected_sources_and_read_requirements(case):
    state, resolver, identity, _ = case
    store = TaskStore(state / "kanban.json")
    task = store.get("T1")
    task.contract.evidence_contract["source_refs"] = identity
    store.update("T1", contract=task.contract)
    task = store.get("T1")
    snapshot = build_task_contract_snapshot(task, workflow_run_id="run", task_map_generation_id="g1",
                                            base_commit="base", task_ref="refs/zf/tasks/T1")
    snapshot_ref = write_task_contract_snapshot(state, snapshot)
    manifest, _ = resolver.resolve_payload(
        payload={**current_task_contract_identity(task), **identity, "workflow_run_id": "run",
                 "task_ref": "refs/zf/tasks/T1", "base_commit": "base", "output_profile_id": "implementation",
                 "contract_snapshot_ref": snapshot_ref["ref"], "contract_snapshot_digest": snapshot_ref["sha256"]},
        workflow_run_id="run", task_id="T1", attempt_id="a1", dispatch_id="d1",
    )
    reads = build_canonical_required_reads(manifest, output_profile_id="implementation")
    plan_reads = [(r["source_id"], r["json_path"]) for r in reads if r["source_id"].startswith("plan-port-")]
    assert plan_reads == [("plan-port-task_execution_input", "$.shared"), ("plan-port-task_execution_input", "$.tasks[0]")]
    with pytest.raises(ArtifactReadError, match="not allowed"):
        read_attempt_artifact(state, manifest=manifest, source_id="plan-port-task_execution_input",
                              artifact_id="task_execution_input", json_path="$.tasks[1]")


def test_discovery_handoff_reads_whole_plan_for_parent_without_partition(case):
    state, resolver, identity, _ = case
    manifest, _ = resolver.resolve_payload(
        payload={**identity, "workflow_run_id": "run", "output_profile_id": "workflow-read",
                 "canonical_success_event": "flow.discovery.child.completed"},
        workflow_run_id="run", task_id="PARENT", attempt_id="discovery", dispatch_id="d",
    )
    source = next(s for s in manifest["sources"] if s["source_id"] == "plan-port-execution_spec")
    assert source["allowed_paths"] == ["$"]
    result = read_attempt_artifact(state, manifest=manifest, source_id=source["source_id"],
                                  artifact_id=source["artifact_id"], json_path="$")
    assert [r["task_id"] for r in json.loads(result["content"])["tasks"]] == ["T1", "T2"]


@pytest.mark.parametrize("fault", ["digest", "generation", "run"])
def test_discovery_handoff_rejects_wrong_plan_identity(case, fault):
    _, resolver, identity, _ = case
    identity = dict(identity)
    if fault == "digest": identity["plan_artifact_package_digest"] = "0" * 64
    if fault == "generation": identity["task_map_generation"] = "other"
    with pytest.raises(ArtifactReadError):
        resolver.resolve_payload(
            payload={**identity, "workflow_run_id": "run", "output_profile_id": "workflow-read",
                     "canonical_success_event": "flow.discovery.child.completed"},
            workflow_run_id="other" if fault == "run" else "run", task_id="PARENT",
            attempt_id="discovery-invalid", dispatch_id="d",
        )
