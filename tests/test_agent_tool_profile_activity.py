from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from zf.web.agent_child_activity import build_child_activity_projection
from zf.web.agent_conversation_store import AgentConversationStore
from zf.web.agent_profile_builder import AgentProfileBuilder, AgentProfileConflict
from zf.web.agent_tool_policy import AgentToolCatalog, mcp_controlled_intent
from zf.web.agent_turn_store import AgentTurnConflict, AgentTurnStore
from zf.web.webchat_runtime import prepare_durable_webchat_run


def test_tool_catalog_is_stable_unknown_denied_and_ask_is_exact(tmp_path: Path) -> None:
    store = AgentTurnStore(tmp_path)
    turn = store.submit(
        project_id="p", conversation_id="c", thread_id="t",
        backend="codex-headless", permission_profile="workspace_writer",
        idempotency_key="turn",
    )[0]
    claim = store.claim(turn["turn_id"], owner="worker")
    catalog = AgentToolCatalog()
    first = catalog.projection(surface="kanban_agent")
    second = catalog.projection(surface="kanban_agent")
    assert first["digest"] == second["digest"]
    assert catalog.evaluate(
        state_dir=tmp_path, turn_id=turn["turn_id"], attempt_id=claim.attempt_id,
        name="unknown", args={}, workspace="/tmp", surface="kanban_agent",
    ).decision == "deny"
    decision = catalog.evaluate(
        state_dir=tmp_path, turn_id=turn["turn_id"], attempt_id=claim.attempt_id,
        name="edit_file", args={"path": "a.py"}, workspace="/project", surface="kanban_agent",
    )
    assert decision.decision == "ask"
    interaction = decision.interaction or {}
    store.answer_interaction(
        interaction["interaction_id"], revision=interaction["revision"], response={"approved": True},
    )
    with pytest.raises(Exception, match="currentness mismatch"):
        store.consume_interaction(
            interaction["interaction_id"], revision=interaction["revision"],
            expected_currentness={
                "tool": "workspace.edit", "args_digest": "sha256:changed",
                "workspace": "/project", "attempt_id": claim.attempt_id, "surface": "kanban_agent",
            },
        )


def test_unattended_worker_does_not_inherit_kanban_ask_default() -> None:
    catalog = AgentToolCatalog()
    projection = catalog.projection(surface="worker", unattended=True)
    edit = next(item for item in projection["items"] if item["name"] == "workspace.edit")
    assert edit["effective_policy"] == "allow"


def test_explicit_tool_policy_overrides_default_and_mcp_intent_excludes_secrets(
    tmp_path: Path,
) -> None:
    store = AgentTurnStore(tmp_path)
    turn = store.submit(
        project_id="p", conversation_id="c", thread_id="t",
        backend="codex-headless", permission_profile="workspace_writer",
        idempotency_key="override-turn",
    )[0]
    claim = store.claim(turn["turn_id"], owner="worker")
    catalog = AgentToolCatalog()
    allowed = catalog.evaluate(
        state_dir=tmp_path, turn_id=turn["turn_id"], attempt_id=claim.attempt_id,
        name="workspace.edit", args={"path": "a.py"}, workspace="/project",
        surface="kanban_agent", overrides={"workspace.edit": "allow"},
    )
    assert allowed.decision == "allow"
    denied = catalog.evaluate(
        state_dir=tmp_path, turn_id=turn["turn_id"], attempt_id=claim.attempt_id,
        name="workspace.edit", args={"path": "a.py"}, workspace="/project",
        surface="kanban_agent", overrides={"workspace": "deny", "workspace.edit": "allow"},
    )
    assert denied.decision == "deny"
    intent = mcp_controlled_intent(
        server="github", tool="issues.create", args={"title": "Bug"},
        credential_ref="host://github/default",
    )
    assert intent["credentials_exposed"] is False
    assert intent["effect"] == "controlled_intent_only"
    assert intent["host_credential_configured"] is True
    assert "credential_ref" not in intent
    with pytest.raises(ValueError, match="credential fields"):
        mcp_controlled_intent(
            server="github", tool="issues.create",
            args={"request": {"headers": {"access_token": "secret"}}},
        )


