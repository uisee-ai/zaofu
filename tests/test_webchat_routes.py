from __future__ import annotations

import base64
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.events import EventLog, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.web.agent_conversation_store import AgentConversationStore
from zf.web.agent_artifact_store import AgentArtifactStore
from zf.web.agent_turn_store import AgentTurnStore
from zf.web.server import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "kanban.json").write_text("[]", encoding="utf-8")
    (state / "feature_list.json").write_text("[]", encoding="utf-8")
    EventLog(state / "events.jsonl").append(ZfEvent(type="loop.started", actor="test"))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    return TestClient(create_app(state, project_root=tmp_path)), state


def test_runtime_route_is_wired_and_read_only(client: tuple[TestClient, Path]) -> None:
    web, state = client
    store = AgentTurnStore(state)
    turn = store.submit(
        project_id="default", conversation_id="c", thread_id="main",
        backend="codex-headless", permission_profile="read_only",
        idempotency_key="turn-1",
    )[0]
    response = web.get("/api/projects/default/webchat/runtime?conversation_id=c&thread_id=main")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "webchat-runtime.v1"
    assert body["rollout"]["mode"] == "durable"
    assert body["messages"] == []
    assert body["rollout"]["store_schema"] == "agent-webchat-runtime.v1"
    assert body["turns"][0]["turn_id"] == turn["turn_id"]
    assert body["capabilities"]["completion_authority"] is False


def test_runtime_returns_project_scoped_control_receipts(
    client: tuple[TestClient, Path],
) -> None:
    web, state = client
    store = AgentTurnStore(state)
    local = store.submit(
        project_id="default", conversation_id="c", thread_id="main",
        backend="codex-headless", permission_profile="read_only",
        idempotency_key="local-control-turn",
    )[0]
    foreign = store.submit(
        project_id="other", conversation_id="c", thread_id="main",
        backend="codex-headless", permission_profile="read_only",
        idempotency_key="foreign-control-turn",
    )[0]
    local_receipt = store.record_control(
        operation="steer", turn_id=local["turn_id"],
        idempotency_key="local-steer", status="queued_fallback",
        fallback="queue", payload={"queue_id": "queue-local"},
    )[0]
    store.record_control(
        operation="cancel", turn_id=foreign["turn_id"],
        idempotency_key="foreign-cancel", status="accepted",
    )

    response = web.get(
        "/api/projects/default/webchat/runtime?conversation_id=c&thread_id=main"
    )

    assert response.status_code == 200
    assert response.json()["control_receipts"] == [local_receipt]


def test_mutations_require_token_and_queue_survives_new_client(client: tuple[TestClient, Path]) -> None:
    web, state = client
    denied = web.post("/api/projects/default/webchat/queue", json={
        "conversation_id": "c", "thread_id": "main", "message": "next",
        "idempotency_key": "queue-1",
    })
    assert denied.status_code == 403
    accepted = web.post(
        "/api/projects/default/webchat/queue",
        headers={"x-zf-web-token": "test-token"},
        json={
            "conversation_id": "c", "thread_key": "main", "message": "next",
            "idempotency_key": "queue-1",
        },
    )
    assert accepted.status_code == 201
    assert accepted.json()["item"]["thread_id"] == "main"
    reloaded = TestClient(create_app(state, project_root=state.parent))
    runtime = reloaded.get("/api/projects/default/webchat/runtime?conversation_id=c&thread_id=main").json()
    assert runtime["queue"][0]["message"] == "next"
    cancelled = reloaded.delete(
        f"/api/projects/default/webchat/queue/{accepted.json()['item']['queue_id']}",
        headers={"x-zf-web-token": "test-token"},
    )
    assert cancelled.status_code == 200
    refreshed = reloaded.get(
        "/api/projects/default/webchat/runtime?conversation_id=c&thread_id=main"
    ).json()
    assert refreshed["queue"][0]["state"] == "cancelled"


