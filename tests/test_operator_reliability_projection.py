from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.config.project_context import resolve_project_context
from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.workspace.registry import WorkspaceRegistry
from zf.runtime.assignment_route import project_assignment_routes
from zf.runtime.operator_reliability import (
    project_agent_cockpit,
    project_mutation_audit,
    project_recovery_catalog,
    project_worktree_drift_audit,
)
from zf.runtime.pause_lifecycle import is_dispatch_paused, project_pause_lifecycle


def test_agent_cockpit_prioritizes_stuck_worker_signals(tmp_path: Path) -> None:
    now = datetime(2026, 5, 24, 0, 10, tzinfo=timezone.utc)
    events = [
        ZfEvent(
            type="worker.heartbeat",
            actor="dev-1",
            task_id="T1",
            ts="2026-05-24T00:00:00+00:00",
            payload={
                "instance_id": "dev-1",
                "current_task_id": "T1",
                "context_usage_ratio": 0.91,
            },
        ),
        ZfEvent(
            type="worker.probe.silent",
            actor="watcher",
            task_id="T1",
            ts="2026-05-24T00:06:00+00:00",
            payload={"instance_id": "dev-1", "reason": "heartbeat threshold exceeded"},
        ),
        ZfEvent(
            type="worker.stuck",
            actor="watcher",
            task_id="T1",
            ts="2026-05-24T00:09:00+00:00",
            payload={"instance_id": "dev-1", "reason": "stuck threshold exceeded"},
        ),
    ]
    projection = project_agent_cockpit(
        tmp_path,
        events=events,
        agents=[
            {
                "instance_id": "dev-1",
                "parent_role": "dev",
                "backend": "codex",
                "task_id": "T1",
                "freshness": {"last_heartbeat_at": "2026-05-24T00:00:00+00:00"},
            }
        ],
        now=now,
    )

    worker = projection["workers"][0]
    assert projection["schema_version"] == "agent-cockpit.v1"
    assert projection["summary"]["stuck"] == 1
    assert worker["instance_id"] == "dev-1"
    assert worker["status"] == "stuck"
    assert worker["context_risk"] == "critical"
    assert "open_recovery_catalog" in worker["next_actions"]
    assert worker["heartbeat_age_sec"] == 600.0


def test_recovery_catalog_projects_explicit_runs_and_checkpoint_evidence(tmp_path: Path) -> None:
    now = datetime(2026, 5, 24, 1, tzinfo=timezone.utc)
    events = [
        ZfEvent(
            type="recovery.run.started",
            actor="zf-cli",
            task_id="T1",
            ts="2026-05-24T00:00:00+00:00",
            payload={"run_id": "rec-1", "trigger_event_id": "evt-stuck"},
        ),
        ZfEvent(
            type="recovery.step.started",
            actor="zf-cli",
            task_id="T1",
            ts="2026-05-24T00:01:00+00:00",
            payload={"run_id": "rec-1", "step_id": "checkpoint"},
        ),
        ZfEvent(
            type="recovery.step.completed",
            actor="zf-cli",
            task_id="T1",
            ts="2026-05-24T00:02:00+00:00",
            payload={"run_id": "rec-1", "step_id": "checkpoint"},
        ),
        ZfEvent(
            type="recovery.run.completed",
            actor="zf-cli",
            task_id="T1",
            ts="2026-05-24T00:03:00+00:00",
            payload={"run_id": "rec-1"},
        ),
        ZfEvent(
            type="worker.checkpointed",
            actor="dev-1",
            task_id="T2",
            ts="2026-05-24T00:04:00+00:00",
            payload={
                "instance_id": "dev-1",
                "checkpoint_id": "cp-1",
                "resume_packet_path": "memory/resume.md",
            },
        ),
        ZfEvent(
            type="worker.context.critical",
            actor="watcher",
            task_id="T2",
            ts="2026-05-24T00:05:00+00:00",
            payload={"instance_id": "dev-1", "reason": "context exceeded"},
        ),
    ]

    projection = project_recovery_catalog(tmp_path, events=events, now=now)
    runs = {row["run_id"]: row for row in projection["runs"]}

    assert projection["schema_version"] == "recovery-catalog.v1"
    assert runs["rec-1"]["status"] == "completed"
    assert runs["rec-1"]["step_count"] == 1
    assert runs["checkpoint:cp-1"]["status"] == "completed"
    assert runs["checkpoint:cp-1"]["evidence_refs"] == ["memory/resume.md"]
    assert projection["suggestions"][0]["recommended_recovery"] == "build_resume_packet_and_checkpoint"


