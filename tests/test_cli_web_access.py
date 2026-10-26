"""Dashboard launch defaults preserve controlled-action authentication."""
import argparse
import os
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from zf.cli import web
from zf.cli.web_access import configure_web_access, dashboard_launch_url
from zf.web.server import _web_mutation_auth_error, _web_session_projection


@pytest.fixture(autouse=True)
def isolate_access(tmp_path, monkeypatch):
    with patch.dict(os.environ):
        for key in ("ZF_WEB_ACTION_TOKEN", "ZF_WEB_PASSCODE", "ZF_WEB_TRUSTED_SESSION", "ZF_WEB_ACTION_TOKEN_FILE"):
            os.environ.pop(key, None)
        monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace"))
        yield


@pytest.mark.parametrize("host,read_only", [("127.0.0.1", False), ("0.0.0.0", False), ("127.0.0.1", True)])
def test_cli_launch_access_controls_real_task_creation(tmp_path, monkeypatch, capsys, host, read_only):
    import uvicorn
    from zf.core.task.store import TaskStore

    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject: {name: access-test, state_dir: runtime-state}\n')
    (tmp_path / "runtime-state").mkdir()
    monkeypatch.setattr(web, "_load_project_env", lambda _: None)
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **_: apps.append(app))
    args = argparse.Namespace(host=host, port=8002, state_dir=None, reload=False, workspace_only=False, read_only=read_only)
    assert web.run(args) == 0
    with TestClient(apps[0]) as client:
        session = client.get("/api/web-session").json()
        payload = {"title": "Write permission proof", "contract": {"behavior": "render board", "verification": "typecheck"}}
        if host == "0.0.0.0":
            assert client.post("/api/actions/create-task", json=payload).status_code == 403
        headers = {"X-ZF-Web-Token": os.environ["ZF_WEB_ACTION_TOKEN"]} if host == "0.0.0.0" else {}
        created = client.post("/api/actions/create-task", json=payload, headers=headers)
    assert created.status_code == (403 if read_only else 201), created.json()
    if read_only:
        assert session["mode"] == "read_only"
    else:
        assert TaskStore(tmp_path / "runtime-state" / "kanban.json").get(created.json()["task_id"]) is not None
        assert session["mode"] == ("local_trusted" if host == "127.0.0.1" else "token_required")
    assert f"http://{host}:8002/" in capsys.readouterr().out


def test_network_default_provisions_stable_token_and_authorized_launch_link(tmp_path):
    configure_web_access(host="0.0.0.0")
    token = os.environ["ZF_WEB_ACTION_TOKEN"]
    token_path = tmp_path / "workspace" / "web-action-token"
    assert len(token) == 64
    assert token_path.stat().st_mode & 0o777 == 0o600
    link = urlsplit(dashboard_launch_url("0.0.0.0", 8002))
    assert link.query == ""
    assert parse_qs(link.fragment)["zf-action-token"] == [token]
    assert _web_mutation_auth_error("create-task", authorization=None, x_zf_web_token=None)["status"] == "unauthorized"
    assert _web_mutation_auth_error("create-task", authorization=None, x_zf_web_token=token) is None
    del os.environ["ZF_WEB_ACTION_TOKEN"]
    configure_web_access(host="0.0.0.0")
    assert os.environ["ZF_WEB_ACTION_TOKEN"] == token


@pytest.mark.parametrize("key,value,mode", [
    ("ZF_WEB_PASSCODE", "explicit-passcode", "remote_passcode"),
    ("ZF_WEB_ACTION_TOKEN", "explicit-token", "token_required"),
    ("ZF_WEB_TRUSTED_SESSION", "0", "token_required"),
])
def test_existing_access_policy_is_preserved(monkeypatch, key, value, mode):
    monkeypatch.setenv(key, value)
    configure_web_access(host="127.0.0.1")
    assert os.environ[key] == value
    assert _web_session_projection()["mode"] == mode


def test_disabling_trusted_access_still_allows_authenticated_writes(monkeypatch):
    monkeypatch.setenv("ZF_WEB_TRUSTED_SESSION", "0")
    configure_web_access(host="127.0.0.1")
    token = os.environ["ZF_WEB_ACTION_TOKEN"]
    assert _web_mutation_auth_error("create-task", authorization=None, x_zf_web_token=None)["status"] == "unauthorized"
    assert _web_mutation_auth_error("create-task", authorization=None, x_zf_web_token=token) is None


def test_explicit_read_only_disables_all_write_credentials(monkeypatch):
    for key in ("ZF_WEB_ACTION_TOKEN", "ZF_WEB_PASSCODE", "ZF_WEB_TRUSTED_SESSION"):
        monkeypatch.setenv(key, "1")
    configure_web_access(host="0.0.0.0", read_only=True)
    assert _web_session_projection()["mode"] == "read_only"
    assert _web_mutation_auth_error("create-task", authorization=None, x_zf_web_token="1")["status"] == "disabled"
    assert "#" not in dashboard_launch_url("0.0.0.0", 8002)