def test_queue_rejects_missing_thread_scope(client: tuple[TestClient, Path]) -> None:
    web, _ = client
    response = web.post(
        "/api/projects/default/webchat/queue",
        headers={"x-zf-web-token": "test-token"},
        json={
            "conversation_id": "c", "message": "orphaned",
            "idempotency_key": "queue-without-thread",
        },
    )
    assert response.status_code == 400
    assert "thread_id is required" in response.json()["detail"]


def test_interaction_answer_route_is_revision_bound(client: tuple[TestClient, Path]) -> None:
    web, state = client
    turns = AgentTurnStore(state)
    turn = turns.submit(
        project_id="default", conversation_id="c", thread_id="main",
        backend="codex-headless", permission_profile="read_only",
        idempotency_key="turn-question",
    )[0]
    interaction, _ = turns.request_interaction(
        turn_id=turn["turn_id"], kind="ask_user", interaction_id="question-1",
        request={"question": "Choose", "options": ["a", "b"]}, revision=2,
    )
    stale = web.post(
        "/api/projects/default/webchat/interactions/question-1/answer",
        headers={"x-zf-web-token": "test-token"},
        json={"revision": 1, "response": {"answer": "a"}},
    )
    assert stale.status_code == 409
    answered = web.post(
        "/api/projects/default/webchat/interactions/question-1/answer",
        headers={"x-zf-web-token": "test-token"},
        json={"revision": interaction["revision"], "response": {"answer": "a"}},
    )
    assert answered.status_code == 200
    assert answered.json()["interaction"]["status"] == "answered"


def test_interaction_answer_can_resolve_without_provider_resume(
    client: tuple[TestClient, Path],
) -> None:
    web, state = client
    turns = AgentTurnStore(state)
    turn = turns.submit(
        project_id="default", conversation_id="c", thread_id="main",
        backend="codex-headless", permission_profile="read_only",
        idempotency_key="turn-direct-apply",
    )[0]
    interaction, _ = turns.request_interaction(
        turn_id=turn["turn_id"], kind="ask_user",
        interaction_id="question-direct-apply",
        request={"question": "Apply?", "options": ["yes", "no"]},
    )
    answered = web.post(
        "/api/projects/default/webchat/interactions/question-direct-apply/answer",
        headers={"x-zf-web-token": "test-token"},
        json={
            "revision": interaction["revision"],
            "response": {"answer": "yes", "applied_action": "channel-create"},
            "resume": False,
        },
    )
    assert answered.status_code == 200
    assert answered.json()["resume"]["status"] == "resolved_without_resume"
    assert turns.get(turn["turn_id"])["state"] == "completed"


def test_runtime_and_interaction_answer_are_project_scoped(client: tuple[TestClient, Path]) -> None:
    web, state = client
    turns = AgentTurnStore(state)
    foreign = turns.submit(
        project_id="other", conversation_id="c", thread_id="main",
        backend="codex-headless", permission_profile="read_only",
        idempotency_key="foreign-turn",
    )[0]
    turns.request_interaction(
        turn_id=foreign["turn_id"], kind="ask_user", interaction_id="foreign-question",
        request={"question": "private"},
    )
    runtime = web.get(
        "/api/projects/default/webchat/runtime?conversation_id=c&thread_id=main"
    ).json()
    assert runtime["turns"] == []
    assert runtime["interactions"] == []
    denied = web.post(
        "/api/projects/default/webchat/interactions/foreign-question/answer",
        headers={"x-zf-web-token": "test-token"},
        json={"revision": 1, "response": {"answer": "leak"}},
    )
    assert denied.status_code == 404