def test_recovery_event_types_are_known() -> None:
    assert {
        "recovery.run.started",
        "recovery.step.started",
        "recovery.step.completed",
        "recovery.step.failed",
        "recovery.run.completed",
        "runtime.maintenance.entered",
        "runtime.maintenance.exited",
        "dispatch.paused",
        "dispatch.resumed",
        "worker.checkpointed",
    } <= KNOWN_EVENT_TYPES


def test_pause_lifecycle_projects_pause_resume_checkpoint_and_sweep(tmp_path: Path) -> None:
    now = datetime(2026, 5, 24, 2, tzinfo=timezone.utc)
    paused_events = [
        ZfEvent(
            type="runtime.maintenance.entered",
            actor="zf-autoresearch",
            ts="2026-05-24T00:00:00+00:00",
            payload={"reason": "repair", "trigger_id": "trig-1"},
        ),
        ZfEvent(
            type="dispatch.paused",
            actor="zf-autoresearch",
            ts="2026-05-24T00:00:01+00:00",
            payload={"reason": "repair"},
        ),
        ZfEvent(
            type="worker.checkpointed",
            actor="dev-1",
            task_id="T1",
            ts="2026-05-24T00:01:00+00:00",
            payload={
                "instance_id": "dev-1",
                "checkpoint_id": "ckpt-1",
                "resume_packet_path": "maintenance/ckpt-1.resume.json",
                "dirty_diff_artifact": "maintenance/ckpt-1.diff",
            },
        ),
        ZfEvent(
            type="agent.session.run.cancelled",
            actor="web",
            task_id="T1",
            ts="2026-05-24T00:02:00+00:00",
            payload={
                "conversation_id": "kanban:default",
                "thread_id": "main",
                "run_id": "run-1",
                "provider": "codex-headless",
                "reason": "operator cancelled before repair",
            },
        ),
    ]

    paused = project_pause_lifecycle(tmp_path, events=paused_events, now=now)

    assert paused["schema_version"] == "pause-lifecycle.v1"
    assert paused["status"] == "paused"
    assert paused["dispatch_allowed"] is False
    assert paused["current"]["reason"] == "repair"
    assert paused["checkpoints"][0]["resume_packet_path"].endswith("ckpt-1.resume.json")
    assert paused["checkpoints"][0]["during_pause"] is True
    assert paused["affected_sessions"][0]["run_id"] == "run-1"
    assert paused["affected_sessions"][0]["during_pause"] is True

    resuming_events = [
        *paused_events,
        ZfEvent(
            type="loop.resume_requested",
            actor="feishu:u1",
            ts="2026-05-24T00:03:00+00:00",
        ),
        ZfEvent(
            type="worker.probe.silent",
            actor="watcher",
            task_id="T1",
            ts="2026-05-24T00:04:00+00:00",
            payload={"instance_id": "dev-1", "reason": "stale after resume"},
        ),
    ]
    resuming = project_pause_lifecycle(tmp_path, events=resuming_events, now=now)

    assert resuming["status"] == "resuming"
    assert resuming["dispatch_allowed"] is True
    assert resuming["resume_sweep"]["stale_workers"] == ["dev-1"]
    assert (
        resuming["resume_sweep"]["suggestions"][0]["recommended_recovery"]
        == "probe_or_respawn_stale_worker"
    )

    running = project_pause_lifecycle(
        tmp_path,
        events=[
            *paused_events,
            ZfEvent(
                type="dispatch.resumed",
                actor="zf-autoresearch",
                ts="2026-05-24T00:05:00+00:00",
            ),
        ],
        now=now,
    )
    assert running["status"] == "running"
    assert running["dispatch_allowed"] is True


