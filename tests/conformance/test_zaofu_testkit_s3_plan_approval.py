"""Raw planning artifacts → approval → blocking dispatch; no seeded admission.

Run contract and planner-produced files are Given inputs. Only physical worker
activation and transport are inert. No approval, package/generation admission,
canonical task, or completion fact is injected by these tests.
"""

import json
from dataclasses import asdict

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import _submit_generation_stage
from tests.test_scheduler_task_attempt import _RecordingTransport
from tests.test_task_pipeline_generation import _git, _init_master_repo
from tests.test_task_pipeline_reconciler import _policy
from tests.testkit.command_runner import run_command
from zf.core.config.schema import (
    FanoutAggregateConfig, ProjectConfig, RoleConfig, RoleLifecycleConfig, RuntimeConfig, WorkdirConfig,
    WorkflowConfig, WorkflowStageConfig, WorkflowTaskAttemptConfig, ZfConfig,
)
from zf.core.events import ZfEvent
from zf.core.state.session import SessionStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential
from zf.runtime.ship import ShipService
from zf.runtime.run_contract import stable_json_sha256, write_run_contract
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.fixture
def planning_runtime(tmp_path, monkeypatch):
    return planning_runtime_at(tmp_path / "project", monkeypatch)


def planning_runtime_at(project, monkeypatch, *, task_items=None):
    """Seed raw planner inputs only; admission remains a production action."""
    _init_master_repo(project)
    state = project / "runtime-state"
    state.mkdir()
    SessionStore(state / "session.yaml").create(project_root=str(project))
    config = ZfConfig(
        project=ProjectConfig(name="plan-approval", state_dir=str(state)),
        roles=[RoleConfig(
            name=role, instance_id=f"{role}-1", backend="mock",
            role_kind="writer" if role == "impl" else "reader",
            lifecycle=RoleLifecycleConfig(mode="on_demand"),
        ) for role in ("impl", "verify")],
        runtime=RuntimeConfig(workdirs=WorkdirConfig(enabled=True, mode="worktree")),
        workflow=WorkflowConfig(
            plan_approval_enabled=True,
            stages=[WorkflowStageConfig(
                id="impl", trigger="task_map.ready", topology="fanout_writer_scoped",
                roles=["impl-1"], synthesize_canonical_tasks=True,
                aggregate=FanoutAggregateConfig(
                    mode="candidate_integration", success_event="candidate.ready",
                    failure_event="integration.failed",
                ),
            )],
            task_attempt=WorkflowTaskAttemptConfig(mode="enforce", max_attempts=3),
            flow_metadata={
                "flow_kind": "prd",
                "artifact_package": {"mode": "blocking"},
                "task_pipeline": {**_policy(), "mode": "blocking"},
                "result_protocol": {
                    "mode": "blocking", "semantic_submit_profiles": {
                        "implementation": "blocking", "task-verify": "blocking",
                    },
                },
            },
        ),
    )
    transport = _RecordingTransport(state)
    monkeypatch.setattr(transport, "is_alive", lambda _instance: False)
    monkeypatch.setattr(Orchestrator, "_ensure_role_active", lambda *a, **kw: False)
    runtime = Orchestrator(state, config, transport, project_root=project)
    contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    write_run_contract(state, {**contract, "contract_digest": stable_json_sha256(contract)})
    artifacts = state / "artifacts"
    artifacts.mkdir(exist_ok=True)
    requirement = artifacts / "requirement.md"
    requirement.write_text("# Goal\nREADME exists at the pinned commit.\n", encoding="utf-8")
    task_map = artifacts / "task-map.json"
    task_map.write_text(json.dumps({"tasks": task_items if task_items is not None else [{
        "task_id": "TASK-BLOCKING", "title": "Bounded README change",
        "scope": "README", "allowed_paths": ["README.md"],
        "acceptance_criteria": ["README exists at the pinned commit"],
        "verification": "git cat-file -e HEAD:README.md",
        "validation": {"commands": [{
            "id": "contract-verification", "command": "git cat-file -e HEAD:README.md",
            "tier": "runtime", "rolling_smoke": True, "deterministic": True,
            "reusable": True,
        }]},
    }]}), encoding="utf-8")
    config.workflow.stages[0].task_map = str(task_map)
    trigger = runtime.event_writer.append(ZfEvent(
        type="task_map.ready", actor="planner-fixture", correlation_id="run-1",
        payload={"workflow_run_id": "run-1", "flow_kind": "prd", "pdd_id": "GOAL-1",
                 "task_map_generation": "map-current", "task_map_ref": str(task_map),
                 "prd_ref": str(requirement)},
    ))
    return runtime, transport, trigger