def test_resource_ids_cannot_cross_project_routes(client: tuple[TestClient, Path]) -> None:
    web, state = client
    conversations = AgentConversationStore(state)
    foreign_message = conversations.add_message(
        project_id="other", conversation_id="foreign", thread_id="foreign-thread",
        role="user", origin="test", content="private",
    )[0]
    corrected = web.post(
        f"/api/projects/default/webchat/messages/{foreign_message['message_id']}/correct",
        headers={"x-zf-web-token": "test-token"}, json={"content": "crossed"},
    )
    assert corrected.status_code == 404

    artifact = AgentArtifactStore(state).capture_bytes(
        project_id="other", logical_name="private.txt", data=b"private",
        media_type="text/plain", producer={"agent_id": "kanban-agent"},
    )[0]
    assert web.get(
        f"/api/projects/default/webchat/artifacts/{artifact['version_id']}?principal=kanban-agent"
    ).status_code == 404

    turns = AgentTurnStore(state)
    foreign_turn = turns.submit(
        project_id="other", conversation_id="foreign", thread_id="foreign-thread",
        backend="codex-headless", permission_profile="workspace_writer",
        idempotency_key="foreign-tool-turn",
    )[0]
    foreign_claim = turns.claim(foreign_turn["turn_id"], owner="test")
    tool = web.post(
        "/api/projects/default/webchat/tools/evaluate",
        headers={"x-zf-web-token": "test-token"},
        json={
            "turn_id": foreign_turn["turn_id"], "attempt_id": foreign_claim.attempt_id,
            "tool": "workspace.edit", "args": {"path": "a.py"},
        },
    )
    assert tool.status_code == 404


def test_conversation_correction_search_fork_and_export_routes(client: tuple[TestClient, Path]) -> None:
    web, state = client
    source = AgentConversationStore(state).add_message(
        project_id="default", conversation_id="c", thread_id="main",
        role="user", origin="web", content="original request",
    )[0]
    corrected = web.post(
        f"/api/projects/default/webchat/messages/{source['message_id']}/correct",
        headers={"x-zf-web-token": "test-token"}, json={"content": "corrected request"},
    )
    assert corrected.status_code == 200
    assert corrected.json()["message"]["supersedes_message_id"] == source["message_id"]
    forked = web.post(
        f"/api/projects/default/webchat/messages/{source['message_id']}/fork",
        headers={"x-zf-web-token": "test-token"}, json={"title": "Alternative"},
    )
    assert forked.status_code == 200
    searched = web.get("/api/projects/default/webchat/conversations/search?q=corrected")
    assert searched.json()["items"][0]["message_id"] == corrected.json()["message"]["message_id"]
    exported = web.get("/api/projects/default/webchat/conversations/c/threads/main/export?format=markdown")
    assert exported.status_code == 200
    assert "corrected request" in exported.text


def test_artifact_publish_and_adopt_are_separate_exact_digest_actions(client: tuple[TestClient, Path]) -> None:
    web, state = client
    uploaded = web.post(
        "/api/projects/default/webchat/artifacts",
        headers={"x-zf-web-token": "test-token"},
        json={
            "logical_name": "plan.md", "media_type": "application/octet-stream",
            "data_base64": base64.b64encode(b"# exact plan").decode("ascii"),
            "lineage": {"conversation_id": "c", "turn_id": "turn-1"},
        },
    )
    assert uploaded.status_code == 201
    artifact = uploaded.json()["artifact"]
    assert artifact["media_type"] == "text/markdown"
    detail = web.get(f"/api/projects/default/webchat/artifacts/{artifact['version_id']}")
    assert detail.status_code == 200
    assert detail.json()["preview"] == "# exact plan"
    kanban_before = (state / "kanban.json").read_bytes()
    published = web.post(
        f"/api/projects/default/webchat/artifacts/{artifact['version_id']}/publish",
        headers={"x-zf-web-token": "test-token"},
        json={"digest": artifact["digest"], "principal": "kanban-agent"},
    )
    assert published.status_code == 200
    assert published.json()["artifact"]["visibility"] == "published"
    assert (state / "kanban.json").read_bytes() == kanban_before
    TaskStore(state / "kanban.json").add(Task(
        id="TASK-ADOPT-EXACT", title="Adopt exact", contract_authority_revision="authority-r1",
    ))
    unsupported = web.post(
        f"/api/projects/default/webchat/artifacts/{artifact['version_id']}/adopt",
        headers={"x-zf-web-token": "test-token"},
        json={"digest": artifact["digest"], "target": {"kind": "plan", "revision": 1}},
    )
    assert unsupported.status_code == 409
    target = {
        "kind": "task_input", "task_id": "TASK-ADOPT-EXACT",
        "contract_authority_revision": "authority-r1",
    }
    stale = web.post(
        f"/api/projects/default/webchat/artifacts/{artifact['version_id']}/adopt",
        headers={"x-zf-web-token": "test-token"},
        json={"digest": "sha256:stale", "target": target},
    )
    assert stale.status_code == 409
    adopted = web.post(
        f"/api/projects/default/webchat/artifacts/{artifact['version_id']}/adopt",
        headers={"x-zf-web-token": "test-token"},
        json={"digest": artifact["digest"], "target": target},
    )
    assert adopted.status_code == 200
    assert adopted.json()["artifact"]["visibility"] == "adopted"
    assert adopted.json()["controlled"] is True