def test_dispatch_ready_skips_new_dispatch_while_paused(tmp_path: Path) -> None:
    from zf.core.config.schema import (
        ProjectConfig,
        RoleConfig,
        SessionConfig,
        WorkflowConfig,
        ZfConfig,
    )
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="paused task", status="backlog", assigned_to="dev"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="dispatch.paused", actor="operator", payload={"reason": "maintenance"}))

    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-t"),
        roles=[RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")],
        workflow=WorkflowConfig(),
    )

    class _StubTransport:
        def __init__(self) -> None:
            self.sends: list[str] = []

        def send_task(self, role_name, briefing_path, prompt):
            self.sends.append(role_name)

        def is_alive(self, role_name):
            return True

        def capture_log(self, role_name, lines=200):
            return ""

    transport = _StubTransport()
    orchestrator = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]

    decisions = orchestrator._dispatch_ready()  # type: ignore[attr-defined]

    assert decisions == []
    assert transport.sends == []
    assert store.get("T1").status == "backlog"
    assert is_dispatch_paused(state_dir) is True
    skipped = [
        event for event in log.read_all()
        if event.type == "orchestrator.dispatch_skipped"
    ]
    assert skipped[-1].payload["reason"] == "dispatch_paused"

    log.append(ZfEvent(type="dispatch.resumed", actor="operator"))
    decisions = orchestrator._dispatch_ready()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert transport.sends == ["dev"]


def test_mutation_audit_classifies_controlled_paths_and_redacts_secrets(tmp_path: Path) -> None:
    events = [
        ZfEvent(
            type="web.action.requested",
            actor="operator",
            task_id="T1",
            payload={
                "action": "create-task",
                "target": "T1",
                "api_token": "sk-this-secret-value-should-redact",
            },
        ),
        ZfEvent(
            type="assignment.intent.proposed",
            actor="kanban-agent",
            task_id="T1",
            payload={"target_agent": "dev-1", "reason": "operator selected dev"},
        ),
        ZfEvent(
            type="workflow.invoke.requested",
            actor="operator",
            task_id="T1",
            payload={"pattern_id": "squad-review", "workflow_invoke_event_id": "evt-wf"},
        ),
        ZfEvent(
            type="workdir.writer_synced",
            actor="zf-cli",
            task_id="T1",
            payload={
                "instance_id": "dev-1",
                "backup_ref": "refs/zf/workdir-backups/dev/1",
                "stashed_ref": "refs/zf/workdir-stash/dev/1",
            },
        ),
    ]

    projection = project_mutation_audit(tmp_path, events=events)
    by_type = {row["mutation_type"]: row for row in projection["entries"]}

    assert projection["schema_version"] == "mutation-audit.v1"
    assert by_type["task_create"]["controlled_path"] == "web_action"
    assert by_type["assignment_intent"]["status"] == "requested"
    assert by_type["workflow_invoke"]["target"] == "squad-review"
    assert by_type["worktree"]["evidence_refs"] == [
        "refs/zf/workdir-backups/dev/1",
        "refs/zf/workdir-stash/dev/1",
    ]
    # 5c6e26f: key-based redact_obj overrides the inner placeholder for
    # *_token keys with [REDACTED_SECRET] (redaction got stricter).
    assert by_type["task_create"]["payload"]["api_token"] == "[REDACTED_SECRET]"