def test_operator_approval_admits_raw_plan_and_reaches_candidate(planning_runtime, record_property):
    runtime, transport, trigger = planning_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    config.runtime.git.ship_target_branch = "master"
    runtime.run_once()
    events = runtime.event_log.read_all()
    [pending] = [e for e in events if e.type == "plan.approval.requested"]
    assert pending.payload["plan_id"] == trigger.id
    assert runtime.task_store.get("TASK-BLOCKING") is None
    assert transport.contexts == []
    assert not any(e.type == "task.pipeline.generation.admitted" for e in events)
    requested = runtime.event_writer.append(ZfEvent(
        type="control.action.requested", actor="operator", correlation_id="run-1",
        payload={"action": "plan-approve", "plan_id": trigger.id},
    ))
    result = ControlledActionService(
        state, runtime.event_writer, config=config, project_root=project,
        actor="operator", source="test", surface="test",
    ).execute(action="plan-approve", requested_action="plan-approve",
              payload={"plan_id": trigger.id}, requested=requested)
    assert result["ok"] is True
    runtime = Orchestrator(state, config, transport, project_root=project)
    tokens = {role: provision_role_submit_credential(state, role)
              for role in ("impl-1", "verify-1")}
    runtime.run_once()
    assert len(transport.contexts) == 1
    _, body = _submit_generation_stage(runtime, transport.contexts[-1], tokens["impl-1"], repair=True)
    target = body["target_commit"]
    runtime.run_once()
    assert len(transport.contexts) == 2
    _submit_generation_stage(runtime, transport.contexts[-1], tokens["verify-1"])
    runtime.run_once()
    assert runtime.task_store.get("TASK-BLOCKING").status == "done"
    [ready] = [e for e in runtime.event_log.read_all() if e.type == "candidate.ready"]
    freeze = hydrate_sidecar_ref(state, ready.payload["freeze_receipt_ref"]).payload
    assert freeze["status"] == "frozen"
    [integrated] = [e for e in runtime.event_log.read_all() if e.type == "integration.queue.integrated"]
    receipt = hydrate_sidecar_ref(state, integrated.payload["receipt_ref"]).payload
    assert receipt["task_commit"] == target
    assert _git(project, "show", f"{ready.payload['commit']}:README.md") == "K462 repaired"

    # Deliver the actual frozen candidate, not a separately seeded ready ref.
    candidate_ref = ready.payload.get("candidate_ref") or ready.payload.get("candidate_branch")
    assert candidate_ref
    original_head = _git(project, "rev-parse", "master")
    shipped = ShipService(
        state_dir=state, project_root=project, config=config,
        event_log=runtime.event_log,
    ).ship(
        target_ref=candidate_ref,
        pdd_id=str(ready.payload.get("pdd_id") or ""),
        event_writer=runtime.event_writer,
        causation_id=ready.id, correlation_id=trigger.correlation_id,
    )
    assert shipped.status == "completed" and shipped.ok is True
    assert shipped.payload["source_commit"] == ready.payload["commit"]
    assert shipped.payload["original_head"] == original_head
    final_commit = _git(project, "rev-parse", "HEAD")
    assert shipped.payload["final_commit"] == final_commit
    assert _git(project, "show", "-s", "--format=%P", final_commit).split() == [
        original_head, ready.payload["commit"],
    ]
    assert _git(project, "branch", "--show-current") == "master"
    assert (project / "README.md").read_text(encoding="utf-8") == "K462 repaired\n"
    post_merge = run_command(
        runtime.task_store.get("TASK-BLOCKING").contract.verification,
        command_id="blocking-post-merge", cwd=project,
        output_dir=project.parent / "post-merge-evidence", timeout_seconds=15,
    )
    assert post_merge.passed
    assert _git(project, "rev-parse", "HEAD") == final_commit
    assert _git(project, "diff", "HEAD", "--") == ""
    events = runtime.event_log.read_all()
    [completed] = [event for event in events if event.type == "ship.completed"]
    assert completed.causation_id == ready.id
    assert events.index(ready) < events.index(completed)
    before_replay = [event.id for event in events]
    replay = ShipService(
        state_dir=state, project_root=project, config=config, event_log=runtime.event_log,
    ).ship(target_ref=candidate_ref, event_writer=runtime.event_writer)
    assert replay.ok and replay.payload["idempotent"] is True
    assert _git(project, "rev-parse", "HEAD") == final_commit
    assert [event.id for event in runtime.event_log.read_all()] == before_replay
    assert len(transport.contexts) == 2
    record_property("blocking_local_delivery", json.dumps({
        "candidate_event_id": ready.id, "candidate_ref": candidate_ref,
        "ship_completed_event_id": completed.id, "ship": shipped.payload,
        "post_merge": asdict(post_merge), "replay_idempotent": True,
    }))