def test_task_artifact_adoption_requires_exact_authority_revision(
    client: tuple[TestClient, Path],
) -> None:
    web, state = client
    TaskStore(state / "kanban.json").add(Task(
        id="TASK-ADOPT", title="Adopt", contract_authority_revision="authority-r1",
    ))
    artifact = AgentArtifactStore(state).capture_bytes(
        project_id="default", logical_name="delivery/result.md", data=b"result",
        media_type="text/markdown", producer={"agent_id": "kanban-agent"},
    )[0]
    AgentArtifactStore(state).publish(
        artifact["version_id"], expected_digest=artifact["digest"],
        principal="kanban-agent", project_id="default",
    )
    target = web.get(
        "/api/projects/default/webchat/adoption-targets/tasks/TASK-ADOPT",
    )
    assert target.status_code == 200
    assert target.json()["contract_authority_revision"] == "authority-r1"
    missing = web.post(
        f"/api/projects/default/webchat/artifacts/{artifact['version_id']}/adopt",
        headers={"x-zf-web-token": "test-token"},
        json={
            "digest": artifact["digest"],
            "target": {"kind": "task_input", "task_id": "TASK-ADOPT"},
        },
    )
    assert missing.status_code == 409
    stale = web.post(
        f"/api/projects/default/webchat/artifacts/{artifact['version_id']}/adopt",
        headers={"x-zf-web-token": "test-token"},
        json={
            "digest": artifact["digest"],
            "target": {
                "kind": "task_input", "task_id": "TASK-ADOPT",
                "contract_authority_revision": "authority-stale",
            },
        },
    )
    assert stale.status_code == 409
    adopted = web.post(
        f"/api/projects/default/webchat/artifacts/{artifact['version_id']}/adopt",
        headers={"x-zf-web-token": "test-token"},
        json={"digest": artifact["digest"], "target": target.json()},
    )
    assert adopted.status_code == 200
    assert adopted.json()["artifact"]["adopted_target"]["contract_authority_revision"] == "authority-r1"


def test_large_artifact_detail_is_bounded_but_download_remains_complete(
    client: tuple[TestClient, Path],
) -> None:
    web, state = client
    data = ("line of evidence\n" * 20_000).encode("utf-8")
    artifact = AgentArtifactStore(state).capture_bytes(
        project_id="default", logical_name="delivery/large.txt", data=data,
        media_type="text/plain", producer={"agent_id": "kanban-agent"},
    )[0]
    detail = web.get(
        f"/api/projects/default/webchat/artifacts/{artifact['version_id']}",
    )
    assert detail.status_code == 200
    assert detail.json()["preview_truncated"] is True
    assert len(detail.json()["preview"].encode("utf-8")) <= 256 * 1024
    content = web.get(
        f"/api/projects/default/webchat/artifacts/{artifact['version_id']}/content",
    )
    assert content.status_code == 200
    assert content.content == data