def test_assignment_route_links_squad_synthesis_to_workflow_invoke(tmp_path: Path) -> None:
    synthesis = ZfEvent(
        type="channel.synthesis.proposed",
        id="evt-synth",
        actor="web",
        ts="2026-05-24T00:00:00+00:00",
        payload={
            "channel_id": "ch-auth",
            "thread_id": "main",
            "decision": "invoke_workflow",
            "summary": "run review wave",
            "source": "web",
            "recommended_workflow": {"pattern_id": "review-wave"},
        },
    )
    events = [
        ZfEvent(
            type="assignment.intent.proposed",
            actor="operator",
            task_id="TASK-1",
            ts="2026-05-24T00:01:00+00:00",
            payload={
                "proposal_id": "assign-1",
                "task_id": "TASK-1",
                "assignee_type": "squad",
                "assignee_id": "ch-auth",
                "channel_id": "ch-auth",
                "dispatches": False,
            },
        ),
        synthesis,
        ZfEvent(
            type="workflow.invoke.requested",
            id="evt-wf",
            actor="operator",
            task_id="TASK-1",
            ts="2026-05-24T00:02:00+00:00",
            payload={
                "task_id": "TASK-1",
                "pattern_id": "review-wave",
                "channel_id": "ch-auth",
                "thread_id": "main",
                "requested_by": "operator",
                "reason": "synthesis",
                "source": "web",
                "synthesis_event_id": "evt-synth",
            },
        ),
        ZfEvent(
            type="workflow.invoke.accepted",
            actor="zf-cli",
            task_id="TASK-1",
            ts="2026-05-24T00:03:00+00:00",
            payload={
                "task_id": "TASK-1",
                "pattern_id": "review-wave",
                "source_event_id": "evt-wf",
            },
        ),
    ]

    projection = project_assignment_routes(tmp_path, events=events)
    route = projection["routes"][0]

    assert projection["schema_version"] == "assignment-routes.v1"
    assert route["stage"] == "execution_accepted"
    assert route["assignee_type"] == "squad"
    assert route["channel_id"] == "ch-auth"
    assert route["pattern_id"] == "review-wave"
    assert route["dispatches"] is False
    assert route["execution_started"] is False
    assert {"evt-synth", "evt-wf"} <= set(route["evidence_event_ids"])
    assert route["summary"] == "assignment intent -> squad synthesis -> workflow requested -> execution accepted"


def test_assignment_intent_alone_never_marks_execution_started(tmp_path: Path) -> None:
    events = [
        ZfEvent(
            type="assignment.intent.proposed",
            actor="operator",
            task_id="TASK-2",
            payload={
                "proposal_id": "assign-2",
                "task_id": "TASK-2",
                "assignee_type": "agent",
                "assignee_id": "dev-1",
                "dispatches": False,
            },
        )
    ]

    route = project_assignment_routes(tmp_path, events=events)["routes"][0]

    assert route["stage"] == "assignment_intent"
    assert route["stage_label"] == "Assignment Intent Only"
    assert route["execution_started"] is False


def test_worktree_drift_audit_surfaces_refs_and_dirty_retire_hint(tmp_path: Path) -> None:
    events = [
        ZfEvent(
            type="workdir.writer_synced",
            actor="zf-cli",
            task_id="T1",
            payload={
                "instance_id": "dev-1",
                "project_path": "/tmp/project/.zf/workdirs/dev/project",
                "backup_ref": "refs/zf/workdir-backups/dev/1",
                "stashed_ref": "refs/zf/workdir-stash/dev/1",
                "source_ref": "main",
            },
        ),
        ZfEvent(
            type="workdir.retire_failed",
            actor="zf-cli",
            payload={
                "instance_id": "dev-2",
                "workdir": "/tmp/project/.zf/workdirs/dev-2",
                "status": "dirty",
                "reason": "workdir has uncommitted changes",
            },
        ),
        ZfEvent(
            type="reader.write_violation",
            actor="zf-cli",
            task_id="T2",
            payload={
                "instance_id": "review-1",
                "status": " M src/app.py",
                "reset": True,
            },
        ),
    ]

    projection = project_worktree_drift_audit(tmp_path, events=events)
    rows = {row["instance_id"]: row for row in projection["entries"]}

    assert projection["schema_version"] == "worktree-drift-audit.v1"
    assert rows["dev-1"]["status"] == "synced_with_stash"
    assert rows["dev-1"]["backup_ref"] == "refs/zf/workdir-backups/dev/1"
    assert rows["dev-1"]["stashed_ref"] == "refs/zf/workdir-stash/dev/1"
    assert rows["dev-1"]["action_required"] is True
    assert rows["dev-2"]["status"] == "dirty_retire_refused"
    assert "checkpoint" in rows["dev-2"]["recovery_hint"]
    assert rows["review-1"]["status"] == "reader_dirty_reset"


