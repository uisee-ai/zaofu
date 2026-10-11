from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.web.agent_artifact_store import (
    AgentArtifactAccessDenied,
    AgentArtifactConflict,
    AgentArtifactNotFound,
    AgentArtifactStore,
)


def _capture(store: AgentArtifactStore, data: bytes, *, child: str = "child-a") -> dict:
    return store.capture_bytes(
        project_id="p",
        logical_name="plans/plan.md",
        data=data,
        media_type="text/markdown",
        producer={"agent_id": "kanban-agent", "child_id": child},
        lineage={"conversation_id": "c", "turn_id": "turn-parent", "parent_turn_id": "turn-parent"},
    )[0]


def test_versions_are_immutable_and_publish_does_not_touch_task_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = workspace / ".state"
    source = workspace / "plan.md"
    source.write_text("v1", encoding="utf-8")
    store = AgentArtifactStore(state, project_root=workspace)
    first = store.capture_path(source, project_id="p", logical_name="plan.md", media_type="text/markdown")[0]
    source.write_text("v2", encoding="utf-8")
    second = store.capture_path(source, project_id="p", logical_name="plan.md", media_type="text/markdown")[0]
    assert first["version_id"] != second["version_id"]
    # Path capture without a producer is private; explicitly grant the owner.
    store.grant(first["version_id"], principal="owner", capability="publish")
    _, first_bytes = store.read(first["version_id"], principal="owner")
    assert first_bytes == b"v1"
    published = store.publish(first["version_id"], expected_digest=first["digest"], principal="owner")
    assert published["visibility"] == "published"
    assert not (state / "kanban.json").exists()


def test_private_child_scope_publish_adopt_and_stale_digest(tmp_path: Path) -> None:
    store = AgentArtifactStore(tmp_path)
    item = _capture(store, b"# plan")
    with pytest.raises(AgentArtifactAccessDenied):
        store.read(item["version_id"], principal="child-b")
    assert store.read(item["version_id"], principal="turn-parent")[1] == b"# plan"
    store.grant(item["version_id"], principal="owner", capability="publish")
    with pytest.raises(AgentArtifactConflict):
        store.publish(item["version_id"], expected_digest="sha256:stale", principal="owner")
    published = store.publish(item["version_id"], expected_digest=item["digest"], principal="owner")
    adopted = store.mark_adopted(
        published["version_id"], expected_digest=published["digest"],
        target={"kind": "task_input", "task_id": "task-1", "revision": 3},
        principal="kanban-agent",
    )
    assert adopted["visibility"] == "adopted"
    assert adopted["adopted_target"]["revision"] == 3
    with pytest.raises(ValueError, match="unsupported artifact adoption target kind"):
        store.mark_adopted(
            published["version_id"], expected_digest=published["digest"],
            target={"kind": "plan", "revision": 1}, principal="kanban-agent",
        )


def test_private_expiry_and_path_traversal_fail_closed(tmp_path: Path) -> None:
    store = AgentArtifactStore(tmp_path)
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    item = store.capture_bytes(
        project_id="p", logical_name="tmp/result.txt", data=b"temporary",
        media_type="text/plain", producer={"agent_id": "a"}, expires_at=expired_at,
    )[0]
    assert store.expire_private() == [item["version_id"]]
    with pytest.raises(AgentArtifactNotFound):
        store.read(item["version_id"], principal="a")
    with pytest.raises(ValueError):
        store.capture_bytes(project_id="p", logical_name="../secret", data=b"x")


def test_compare_text_versions(tmp_path: Path) -> None:
    store = AgentArtifactStore(tmp_path)
    left = _capture(store, b"one\n")
    right = _capture(store, b"two\n")
    diff = store.compare(left["version_id"], right["version_id"], principal="turn-parent")
    assert "-one" in diff and "+two" in diff


def test_generic_upload_media_type_is_inferred_from_filename(tmp_path: Path) -> None:
    store = AgentArtifactStore(tmp_path)
    item = store.capture_bytes(
        project_id="p",
        logical_name="notes/result.md",
        data=b"# inspectable result",
        media_type="application/octet-stream",
        producer={"agent_id": "kanban-agent"},
    )[0]

    assert item["media_type"] == "text/markdown"
    assert store.read(item["version_id"], principal="kanban-agent")[1] == b"# inspectable result"


def test_media_content_and_capability_grants_fail_closed(tmp_path: Path) -> None:
    store = AgentArtifactStore(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        store.capture_bytes(
            project_id="p", logical_name="image.png", data=b"not-a-png",
            media_type="image/png",
        )
    with pytest.raises(ValueError, match="JSON artifact"):
        store.capture_bytes(
            project_id="p", logical_name="result.json", data=b"not-json",
            media_type="application/json",
        )
    item = _capture(store, b"private")
    store.grant(item["version_id"], principal="reader", capability="read")
    assert store.read(item["version_id"], principal="reader")[1] == b"private"
    with pytest.raises(AgentArtifactAccessDenied):
        store.publish(
            item["version_id"], expected_digest=item["digest"], principal="reader",
        )