def test_tool_and_profile_routes_are_wired(client: tuple[TestClient, Path]) -> None:
    web, state = client
    turns = AgentTurnStore(state)
    turn = turns.submit(
        project_id="default", conversation_id="c", thread_id="main",
        backend="codex-headless", permission_profile="workspace_writer",
        idempotency_key="turn-tool",
    )[0]
    claim = turns.claim(turn["turn_id"], owner="test")
    decision = web.post(
        "/api/projects/default/webchat/tools/evaluate",
        headers={"x-zf-web-token": "test-token"},
        json={
            "turn_id": turn["turn_id"], "attempt_id": claim.attempt_id,
            "tool": "workspace.edit", "args": {"path": "src/a.py"},
            "surface": "worker", "unattended": True,
            "policy": {"workspace.edit": "allow"},
        },
    )
    assert decision.status_code == 200
    assert decision.json()["decision"] == "ask"
    catalog = web.get("/api/projects/default/webchat/profiles/catalog")
    assert catalog.status_code == 200
    assert "codex-headless" in catalog.json()["providers"]


def test_profile_routes_preview_apply_reload_and_reject_secret(
    client: tuple[TestClient, Path],
) -> None:
    web, state = client
    profile = {
        "role": "operator",
        "provider": "codex-headless",
        "permission_profile": "read_only",
        "skills": [],
        "tools": ["task.read", "artifact.read"],
        "eligibility": {"ephemeral": True, "channel": False, "delivery": False},
        "path_policy": {"allow": ["src"], "deny": [".env"]},
        "budget": {"max_tokens": 10_000},
    }
    proposed = web.post(
        "/api/projects/default/webchat/profiles/kanban-agent/proposals",
        headers={"x-zf-web-token": "test-token"},
        json={"base_profile": profile, "profile": profile},
    )
    assert proposed.status_code == 200
    proposal = proposed.json()["proposal"]
    assert proposal["validation"]["valid"] is True
    assert isinstance(proposal["diff"], str)
    applied = web.post(
        f"/api/projects/default/webchat/profiles/proposals/{proposal['proposal_id']}/apply",
        headers={"x-zf-web-token": "test-token"},
        json={
            "base_digest": proposal["base_digest"],
            "proposal_digest": proposal["proposal_digest"],
        },
    )
    assert applied.status_code == 200
    catalog = web.get("/api/projects/default/webchat/profiles/catalog").json()
    assert catalog["current_revision"] == 1
    assert catalog["current_profile"] == profile
    runtime = web.get(
        "/api/projects/default/webchat/runtime?conversation_id=c&thread_id=main",
    ).json()
    assert runtime["agent_profile"]["revision"] == 1

    same_profile = web.post(
        "/api/projects/default/webchat/profiles/kanban-agent/proposals",
        headers={"x-zf-web-token": "test-token"},
        json={"base_profile": profile, "profile": profile},
    )
    assert same_profile.status_code == 200
    same_proposal = same_profile.json()["proposal"]
    reapplied = web.post(
        f"/api/projects/default/webchat/profiles/proposals/{same_proposal['proposal_id']}/apply",
        headers={"x-zf-web-token": "test-token"},
        json={
            "base_digest": same_proposal["base_digest"],
            "proposal_digest": same_proposal["proposal_digest"],
        },
    )
    assert reapplied.status_code == 200
    assert reapplied.json()["revision"]["revision"] == 1

    rejected = web.post(
        "/api/projects/default/webchat/profiles/kanban-agent/proposals",
        headers={"x-zf-web-token": "test-token"},
        json={
            "base_profile": profile,
            "profile": {**profile, "nested": {"api_token": "must-not-persist"}},
        },
    )
    assert rejected.status_code == 409
    assert "secret fields" in rejected.json()["detail"]
    artifacts = AgentArtifactStore(state).list_versions(
        project_id="default", principal="agent-profile-builder",
    )
    assert len(artifacts) == 1
