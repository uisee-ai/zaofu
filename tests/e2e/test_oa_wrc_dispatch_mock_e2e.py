"""OA submission -> admitted plan -> v4 WRC -> transport, with isolated Git state."""

from dataclasses import replace

import pytest

from tests.test_task_pipeline_orchestration import _admitted
from tests.test_controller_rendered_fanout_runtime import (
    _init_repo,
    _RecordingTransport,
)
from zf.core.config.schema import RoleLifecycleConfig
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator

pytestmark = pytest.mark.mock_e2e


@pytest.mark.parametrize("lanes", [1, 8])
def test_oa_delegation_reaches_transport_and_restart_does_not_redispatch(
    tmp_path, lanes
):
    head = _init_repo(tmp_path)
    seed, policy, contexts, _ = _admitted(tmp_path)
    context = contexts["TASK-1"]
    context.update(
        dispatch_base_commit=head,
        profile_id=policy["profile_id"],
        profile_digest=policy["profile_digest"],
    )
    task_ids = [f"TASK-{i}" for i in range(1, lanes + 1)]
    if lanes == 8:
        seed.config.roles.extend(
            replace(seed.config.roles[-1], instance_id=f"dev-{i}") for i in range(3, 9)
        )
        policy["pools"]["impl"].update(
            capacity=8, role_instances=[f"dev-{i}" for i in range(1, 9)]
        )
        policy["max_active_task_pipelines"] = 16
        policy["backpressure"]["max_unverified_tasks"] = 8
    config = seed.config
    config.workflow.orchestration.normal_frontier_owner = "wrc"
    config.project.workspace = str(tmp_path)
    config.project.state_dir = str(seed.state_dir)
    config.workflow.flow_metadata = {
        "task_pipeline": policy,
        "result_protocol": {
            "semantic_submit_profiles": {
                "implementation": "blocking",
                "task-verify": "blocking",
            },
        },
    }
    config.runtime.workdirs.enabled = True
    config.runtime.workdirs.mode = "worktree"
    config.runtime.workdirs.root = str(seed.state_dir / "workdirs")
    config.roles = [
        replace(role, lifecycle=RoleLifecycleConfig(mode="on_demand"))
        if role.name == "dev"
        else role
        for role in config.roles
    ]
    for task_id in task_ids:
        TaskStore(seed.state_dir / "kanban.json").add(
            Task(
                id=task_id,
                title="Implement isolated API slice",
                status="backlog",
                contract=TaskContract(
                    behavior=f"Create {task_id}.txt",
                    scope=[f"{task_id}.txt"],
                    owner_role="dev",
                    evidence_contract={
                        "source_refs": {
                            key: context[key]
                            for key in (
                                "plan_artifact_package_id",
                                "plan_artifact_package_ref",
                                "plan_artifact_package_digest",
                                "task_map_generation",
                            )
                        }
                    },
                ),
            )
        )
    seed.event_writer.append(
        ZfEvent(
            type="task.pipeline.generation.admitted",
            payload={
                **context,
                "schema_version": "task-pipeline-generation.v1",
                "frontier_owner": "wrc",
                "task_ids": task_ids,
            },
        )
    )
    transport = _RecordingTransport()
    runtime = Orchestrator(seed.state_dir, config, transport, project_root=tmp_path)
    from zf.runtime.task_pipeline_projection import read_task_pipeline_projection
    before = read_task_pipeline_projection(seed.state_dir, project_root=tmp_path, config=config)
    assert before["dispatchable"]["impl"][0]["role_instance"] == "dev-2"
    runtime._dispatch_ready()
    events = runtime.event_log.read_all()
    failures = [
        (e.type, e.payload)
        for e in events
        if e.type
        in {
            "kernel.housekeeping.failed",
            "task.pipeline.stage.dispatch_deferred",
            "task.pipeline.stage.waiting",
        }
    ]
    expected = (
        ["dev-2"]
        if lanes == 1
        else ["dev-2", "dev-1", *[f"dev-{i}" for i in range(3, 9)]]
    )
    assert [row[0] for row in transport.sent] == expected, failures
    receipt = next(e for e in events if e.type == "task.pipeline.stage.dispatched")
    assert receipt.payload["frontier_owner"] == "wrc"
    assert any(
        e.type == "workflow.normal_frontier.wrc.reconciled"
        and e.payload["owner"] == "wrc"
        for e in events
    )
    assert receipt.payload["orchestration_assignment"]["reason"] == "oa_preferred"
    assert receipt.payload["orchestration_assignment"]["execution_plan_ref"]
    assert "Active task: TASK-1" in transport.sent[0][1].read_text()
    restarted = Orchestrator(seed.state_dir, config, transport, project_root=tmp_path)
    restarted._dispatch_ready()
    assert len(transport.sent) == lanes
    assert (
        len(
            [
                e
                for e in restarted.event_log.read_all()
                if e.type == "task.pipeline.stage.dispatched"
            ]
        )
        == lanes
    )
