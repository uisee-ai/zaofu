"""Production profile -> Plan admission -> canonical Task -> required reads."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tests.test_product_acceptance import _spec
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.artifact_query.handoff import CanonicalHandoffResolver, build_handoff_authority_contract
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.artifact_read_requirements import build_canonical_required_reads
from zf.runtime.plan_artifact_package import admit_plan_artifact_package_for_payload, PlanArtifactPackageError
from zf.runtime.run_contract import stable_json_sha256, write_run_contract
from zf.runtime.task_contract_snapshot import (
    build_task_contract_snapshot, current_task_contract_identity, write_task_contract_snapshot,
)


def prepare(root, extra_tasks, *, selection=True):
    state = root / "state"
    artifacts = state / "artifacts"
    artifacts.mkdir(parents=True)
    config = load_config(Path(__file__).parents[1] / "zf-prd-v5.yaml")
    metadata = config.workflow.flow_metadata
    policy = {"schema_version": "task-plan-input-selection.v1",
              "implementation": ["task_execution_input"], "task-verify": ["task_execution_input"]}
    ids = ["T1", *[f"OTHER-{i}" for i in range(extra_tasks)]]
    task_map = {"schema_version": "task-map.v1", "objective": "Deliver bounded commands",
        "required_plan_ports": metadata["artifact_package"]["required_ports"],
        "tasks": [{"task_id": task_id, "acceptance_criteria": [f"AC-{task_id}"],
                   "evidence_contract": {"plan_input_selection": policy} if selection else {}} for task_id in ids]}
    (artifacts / "map.json").write_text(json.dumps(task_map))
    (artifacts / "requirement.md").write_text("Deliver bounded commands.")
    body = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd", "metadata": metadata}}
    write_run_contract(state, {**body, "contract_digest": stable_json_sha256(body)})
    scoped = {"schema_version": "task-scoped-plan-input.v1", "shared": {"protocol": "v1"},
              "tasks": [{"task_id": task_id, "body": {"assertion": f"AC-{task_id}",
                         "behavior": "bounded command" if task_id == "T1" else "unrelated" * 100}}
                        for task_id in ids]}
    pas = _spec()
    for key in ("workflow_run_id", "task_map_generation", "plan_revision"):
        pas.pop(key)
    pas["assembly_owner"] = pas["entrypoints"][0]["owner"] = "T1"
    ports = [{"logical_name": name, "body": deepcopy(scoped)}
             for name in ("execution_spec", "task_execution_input")]
    ports += [{"logical_name": name, "body": {"schema_version": "matrix.v1", "status": "ready",
               "rows": [{"task_id": task_id, "acceptance_id": f"AC-{task_id}"} for task_id in ids]}}
              for name in ("acceptance_matrix", "test_matrix", "real_e2e_matrix")]
    ports.append({"logical_name": "product_acceptance_spec", "body": pas})
    writer = EventWriter(EventLog(state / "events.jsonl"))
    binding = admit_plan_artifact_package_for_payload(
        state_dir=state, project_root=root, events=[], event_writer=writer,
        payload={"task_map_ref": "artifacts/map.json", "prd_ref": "artifacts/requirement.md",
                 "task_map_generation": "g1", "plan_ports": ports},
        workflow_run_id="run", flow_kind="prd", producer_stage_id="prd-plan", goal_id="G", metadata=metadata)
    evidence = {**task_map["tasks"][0]["evidence_contract"],
                "required_plan_ports": task_map["required_plan_ports"], "source_refs": binding}
    task = Task(id="T1", status="in_progress", contract=TaskContract(evidence_contract=evidence))
    store = TaskStore(state / "kanban.json")
    store.add(task)
    task = store.get("T1")
    snapshot = build_task_contract_snapshot(task, workflow_run_id="run", task_map_generation_id="g1",
                                           base_commit="base", task_ref="refs/zf/tasks/T1")
    ref = write_task_contract_snapshot(state, snapshot)
    resolver = CanonicalHandoffResolver(state_dir=state, project_root=root, config=config)
    manifest, _ = resolver.resolve_payload(
        payload={**binding, **current_task_contract_identity(task), "workflow_run_id": "run",
                 "task_ref": "refs/zf/tasks/T1", "base_commit": "base", "output_profile_id": "implementation",
                 "handoff_authority_contract": build_handoff_authority_contract({},
                     output_profile_id="implementation", stage_id="impl", operation_type="writer"),
                 "contract_snapshot_ref": ref["ref"], "contract_snapshot_digest": ref["sha256"]},
        workflow_run_id="run", task_id="T1", attempt_id="a1", dispatch_id="d1")
    return state, resolver, binding, manifest


def test_new_production_plan_cannot_omit_task_selection(tmp_path):
    with pytest.raises(PlanArtifactPackageError, match="plan_input_selection is required"):
        prepare(tmp_path, 0, selection=False)


def test_unrelated_plan_growth_does_not_amplify_required_task_body(tmp_path):
    consumed = []
    for extra in (0, 64):
        state, resolver, binding, manifest = prepare(tmp_path / str(extra), extra)
        reads = [r for r in build_canonical_required_reads(manifest, output_profile_id="implementation")
                 if r["source_id"].startswith("plan-port-")]
        assert [(r["source_id"], r["json_path"]) for r in reads] == [
            ("plan-port-task_execution_input", "$.shared"), ("plan-port-task_execution_input", "$.tasks[0]")]
        consumed.append([read_attempt_artifact(state, manifest=manifest,
            source_id=r["source_id"], artifact_id=r["artifact_id"], json_path=r["json_path"])["content"] for r in reads])
        candidate = resolver._current_plan_port_sources(workflow_run_id="run", task_id="T1",
            currentness=binding, candidate=True, profile="candidate-verify")
        assert {"acceptance_matrix", "test_matrix", "real_e2e_matrix"} <= {s["artifact_id"] for s in candidate}
        assert all(s["allowed_paths"] == ["$"] for s in candidate)
    assert consumed[0] == consumed[1]
