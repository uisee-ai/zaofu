"""Default-project refresh must not mix changed configuration with old state."""

import pytest
from fastapi.testclient import TestClient

from tests.test_channel_profile_registration import setup
from zf.web.server import create_app


@pytest.mark.parametrize("fault,status", [("state_dir", 409), ("invalid", 422)])
def test_changed_default_config_fails_before_read_or_action(tmp_path, monkeypatch, fault, status):
    path, writer, service = setup(tmp_path, monkeypatch)
    writer.emit("loop.started", actor="test")
    (service.state_dir / "kanban.json").write_text("[]\n")
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(service.state_dir, config=service.config, project_root=tmp_path))
    before = len(writer.event_log.read_all())
    path.write_text(path.read_text().replace(str(tmp_path / "state"), str(tmp_path / "other-state"))
                    if fault == "state_dir" else "project: [broken")
    read = client.get("/api/projects/default/channels")
    assert read.status_code == status, read.text
    action = client.post("/api/actions/chat-orchestrator", headers={"x-zf-web-token": "test-token"},
                         json={"message": "Continue", "backend": "codex-headless"})
    assert action.status_code == status, action.text
    assert len(writer.event_log.read_all()) == before
