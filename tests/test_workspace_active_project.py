"""chat-e2e F1: the global workspace registry's last_opened_at must not steer
a server with its own default project — fresh sessions land on the project
the server was started for."""
from __future__ import annotations

from zf.web.projections.common import _active_workspace_project_id


def _item(project_id: str, opened: str) -> dict:
    return {"project_id": project_id, "last_opened_at": opened}


def test_server_default_wins_over_global_recency():
    items = [
        _item("project-other", "2026-07-03T10:00:00+00:00"),  # another server just opened this
        _item("project-mine", "2026-07-01T00:00:00+00:00"),
    ]
    assert _active_workspace_project_id(items, default_project_id="project-mine") == "project-mine"


def test_workspace_only_mode_keeps_recency():
    items = [
        _item("project-a", "2026-07-01T00:00:00+00:00"),
        _item("project-b", "2026-07-02T00:00:00+00:00"),
    ]
    assert _active_workspace_project_id(items, default_project_id="") == "project-b"


def test_workspace_only_mode_falls_back_to_first_item():
    items = [_item("project-a", ""), _item("project-b", "")]
    assert _active_workspace_project_id(items, default_project_id="") == "project-a"
    assert _active_workspace_project_id([], default_project_id="") == ""