def test_project_api_exposes_operator_reliability_projections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from zf.web.server import create_app

    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root = tmp_path / "project"
    state_dir = _make_project(root, name="alpha")
    log = EventLog(state_dir / "events.jsonl")
    log.append(
        ZfEvent(
            type="worker.heartbeat",
            actor="dev-1",
            task_id="T1",
            payload={"instance_id": "dev-1", "context_usage_ratio": 0.91},
        )
    )
    log.append(
        ZfEvent(
            type="worker.stuck",
            actor="watcher",
            task_id="T1",
            payload={"instance_id": "dev-1", "reason": "stuck threshold exceeded"},
        )
    )
    log.append(
        ZfEvent(
            type="worker.checkpointed",
            actor="dev-1",
            task_id="T1",
            payload={
                "instance_id": "dev-1",
                "checkpoint_id": "cp-1",
                "resume_packet_path": "memory/resume.md",
            },
        )
    )
    log.append(
        ZfEvent(
            type="web.action.requested",
            actor="operator",
            task_id="T1",
            payload={"action": "update-task", "target": "T1", "secret_token": "sk-secret-secret-secret"},
        )
    )
    log.append(
        ZfEvent(
            type="workdir.writer_synced",
            actor="zf-cli",
            task_id="T1",
            payload={
                "instance_id": "dev-1",
                "backup_ref": "refs/zf/workdir-backups/dev/1",
                "stashed_ref": "refs/zf/workdir-stash/dev/1",
            },
        )
    )
    log.append(
        ZfEvent(
            type="assignment.intent.proposed",
            actor="operator",
            task_id="T1",
            payload={
                "proposal_id": "assign-1",
                "task_id": "T1",
                "assignee_type": "squad",
                "assignee_id": "ch-auth",
                "channel_id": "ch-auth",
                "dispatches": False,
            },
        )
    )
    log.append(
        ZfEvent(
            type="dispatch.paused",
            actor="operator",
            payload={"reason": "operator repair window"},
        )
    )

    project = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=root))
    app = create_app(state_dir, config=load_config(root / "zf.yaml"), project_root=root)
    client = TestClient(app)

    cockpit = client.get(f"/api/projects/{project.project_id}/agent-cockpit").json()
    assignment_routes = client.get(f"/api/projects/{project.project_id}/assignment-routes").json()
    recovery = client.get(f"/api/projects/{project.project_id}/recovery").json()
    pause_lifecycle = client.get(f"/api/projects/{project.project_id}/pause-lifecycle").json()
    provider_capabilities = client.get(f"/api/projects/{project.project_id}/provider-capabilities").json()
    mutations = client.get(f"/api/projects/{project.project_id}/mutation-audit").json()
    worktree = client.get(f"/api/projects/{project.project_id}/worktree-drift").json()
    snapshot = client.get(f"/api/projects/{project.project_id}/snapshot").json()

    assert cockpit["summary"]["stuck"] == 1
    assert cockpit["workers"][0]["task_id"] == "T1"
    assert assignment_routes["routes"][0]["stage"] == "assignment_intent"
    assert {row["run_id"] for row in recovery["runs"]} >= {"checkpoint:cp-1"}
    assert pause_lifecycle["status"] == "paused"
    assert pause_lifecycle["dispatch_allowed"] is False
    assert provider_capabilities["schema_version"] == "provider-capabilities.v1"
    assert {row["backend"] for row in provider_capabilities["providers"]} >= {"claude-code", "codex", "mock"}
    assert any(row["mutation_type"] == "worktree" for row in mutations["entries"])
    assert any(row["mutation_type"] == "task_update" for row in mutations["entries"])
    assert worktree["entries"][0]["status"] == "synced_with_stash"
    assert snapshot["agent_cockpit"]["summary"]["stuck"] == 1
    assert snapshot["assignment_routes"]["summary"]["intent_only"] == 1
    assert {row["run_id"] for row in snapshot["recovery"]["runs"]} >= {"checkpoint:cp-1"}
    assert snapshot["pause_lifecycle"]["status"] == "paused"
    assert snapshot["provider_capabilities"]["summary"]["providers"] >= 5
    assert snapshot["mutation_audit"]["summary"]["entries"] >= 2
    assert snapshot["worktree_drift"]["summary"]["stashed_refs"] == 1


def _make_project(root: Path, *, name: str, state_dir_name: str = ".zf") -> Path:
    root.mkdir(parents=True)
    (root / "zf.yaml").write_text(
        (
            'version: "1.0"\n'
            "project:\n"
            f"  name: {name}\n"
            f"  state_dir: {state_dir_name}\n"
        ),
        encoding="utf-8",
    )
    state_dir = root / state_dir_name
    state_dir.mkdir(parents=True)
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="session.started", actor="zf-cli"),
    )
    return state_dir
