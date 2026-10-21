"""Real scheduler/dispatch/workspaces with a bounded, held mock transport."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_candidate_incremental import _git
from tests.test_task_pipeline_profile import _task_pipeline
from tests.test_plan_artifact_package import _package
from zf.core.config.schema import (
    GitIsolationConfig, ProjectConfig, RoleConfig, RoleLifecycleConfig,
    RuntimeConfig, WorkdirConfig, WorkflowConfig, ZfConfig,
)
from zf.core.config.task_pipeline_profile import compile_task_pipeline_profile
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.plan_artifact_package import write_plan_artifact_package
from zf.runtime.task_pipeline_runtime import reconcile_task_pipeline_runtime
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.workflow_operation import reduce_workflow_operations

pytestmark = pytest.mark.mock_e2e


def _runtime(root: Path, monkeypatch, stage: str):
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Eight Slot Test")
    _git(root, "config", "user.email", "test@example.com")
    (root / "README.md").write_text("fixture\n")
    (root / ".gitignore").write_text("state/\n")
    _git(root, "add", "README.md", ".gitignore")
    _git(root, "commit", "-qm", "test: seed")
    _git(root, "branch", "-M", "main")
    head = _git(root, "rev-parse", "HEAD")
    policy = compile_task_pipeline_profile(
        flow_kind="prd", profile_id="prd-flow-v4-task-pipeline",
        raw=_task_pipeline(
            mode="blocking", maxActiveTaskPipelines=16,
            pools={"impl": {"capacity": 8}, "verify": {"capacity": 8}},
            backpressure={"maxUnverifiedTasks": 16, "maxIntegrationQueue": 16},
        ),
        default_impl_roles=[f"impl-{i}" for i in range(8)],
        default_verify_roles=[f"verify-{i}" for i in range(8)],
    )
    state = root / "state"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n")
    config = ZfConfig(
        project=ProjectConfig(name="eight-slots", workspace=str(root), state_dir=str(state)),
        roles=[RoleConfig(
            name=kind, instance_id=f"{kind}-{i}", backend="mock",
            role_kind="writer" if kind == "impl" else "reader",
            lifecycle=RoleLifecycleConfig(mode="on_demand"),
        ) for kind in ("impl", "verify") for i in range(8)],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
            git=GitIsolationConfig(candidate_base_ref="main"),
        ),
        workflow=WorkflowConfig(flow_metadata={
            "task_pipeline": policy,
            "result_protocol": {"mode": "blocking", "semantic_submit_profiles": {
                "implementation": "blocking", "task-verify": "blocking",
            }},
        }),
    )
    runtime = Orchestrator(
        state, config, TmuxTransport(TmuxSession(session_name="eight", dry_run=True)),
    )
    monkeypatch.setattr(runtime, "_ensure_role_active", Mock())
    monkeypatch.setattr(runtime, "_set_worker_state", Mock())
    monkeypatch.setattr(runtime.transport, "is_alive", lambda _: False)
    delivered = []
    def send(role, briefing, prompt, context):
        delivered.append((role, briefing, context))
        return context
    monkeypatch.setattr(runtime, "_send_transport_task", send)
    body = _package(state, generation="map-8")
    body["workflow_run_id"] = "run-8"
    descriptor = write_plan_artifact_package(state, body)
    package = {
        "plan_artifact_package_id": descriptor["package_id"],
        "plan_artifact_package_ref": descriptor["ref"],
        "plan_artifact_package_digest": descriptor["sha256"],
    }
    runtime.event_writer.append(ZfEvent(
        type="plan.artifact_package.admitted", correlation_id="run-8",
        payload={"status": "admitted", "workflow_run_id": "run-8",
                 "task_map_generation": "map-8", "plan_revision": "r1",
                 "package_id": descriptor["package_id"], "package_ref": descriptor["ref"],
                 "package_digest": descriptor["sha256"]},
    ))
    count = 17 if stage == "mixed" else 9
    for i in range(count):
        runtime.task_store.add(Task(
            id=f"TASK-{i}", title=f"Independent behavior {i}", priority=i,
            contract=TaskContract(
                behavior="Read the fixture", scope=[f"feature-{i}.txt"],
                verification="test -f README.md", plan_ref="artifacts/plan/task_map.json",
                acceptance_criteria=[{
                    "id": f"AC-{i}", "text": "fixture is readable",
                    "verification_owner": "task_verify", "verification_tier": "runtime",
                    "verification_command_ids": ["contract-verification"],
                }],
                evidence_contract={
                    "source_refs": package, "workflow_run_id": "run-8",
                    "task_map_generation": "map-8",
                    "task_pipeline_entry_mode": "verify_only" if stage == "verify" or (stage == "mixed" and i < 8) else "standard",
                    "candidate_target": head,
                },
            ),
        ))
    runtime.event_writer.append(ZfEvent(
        type="task.pipeline.generation.admitted", origin="kernel",
        correlation_id="run-8", payload={
            "schema_version": "task-pipeline-generation.v1",
            "generation_id": "generation-8", "workflow_run_id": "run-8",
            "task_map_generation": "map-8", "task_ids": [f"TASK-{i}" for i in range(count)],
            "dispatch_base_commit": head, "profile_id": policy["profile_id"],
            "profile_digest": policy["profile_digest"], **package,
        },
    ))
    return runtime, delivered, head


@pytest.mark.parametrize("stage", ["impl", "verify", "mixed"])
def test_eight_stage_operations_in_flight_with_isolated_workspaces(
    tmp_path: Path, monkeypatch, stage: str,
):
    runtime, delivered, head = _runtime(tmp_path, monkeypatch, stage)
    decisions = reconcile_task_pipeline_runtime(runtime)
    expected = 16 if stage == "mixed" else 8
    assert len(delivered) == expected, (decisions, [
        event.payload for event in runtime.event_log.read_all()
        if event.type in {"orchestrator.error", "task.pipeline.stage.dispatch_deferred", "task.pipeline.stage.waiting"}
    ])
    operations = list(reduce_workflow_operations(runtime.event_log.read_all()).values())
    assert len([row for row in operations if row["status"] == "running"]) == expected
    dispatched = [e for e in runtime.event_log.read_all() if e.type == "task.pipeline.stage.dispatched"]
    assert len({e.payload["role_instance"] for e in dispatched}) == expected
    assert len({e.payload["workdir"] for e in dispatched}) == expected
    assert {e.payload["task_pipeline_stage"] for e in dispatched} == ({"impl", "verify"} if stage == "mixed" else {stage})
    if stage == "mixed":
        assert sum(e.payload["task_pipeline_stage"] == "impl" for e in dispatched) == 8
    for event in dispatched:
        assert _git(Path(event.payload["workdir"]), "rev-parse", "HEAD") == head
        if event.payload["task_pipeline_stage"] == "verify":
            assert event.payload["target_commit"] == head
            assert event.payload["target_snapshot_digest"]
    reconcile_task_pipeline_runtime(runtime)
    assert len(delivered) == expected  # No excess dispatch, no duplicate on replay.
    assert runtime.task_store.get(f"TASK-{expected}").status == "backlog"
    assert not any(e.type in {"candidate.ready", "run.goal.completed"} for e in runtime.event_log.read_all())
