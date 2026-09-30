from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from types import SimpleNamespace

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.web.headless_agent import CodexHeadlessBackend
from zf.web.kanban_agent_attachments import (
    MAX_KANBAN_ATTACHMENTS,
    MAX_KANBAN_ATTACHMENT_BYTES,
    MAX_KANBAN_ATTACHMENTS_TOTAL_BYTES,
    build_kanban_agent_attachment_router,
    native_image_paths,
    normalize_kanban_agent_attachments,
    store_kanban_agent_attachment,
)
from zf.web.server import create_app


def _state(tmp_path: Path) -> Path:
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "kanban.json").write_text("[]", encoding="utf-8")
    (state / "feature_list.json").write_text("[]", encoding="utf-8")
    return state


def test_attachment_limits_allow_five_full_size_files() -> None:
    assert MAX_KANBAN_ATTACHMENTS == 5
    assert MAX_KANBAN_ATTACHMENT_BYTES == 20 * 1024 * 1024
    assert MAX_KANBAN_ATTACHMENTS_TOTAL_BYTES == (
        MAX_KANBAN_ATTACHMENTS * MAX_KANBAN_ATTACHMENT_BYTES
    )


def test_attachment_sidecar_is_verified_and_tampering_is_rejected(tmp_path: Path) -> None:
    state = _state(tmp_path)
    image_bytes = b"\x89PNG\r\n\x1a\nfixture"
    descriptor = store_kanban_agent_attachment(state, {
        "filename": "terminal.png",
        "content_type": "image/png",
        "content_base64": base64.b64encode(image_bytes).decode(),
    })

    normalized = normalize_kanban_agent_attachments(state, [descriptor])
    assert normalized[0]["filename"] == "terminal.png"
    assert Path(native_image_paths(state, normalized)[0]).read_bytes() == image_bytes

    tampered = {**descriptor, "sha256": "0" * 64}
    with pytest.raises(ValueError, match="hash mismatch"):
        normalize_kanban_agent_attachments(state, [tampered])


async def test_upload_route_requires_auth_and_returns_sidecar_ref(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    app = FastAPI()

    def authorize(_action: str, **kwargs: object) -> dict[str, object] | None:
        if kwargs.get("x_zf_web_token") == "test-token":
            return None
        return {"_status_code": 403, "ok": False, "reason": "unauthorized"}

    app.include_router(build_kanban_agent_attachment_router(
        resolve_ctx=lambda _project_id: SimpleNamespace(state_dir=state),
        authorize_mutation=authorize,
    ))
    body = {
        "filename": "notes.md",
        "content_type": "text/markdown",
        "content_base64": base64.b64encode(b"# evidence\n").decode(),
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.post("/api/projects/test/kanban-agent/attachments", json=body)
        uploaded = await client.post(
            "/api/projects/test/kanban-agent/attachments",
            headers={"X-ZF-Web-Token": "test-token"},
            json=body,
        )

    assert unauthorized.status_code == 403
    assert uploaded.status_code == 201
    attachment = uploaded.json()["attachment"]
    assert attachment["kind"] == "kanban_agent_user_attachment"
    assert attachment["ref"].startswith("artifacts/kanban-agent/attachments/")
    assert (state / attachment["ref"]).read_bytes() == b"# evidence\n"


async def test_chat_action_records_refs_without_copying_attachment_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    descriptor = store_kanban_agent_attachment(state, {
        "filename": "notes.md",
        "content_type": "text/markdown",
        "content_base64": base64.b64encode(b"private attachment body").decode(),
    })
    app = create_app(
        state,
        config=ZfConfig(project=ProjectConfig(name="test", state_dir=".zf")),
        project_root=tmp_path,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/actions/chat-orchestrator",
            headers={"X-ZF-Web-Token": "test-token"},
            json={
                "message": "inspect the attachment",
                "backend": "deterministic",
                "attachment_refs": [descriptor],
            },
        )

    assert response.status_code == 202
    ledger = (state / "events.jsonl").read_text(encoding="utf-8")
    assert descriptor["ref"] in ledger
    assert "private attachment body" not in ledger


def test_codex_turn_uses_native_local_image_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capture = tmp_path / "turn.json"
    script = tmp_path / "fake_codex_image.py"
    script.write_text(
        "\n".join([
            "import json, os, sys",
            "for line in sys.stdin:",
            "    msg = json.loads(line)",
            "    method, req_id = msg.get('method'), msg.get('id')",
            "    if req_id and method == 'initialize':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{}}), flush=True)",
            "    elif req_id and method == 'thread/start':",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'threadId':'thread-1'}}), flush=True)",
            "    elif req_id and method == 'turn/start':",
            "        open(os.environ['CAPTURE_TURN'], 'w').write(json.dumps(msg['params']))",
            "        print(json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'turnId':'turn-1'}}), flush=True)",
            "        print(json.dumps({'jsonrpc':'2.0','method':'item/agentMessage/delta','params':{'delta':'ok'}}), flush=True)",
            "        print(json.dumps({'jsonrpc':'2.0','method':'turn/completed','params':{'turn':{'status':'completed'}}}), flush=True)",
        ]),
        encoding="utf-8",
    )
    image = tmp_path / "screen.png"
    image.write_bytes(b"image")
    monkeypatch.setenv("CAPTURE_TURN", str(capture))

    result = CodexHeadlessBackend(command=f"{sys.executable} {script}").run_turn(
        prompt="inspect it",
        cwd=tmp_path,
        system_prompt="",
        thread_id="zf-thread",
        provider_session_id="",
        on_session_id=lambda _: None,
        on_message=None,
        timeout_s=5,
        image_paths=[str(image)],
    )

    assert result.ok is True
    assert json.loads(capture.read_text())["input"] == [
        {"type": "text", "text": "inspect it"},
        {"type": "localImage", "path": str(image)},
    ]