def test_web_token_approval_reaches_candidate_without_direct_control_action(
    planning_runtime, monkeypatch,
):
    """The Web token route must feed the same approval fact as the CLI action."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from zf.web.server import create_app

    runtime, transport, trigger = planning_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    runtime.run_once()
    assert transport.contexts == []
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "k472-approval-token")
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(project.parent / "web-workspace"))
    monkeypatch.delenv("ZF_WEB_TRUSTED_SESSION", raising=False)
    monkeypatch.delenv("ZF_WEB_PASSCODE", raising=False)
    (state / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state / "feature_list.json").write_text("[]\n", encoding="utf-8")
    client = TestClient(create_app(state, config=config, project_root=project))
    snapshot = client.get("/api/snapshot")
    assert snapshot.status_code == 200, snapshot.text
    assert "project" in snapshot.json(), snapshot.text
    project_id = snapshot.json()["project"]["project_id"]
    action_url = f"/api/projects/{project_id}/actions/plan-approve"
    body = {
        "project_id": project_id,
        "idempotency_key": "k472-plan-approve",
        "payload": {"plan_id": trigger.id},
    }
    before = (state / "events.jsonl").read_bytes()
    for headers in ({}, {"x-zf-web-token": "wrong"}):
        assert client.post(action_url, headers=headers, json=body).status_code == 403
        assert (state / "events.jsonl").read_bytes() == before
        assert runtime.task_store.get("TASK-BLOCKING") is None
        assert transport.contexts == []
    mismatch = client.post(
        action_url, headers={"x-zf-web-token": "k472-approval-token"},
        json={**body, "project_id": "wrong-project"},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["status"] == "project_mismatch"
    assert (state / "events.jsonl").read_bytes() == before
    approved = client.post(
        action_url, headers={"x-zf-web-token": "k472-approval-token"}, json=body,
    )
    assert approved.status_code == 200
    assert approved.json()["ok"] is True
    assert approved.json()["action"] == "plan-approve"
    after_approval = (state / "events.jsonl").read_bytes()
    replay = client.post(
        action_url, headers={"x-zf-web-token": "k472-approval-token"}, json=body,
    )
    assert replay.status_code == 200
    assert replay.json().get("idempotency", {}).get("status") == "replayed"
    assert (state / "events.jsonl").read_bytes() == after_approval
    approval_events = [event for event in runtime.event_log.read_all() if event.type == "plan.approved"]
    assert len(approval_events) == 1
    assert approval_events[0].actor == "operator"
    assert approval_events[0].payload["plan_id"] == trigger.id
    client.close()

    runtime = Orchestrator(state, config, transport, project_root=project)
    tokens = {role: provision_role_submit_credential(state, role)
              for role in ("impl-1", "verify-1")}
    runtime.run_once()
    assert len(transport.contexts) == 1
    _, body = _submit_generation_stage(runtime, transport.contexts[-1], tokens["impl-1"], repair=True)
    target = body["target_commit"]
    runtime.run_once()
    assert len(transport.contexts) == 2
    _submit_generation_stage(runtime, transport.contexts[-1], tokens["verify-1"])
    runtime.run_once()
    assert runtime.task_store.get("TASK-BLOCKING").status == "done"
    events = runtime.event_log.read_all()
    assert sum(event.type == "plan.approved" for event in events) == 1
    assert sum(event.type == "candidate.ready" for event in events) == 1
    [ready] = [event for event in events if event.type == "candidate.ready"]
    assert _git(project, "show", f"{ready.payload['commit']}:README.md") == "K462 repaired"
    [integrated] = [event for event in events if event.type == "integration.queue.integrated"]
    receipt = hydrate_sidecar_ref(state, integrated.payload["receipt_ref"]).payload
    assert target == receipt["task_commit"]
