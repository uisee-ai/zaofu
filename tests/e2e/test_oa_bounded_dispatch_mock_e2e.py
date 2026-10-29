"""Same admitted DAG and capacity: baseline dispatch vs bounded OA placement."""
from dataclasses import replace
import json

import pytest

from tests import test_orchestrator_agent_run_plan as fixture
from tests.test_controller_rendered_fanout_runtime import _init_repo, _RecordingTransport
from tests.test_task_pipeline_reconciler import _policy
from zf.core.config.schema import RoleLifecycleConfig, WorkflowOrchestrationConfig
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.task_pipeline_projection import read_task_pipeline_projection

pytestmark = pytest.mark.mock_e2e


@pytest.fixture
def plan_ports(monkeypatch):
    original = fixture._port

    def port(state, name, *, revision="1"):
        if name not in {"task_map", "requirement_spec"}:
            return original(state, name, revision=revision)
        body = {"schema_version": name + ".v1"}
        if name == "task_map":
            body["tasks"] = [{"task_id": tid, "title": tid, "owner_role": "dev",
                "blocked_by": ["A"] if tid == "B" else [], "allowed_paths": [tid + ".txt"]}
                for tid in ["A", "B", "C"]]
        else:
            body["requirements"] = [{"id": i, "text": "Preserve authorization and independent verification for each resource."}
                                    for i in range(100)]
        descriptor = write_immutable_json_sidecar(state, body, root="fixtures/" + name,
            kind=name, schema_version=body["schema_version"], created_by="test")
        return {"logical_name": name, "artifact_kind": name, "schema_version": body["schema_version"],
                "producer_stage_id": "prd-plan", "ref": descriptor["ref"], "sha256": descriptor["sha256"]}
    monkeypatch.setattr(fixture, "_port", port)


def _seed(root, bounded):
    root.mkdir()
    head = _init_repo(root)
    seed, trigger, loaded, package, _ = fixture._runtime(root)
    cfg = seed.config
    cfg.roles.append(replace(cfg.roles[-1], instance_id="dev-2"))
    cfg.roles = [replace(role, lifecycle=RoleLifecycleConfig(mode="on_demand"))
                 if role.name == "dev" else role for role in cfg.roles]
    cfg.project.workspace = str(root)
    cfg.project.state_dir = str(seed.state_dir)
    cfg.runtime.workdirs.enabled = True
    cfg.runtime.workdirs.mode = "worktree"
    cfg.runtime.workdirs.root = str(seed.state_dir / "workdirs")
    policy = _policy()
    policy.update(mode="blocking", max_active_task_pipelines=4)
    policy["pools"]["impl"].update(capacity=2, role_instances=["dev-1", "dev-2"])
    policy["backpressure"].update(max_unverified_tasks=4, max_integration_queue=2)
    cfg.workflow.flow_metadata = {"task_pipeline": policy,
        "result_protocol": {"semantic_submit_profiles": {"implementation": "blocking", "task-verify": "blocking"}}}
    cfg.workflow.orchestration.autonomous_actions = ["delegate", "continue", "rework"] if bounded else None
    _, prepared = fixture._checkpoint(seed, trigger, loaded)
    required = {item["source_id"] for item in prepared.context.read_policy["required_reads"]}
    required_bytes = sum((seed.state_dir / item["ref"]).stat().st_size
                         for item in prepared.context.source_manifest["sources"] if item["source_id"] in required)
    if bounded:
        decision = fixture._decision(prepared)
        plan = decision["run_plan"]
        plan["graph"]["work_units"] = [{"work_unit_id": tid, "task_ids": [tid], "stage_ids": ["impl"]}
                                       for tid in ["A", "B", "C"]]
        plan["delegation"] = [{"work_unit_id": tid, "preferred_role_refs": [role],
            "dispatch_rank": rank, "capability_refs": [], "skill_refs": []}
            for tid, role, rank in [("C", "dev-2", 0), ("B", "dev-1", 1), ("A", "dev-1", 2)]]
        plan["context_routes"] = []
        assert apply_orchestrator_agent_decision(seed, fixture._submit(seed, prepared, decision))["applied"]
    else:
        # Baseline has no admitted OA delegation; same read-pack measured above.
        cfg.workflow.orchestration = WorkflowOrchestrationConfig()
    context = {**vars(loaded), "flow_kind": "prd", "dispatch_base_commit": head,
               "profile_id": policy["profile_id"], "profile_digest": policy["profile_digest"]}
    for tid in ["A", "B", "C"]:
        TaskStore(seed.state_dir / "kanban.json").add(Task(id=tid, title=tid, status="backlog",
            blocked_by=["A"] if tid == "B" else [], contract=TaskContract(
                behavior="Implement " + tid, owner_role="dev", scope=[tid + ".txt"],
                evidence_contract={"source_refs": {key: context[key] for key in (
                    "plan_artifact_package_id", "plan_artifact_package_ref", "plan_artifact_package_digest", "task_map_generation")}})))
    seed.event_writer.append(ZfEvent(type="task.pipeline.generation.admitted", payload={
        **context, "schema_version": "task-pipeline-generation.v1", "task_ids": ["A", "B", "C"],
    }))
    return seed, required_bytes, next(port["sha256"] for port in package["produced"] if port["logical_name"] == "task_map")


def test_bounded_ab_dispatch_preserves_dependencies_and_restart(tmp_path, plan_ports):
    results = []
    for name, bounded in [("baseline", False), ("bounded", True)]:
        seed, required_bytes, task_map_digest = _seed(tmp_path / name, bounded)
        transport = _RecordingTransport()
        runtime = Orchestrator(seed.state_dir, seed.config, transport, project_root=seed.project_root)
        runtime._dispatch_ready()
        events = runtime.event_log.read_all()
        dispatched = [e for e in events if e.type == "task.pipeline.stage.dispatched"]
        assert [e.task_id for e in dispatched] == (["C", "A"] if bounded else ["A", "C"])
        assert len({e.payload["role_instance"] for e in dispatched}) == 2
        assert not any(e.type in {"critic.gate.requested", "prd.plan.requested", "orchestrator.replan_requested"} for e in events)
        projection = read_task_pipeline_projection(seed.state_dir, project_root=seed.project_root, config=seed.config)
        assert next(row for row in projection["tasks"] if row["task_id"] == "B")["pipeline_stage"] == "dependency_blocked"
        restarted = Orchestrator(seed.state_dir, seed.config, transport, project_root=seed.project_root)
        restarted._dispatch_ready()
        assert len(transport.sent) == 2
        results.append({"case": name, "task_map_digest": task_map_digest,
                        "required_read_bytes": required_bytes, "dispatch_order": [e.task_id for e in dispatched],
                        "parallel_workers": 2, "critic_requests": 0, "duplicate_dispatches": 0})
    assert results[0]["task_map_digest"] == results[1]["task_map_digest"]
    assert results[1]["required_read_bytes"] < results[0]["required_read_bytes"]
    print("BOUNDED_AB " + json.dumps(results))