def test_profile_builder_proposes_validates_and_applies_without_writing_zf_yaml(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "skills" / "review").mkdir(parents=True)
    (project / "skills" / "review" / "SKILL.md").write_text("# Review", encoding="utf-8")
    state = project / ".state"
    builder = AgentProfileBuilder(state, project_root=project)
    base = {
        "role": "operator", "provider": "codex-headless",
        "permission_profile": "read_only", "skills": [], "tools": ["task.read"],
    }
    proposed = {**base, "skills": ["review"], "tools": ["task.read", "workspace.edit"]}
    proposal, created = builder.propose(
        project_id="p", profile_id="kanban-agent", base_profile=base, proposed_profile=proposed,
    )
    assert created is True
    assert proposal["validation"]["valid"] is True
    assert not (project / "zf.yaml").exists()
    applied = builder.apply(
        proposal["proposal_id"], expected_base_digest=proposal["base_digest"],
        expected_proposal_digest=proposal["proposal_digest"],
    )
    assert applied["revision"] == 1
    assert applied["profile"]["skills"] == ["review"]


def test_profile_builder_reapplying_current_digest_is_a_noop(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    builder = AgentProfileBuilder(project / ".state", project_root=project)
    base = {
        "role": "operator", "provider": "codex-headless",
        "permission_profile": "read_only", "skills": [], "tools": ["task.read"],
    }
    target = {**base, "permission_profile": "workspace_writer"}
    first, _ = builder.propose(
        project_id="p", profile_id="kanban-agent",
        base_profile=base, proposed_profile=target,
    )
    applied = builder.apply(
        first["proposal_id"], expected_base_digest=first["base_digest"],
        expected_proposal_digest=first["proposal_digest"],
    )
    assert applied["revision"] == 1

    duplicate, created = builder.propose(
        project_id="p", profile_id="kanban-agent",
        base_profile=target, proposed_profile=target,
    )
    assert created is True
    reapplied = builder.apply(
        duplicate["proposal_id"], expected_base_digest=duplicate["base_digest"],
        expected_proposal_digest=duplicate["proposal_digest"],
    )
    assert reapplied["revision"] == 1
    assert reapplied["profile_digest"] == applied["profile_digest"]


def test_profile_builder_rejects_secret_permission_and_stale_base(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    builder = AgentProfileBuilder(project / ".state", project_root=project)
    invalid = builder.validate({
        "role": "operator", "provider": "codex-headless",
        "permission_profile": "dangerous_full", "api_key": "secret",
    })
    assert invalid["valid"] is False
    assert {item["field"] for item in invalid["errors"]} >= {"permission_profile", "api_key"}
    base = {"role": "operator", "provider": "codex-headless", "permission_profile": "read_only"}
    proposal, _ = builder.propose(
        project_id="p", profile_id="p1", base_profile=base, proposed_profile=base,
    )
    with pytest.raises(AgentProfileConflict, match="base digest"):
        builder.apply(
            proposal["proposal_id"], expected_base_digest="sha256:stale",
            expected_proposal_digest=proposal["proposal_digest"],
        )


def test_profile_builder_never_persists_secret_bearing_proposal(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    builder = AgentProfileBuilder(project / ".state", project_root=project)
    safe = {
        "role": "operator", "provider": "codex-headless",
        "permission_profile": "read_only", "skills": [], "tools": ["task.read"],
    }
    with pytest.raises(AgentProfileConflict, match="secret fields"):
        builder.propose(
            project_id="p", profile_id="kanban-agent",
            base_profile={**safe, "api_token": "must-not-persist"},
            proposed_profile=safe,
        )
    with pytest.raises(AgentProfileConflict, match="secret fields"):
        builder.propose(
            project_id="p", profile_id="kanban-agent",
            base_profile=safe,
            proposed_profile={**safe, "nested": {"password": "must-not-persist"}},
        )
    assert builder.artifacts.list_versions(project_id="p", principal="agent-profile-builder") == []


def test_identical_profile_bodies_for_different_profiles_do_not_alias(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    builder = AgentProfileBuilder(project / ".state", project_root=project)
    profile = {
        "role": "operator", "provider": "codex-headless",
        "permission_profile": "read_only", "skills": [], "tools": ["task.read"],
    }
    first, _ = builder.propose(
        project_id="p", profile_id="profile-a",
        base_profile=profile, proposed_profile=profile,
    )
    second, _ = builder.propose(
        project_id="p", profile_id="profile-b",
        base_profile=profile, proposed_profile=profile,
    )
    assert first["proposal_id"] != second["proposal_id"]
    assert first["proposal_digest"] != second["proposal_digest"]


def test_applied_profile_revision_is_pinned_by_new_turns(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = project / ".state"
    builder = AgentProfileBuilder(state, project_root=project)
    profile = {
        "role": "operator", "provider": "codex-headless",
        "permission_profile": "read_only", "skills": [], "tools": ["task.read"],
    }
    proposal, _ = builder.propose(
        project_id="p", profile_id="kanban-agent",
        base_profile=profile, proposed_profile=profile,
    )
    applied = builder.apply(
        proposal["proposal_id"], expected_base_digest=proposal["base_digest"],
        expected_proposal_digest=proposal["proposal_digest"],
    )
    conversations = AgentConversationStore(state)
    source = conversations.add_message(
        project_id="p", conversation_id="c", thread_id="main",
        role="user", origin="web", content="long context",
    )[0]
    conversations.compact(
        project_id="p", conversation_id="c", thread_id="main",
        summary="durable compact context", covered_message_ids=[source["message_id"]],
    )
    run = prepare_durable_webchat_run(
        state_dir=state, project_root=project,
        payload={"project_id": "p", "conversation_id": "c", "thread_key": "main"},
        message="use the profile", backend="codex-headless",
        permission_profile="read_only", turn_id="turn-profile", user_message_id="msg-profile",
    )
    turn = AgentTurnStore(state).get(run.turn_id)
    assert turn["profile_digest"] == applied["profile_digest"]
    assert turn["request"]["profile_revision"]["revision"] == applied["revision"]
    assert turn["request"]["compact_summary"]["summary"] == "durable compact context"


def test_applied_profile_mismatch_fails_before_message_or_turn_is_written(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = project / ".state"
    builder = AgentProfileBuilder(state, project_root=project)
    profile = {
        "role": "operator", "provider": "codex-headless",
        "permission_profile": "read_only", "skills": [], "tools": ["task.read"],
    }
    proposal, _ = builder.propose(
        project_id="p", profile_id="kanban-agent",
        base_profile=profile, proposed_profile=profile,
    )
    applied = builder.apply(
        proposal["proposal_id"], expected_base_digest=proposal["base_digest"],
        expected_proposal_digest=proposal["proposal_digest"],
    )
    with pytest.raises(AgentTurnConflict, match="provider"):
        prepare_durable_webchat_run(
            state_dir=state, project_root=project,
            payload={"project_id": "p", "conversation_id": "c", "thread_key": "main"},
            message="must not persist", backend="claude-headless",
            permission_profile="read_only", turn_id="turn-mismatch", user_message_id="msg-mismatch",
        )
    with pytest.raises(AgentTurnConflict, match="stale"):
        prepare_durable_webchat_run(
            state_dir=state, project_root=project,
            payload={
                "project_id": "p", "conversation_id": "c", "thread_key": "main",
                "profile_digest": "sha256:stale",
            },
            message="must not persist", backend="codex-headless",
            permission_profile="read_only", turn_id="turn-stale", user_message_id="msg-stale",
        )
    assert AgentTurnStore(state).list_turns(project_id="p") == []
    assert AgentConversationStore(state).list_messages(
        project_id="p", conversation_id="c", thread_id="main",
    )["items"] == []
    assert builder.current("kanban-agent")["profile_digest"] == applied["profile_digest"]


def test_child_activity_keeps_worker_terminal_separate_from_task_done() -> None:
    events = [
        {"id": "e1", "type": "worker.started", "ts": "2026-01-01T00:00:00Z", "payload": {
            "worker_id": "dev-1", "task_id": "task-1", "parent_turn_id": "turn-1", "role": "dev",
        }},
        {"id": "e2", "type": "worker.completed", "ts": "2026-01-01T00:01:00Z", "payload": {
            "worker_id": "dev-1", "task_id": "task-1", "parent_turn_id": "turn-1",
        }},
        {"id": "e3", "type": "channel.member.reply.completed", "payload": {
            "member_id": "reviewer", "channel_id": "ch-1", "parent_turn_id": "turn-1",
        }},
    ]
    projection = build_child_activity_projection(
        events, parent_turn_id="turn-1", task_status={"task-1": "in_progress"},
    )
    worker = next(item for item in projection["items"] if item["activity_id"] == "dev-1")
    channel = next(item for item in projection["items"] if item["activity_id"] == "reviewer")
    assert worker["kind"] == "delivery_worker"
    assert "not Task completion" in worker["why_not_done"]
    assert channel["kind"] == "channel_member"


def test_child_activity_drops_empty_artifact_refs() -> None:
    projection = build_child_activity_projection([{
        "id": "e1", "type": "child.started", "payload": {
            "child_id": "child-1", "artifact_refs": [{"ref": None}, None, {"ref": "artifact://v1"}],
        },
    }])
    assert projection["items"][0]["artifact_refs"] == ["artifact://v1"]


def test_child_activity_excludes_root_kanban_turn_and_normalizes_missing_task_id() -> None:
    projection = build_child_activity_projection([
        {
            "id": "root",
            "type": "kanban.agent.turn.completed",
            "task_id": None,
            "payload": {"role_instance": "kanban-agent", "status": "completed"},
        },
        {
            "id": "child",
            "type": "child.completed",
            "task_id": None,
            "payload": {"child_id": "child-1", "task_id": None},
        },
    ])
    assert [item["activity_id"] for item in projection["items"]] == ["child-1"]
    assert projection["items"][0]["task_id"] == ""


def test_child_activity_excludes_headless_root_run_with_member_identity() -> None:
    projection = build_child_activity_projection([
        {
            "id": "root-run",
            "type": "agent.session.run.completed",
            "payload": {
                "source": "kanban-agent.headless",
                "member_id": "kanban-agent",
                "run_id": "run-1",
                "thread_id": "kanban:project-1",
            },
        },
        {
            "id": "child-run",
            "type": "agent.session.run.completed",
            "payload": {
                "source": "channel-member",
                "member_id": "reviewer",
                "run_id": "run-2",
            },
        },
    ])
    assert [item["activity_id"] for item in projection["items"]] == ["reviewer"]


def test_child_activity_keeps_three_authorities_distinct_and_controls_declared() -> None:
    events = [
        {"id": "e1", "type": "child.started", "ts": "2026-09-30T00:09:00Z", "payload": {
            "child_id": "analysis-1", "parent_turn_id": "turn-1", "thread_id": "child-thread",
            "control_turn_id": "child-turn", "control_conversation_id": "c-1",
            "control_thread_id": "child-thread",
            "control_capabilities": ["queue", "steer", "interrupt", "cancel"],
        }},
        {"id": "e2", "type": "channel.member.reply.completed", "ts": "2026-09-30T00:09:30Z", "payload": {
            "member_id": "reviewer", "channel_id": "ch-1", "parent_turn_id": "turn-1",
            "artifact_refs": ["artifact://channel-output"],
        }},
        {"id": "e3", "type": "worker.started", "ts": "2026-09-30T00:09:40Z", "payload": {
            "worker_id": "dev-1", "task_id": "task-1", "parent_turn_id": "turn-1",
            "allowed_actions": ["worker-reply", "worker-drain"],
        }},
    ]
    projection = build_child_activity_projection(
        events,
        parent_turn_id="turn-1",
        now=datetime(2026, 9, 30, 0, 10, tzinfo=timezone.utc),
    )
    items = {item["activity_id"]: item for item in projection["items"]}
    assert {item["kind"] for item in items.values()} == {
        "ephemeral_child", "channel_member", "delivery_worker",
    }
    assert {item["operation"] for item in items["analysis-1"]["controls"]} == {
        "queue", "steer", "interrupt", "cancel",
    }
    assert items["reviewer"]["controls"] == []
    assert items["reviewer"]["artifact_refs"] == ["artifact://channel-output"]
    assert {
        (item["operation"], item["action"])
        for item in items["dev-1"]["controls"]
    } == {("steer", "worker-reply"), ("cancel", "worker-drain")}


def test_child_activity_stale_and_unavailable_are_not_guessed_terminal() -> None:
    events = [
        {"id": "e1", "type": "child.heartbeat", "ts": "2026-09-30T00:00:00Z", "payload": {
            "child_id": "stale-child", "parent_turn_id": "turn-1",
        }},
        {"id": "e2", "type": "child.provider.unavailable", "ts": "2026-09-30T00:09:00Z", "payload": {
            "child_id": "offline-child", "parent_turn_id": "turn-1", "provider_available": False,
        }},
    ]
    projection = build_child_activity_projection(
        events,
        parent_turn_id="turn-1",
        now=datetime(2026, 9, 30, 0, 10, tzinfo=timezone.utc),
        stale_after_seconds=300,
    )
    items = {item["activity_id"]: item for item in projection["items"]}
    assert items["stale-child"]["status"] == "stale"
    assert items["offline-child"]["status"] == "unavailable"
    assert all(item["status"] != "terminal" for item in items.values())
