from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from zf.core.config.project_context import resolve_project_context
from zf.core.config.schema import (
    OrchestratorConfig,
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    RuntimeWebTerminalConfig,
    ZfConfig,
)
from zf.core.workspace import stable_project_id
from zf.core.workspace.registry import WorkspaceRegistry
from zf.web.terminal_backend import (
    HerdrProjectRuntime,
    HerdrTerminalResource,
    TerminalBridgeSpec,
    TerminalCapability,
)
from zf.web.terminal_registry import REGISTRY_FILENAME
from zf.web.terminal_routes import (
    _same_origin,
    build_terminal_router,
    terminal_project_providers,
)
from zf.web.terminal_service import TerminalService
from zf.web.server import create_app


def _config(**overrides: object) -> RuntimeWebTerminalConfig:
    values = {
        "enabled": True,
        "max_frame_bytes": 64 * 1024,
        "bridge_queue_bytes": 128 * 1024,
    }
    values.update(overrides)
    return RuntimeWebTerminalConfig(**values)


def test_terminal_project_providers_follow_effective_single_and_mixed_backends() -> None:
    single = ZfConfig(
        orchestrator=OrchestratorConfig(backend="codex-headless"),
        roles=[RoleConfig(name="developer", backend="codex")],
    )
    mixed = ZfConfig(
        orchestrator=OrchestratorConfig(backend="codex"),
        roles=[
            RoleConfig(name="developer", backend="codex"),
            RoleConfig(name="reviewer", backend="claude-headless"),
        ],
    )
    unsupported = ZfConfig(
        orchestrator=OrchestratorConfig(backend="python"),
        roles=[RoleConfig(name="developer", backend="custom-provider")],
    )

    assert terminal_project_providers(single) == ("codex",)
    assert terminal_project_providers(mixed) == ("codex", "claude-code")
    assert terminal_project_providers(unsupported) == ()


class RouteBackend:
    def __init__(self) -> None:
        self.created = 0
        self.existing: set[str] = set()
        self.renamed: list[tuple[str, str]] = []
        self.bridges: list[dict[str, object]] = []

    def probe(self) -> TerminalCapability:
        return TerminalCapability(
            available=True,
            binary="/fake/herdr",
            version="0.8.0",
            schema_available=True,
            observe_bridge=True,
            control_bridge=True,
            tab_rename=True,
        )

    def list_agents(self, runtime):
        return []

    def ensure_project_runtime(self, session_name: str) -> HerdrProjectRuntime:
        return HerdrProjectRuntime(session_name=session_name, server_pid=123)

    def create_terminal(
        self,
        *,
        runtime: HerdrProjectRuntime,
        workspace_id: str,
        project_root: Path,
        label: str,
        agent_name: str,
        provider_kind: str,
        provider_args: tuple[str, ...],
        start_timeout_seconds: int,
    ) -> HerdrTerminalResource:
        del runtime, project_root, label, provider_kind, provider_args
        del start_timeout_seconds
        self.created += 1
        tab_id = f"tab-{self.created}"
        self.existing.add(tab_id)
        return HerdrTerminalResource(
            workspace_id=workspace_id or "workspace-1",
            tab_id=tab_id,
            pane_id=f"pane-{self.created}",
            terminal_id=f"terminal-{self.created}",
            agent_name=agent_name,
        )

    def terminal_exists(self, *, runtime: HerdrProjectRuntime, tab_id: str) -> bool:
        del runtime
        return tab_id in self.existing

    def stop_terminal(self, *, runtime: HerdrProjectRuntime, tab_id: str) -> None:
        del runtime
        self.existing.discard(tab_id)

    def rename_terminal(
        self,
        *,
        runtime: HerdrProjectRuntime,
        tab_id: str,
        title: str,
    ) -> None:
        del runtime
        self.renamed.append((tab_id, title))

    def bridge_spec(
        self,
        *,
        runtime: HerdrProjectRuntime,
        target: str,
        mode: str,
        takeover: bool,
        cols: int,
        rows: int,
    ) -> TerminalBridgeSpec:
        self.bridges.append(
            {
                "session_name": runtime.session_name,
                "target": target,
                "mode": mode,
                "takeover": takeover,
                "cols": cols,
                "rows": rows,
            }
        )
        return TerminalBridgeSpec(("/bin/true",), mode, cols, rows)


def _service(tmp_path: Path, backend: RouteBackend | None = None) -> TerminalService:
    return TerminalService(
        project_id="project-a",
        project_root=tmp_path,
        state_dir=tmp_path / "runtime-state",
        config=_config(),
        allowed_providers=("claude-code", "codex"),
        backend=backend or RouteBackend(),
        usage_binding_wait_seconds=0,
    )


def _router_client(tmp_path: Path) -> tuple[TestClient, TerminalService]:
    backend = RouteBackend()
    service = _service(tmp_path, backend)
    service.create_session(provider="codex", slot="primary")
    ctx = SimpleNamespace(
        state_dir=service.state_dir,
        project_root=service.project_root,
        config=ZfConfig(runtime=RuntimeConfig(web_terminal=service.config)),
    )

    def authorize(
        action: str,
        *,
        authorization: str | None,
        x_zf_web_token: str | None,
        web_session_token: str | None,
    ) -> dict[str, object] | None:
        del action, authorization, web_session_token
        if x_zf_web_token == "secret":
            return None
        return {"_status_code": 403, "ok": False, "status": "unauthorized"}

    app = FastAPI()
    app.include_router(
        build_terminal_router(
            resolve_ctx=lambda project_id: ctx,
            authorize_mutation=authorize,
            service_factory=lambda project_id, resolved: service,
        )
    )
    return TestClient(app), service


def test_terminal_routes_require_auth_and_issue_in_memory_ticket(tmp_path: Path) -> None:
    client, service = _router_client(tmp_path)
    session_id = service.list_sessions()["sessions"][0]["session_id"]

    denied = client.post(
        f"/api/projects/project-a/terminal-sessions/{session_id}/attachments",
        json={"mode": "observe"},
    )
    issued = client.post(
        f"/api/projects/project-a/terminal-sessions/{session_id}/attachments",
        headers={"x-zf-web-token": "secret"},
        json={"mode": "observe", "cols": 100, "rows": 30},
    )
    invalid_geometry = client.post(
        f"/api/projects/project-a/terminal-sessions/{session_id}/attachments",
        headers={"x-zf-web-token": "secret"},
        json={"mode": "observe", "cols": True, "rows": 30},
    )

    assert denied.status_code == 403
    assert issued.status_code == 200
    assert invalid_geometry.status_code == 422
    assert issued.json()["subprotocol"] == "zf-terminal-v1"
    assert "ticket=" not in issued.text


def test_terminal_read_projection_includes_usage_without_native_identity(
    tmp_path: Path,
) -> None:
    client, service = _router_client(tmp_path)
    session_id = service.list_sessions()["sessions"][0]["session_id"]

    response = client.get(
        f"/api/projects/project-a/terminal-sessions/{session_id}"
    )

    assert response.status_code == 200
    session = response.json()["session"]
    assert session["usage"]["schema_version"] == "terminal-usage.v1"
    assert session["usage"]["status"] == "awaiting_usage"
    assert "provider_session_id" not in session
    assert "provider_session_path" not in session


def test_terminal_websocket_same_origin_includes_scheme() -> None:
    config = _config()
    ws = SimpleNamespace(
        headers={"origin": "https://zaofu.example", "host": "zaofu.example"},
        url=SimpleNamespace(scheme="ws"),
    )

    assert _same_origin(ws, config) is False
    assert _same_origin(
        ws,
        _config(allowed_origins=["https://zaofu.example"]),
    ) is True


def test_terminal_takeover_requires_auth_and_writes_coarse_receipt(tmp_path: Path) -> None:
    client, service = _router_client(tmp_path)
    session_id = service.list_sessions()["sessions"][0]["session_id"]

    denied = client.post(
        f"/api/projects/project-a/terminal-sessions/{session_id}/takeover",
        json={"cols": 120, "rows": 40},
    )
    issued = client.post(
        f"/api/projects/project-a/terminal-sessions/{session_id}/takeover",
        headers={"x-zf-web-token": "secret"},
        json={"cols": 120, "rows": 40},
    )

    registry = json.loads((service.state_dir / REGISTRY_FILENAME).read_text(encoding="utf-8"))
    backend = service.backend
    assert denied.status_code == 403
    assert issued.status_code == 200
    assert isinstance(backend, RouteBackend)
    assert backend.bridges[-1]["takeover"] is True
    assert registry["receipts"][-1]["action"] == "takeover"


def test_terminal_rename_requires_auth_and_writes_coarse_receipt(tmp_path: Path) -> None:
    client, service = _router_client(tmp_path)
    record = service.get_session(service.list_sessions()["sessions"][0]["session_id"])

    denied = client.post(
        f"/api/projects/project-a/terminal-sessions/{record.session_id}/rename",
        json={"title": "Review API"},
    )
    allowed = client.post(
        f"/api/projects/project-a/terminal-sessions/{record.session_id}/rename",
        headers={"x-zf-web-token": "secret"},
        json={"title": "Review API"},
    )

    registry = json.loads((service.state_dir / REGISTRY_FILENAME).read_text(encoding="utf-8"))
    backend = service.backend
    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["session"]["title"] == "Review API"
    assert isinstance(backend, RouteBackend)
    assert backend.renamed == [(record.tab_id, "Review API")]
    assert registry["receipts"][-1]["action"] == "rename"


def test_terminal_attachment_limit_uses_canonical_project_scope(tmp_path: Path) -> None:
    client, service = _router_client(tmp_path)
    service.config.max_attachments_per_session = 1
    session_id = service.list_sessions()["sessions"][0]["session_id"]

    default_alias = client.post(
        f"/api/projects/default/terminal-sessions/{session_id}/attachments",
        headers={"x-zf-web-token": "secret"},
        json={"mode": "observe"},
    )
    canonical = client.post(
        f"/api/projects/project-a/terminal-sessions/{session_id}/attachments",
        headers={"x-zf-web-token": "secret"},
        json={"mode": "observe"},
    )

    assert default_alias.status_code == 200
    assert default_alias.json()["receipt"]["project_id"] == "project-a"
    assert canonical.status_code == 429


def test_terminal_reconcile_mutation_requires_auth(tmp_path: Path) -> None:
    client, service = _router_client(tmp_path)
    record = service.get_session(service.list_sessions()["sessions"][0]["session_id"])
    backend = service.backend
    assert isinstance(backend, RouteBackend)
    backend.existing.remove(record.tab_id)

    denied = client.post("/api/projects/project-a/terminal-sessions/reconcile")
    allowed = client.post(
        "/api/projects/project-a/terminal-sessions/reconcile",
        headers={"x-zf-web-token": "secret"},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["sessions"][0]["state"] == "missing"


def test_terminal_routes_reject_query_capabilities(tmp_path: Path) -> None:
    client, service = _router_client(tmp_path)
    session_id = service.list_sessions()["sessions"][0]["session_id"]

    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/api/projects/project-a/terminal-sessions/{session_id}/observe?token=secret",
            headers={"origin": "http://testserver"},
        ):
            pass


def test_create_app_registers_default_enabled_terminal_route_without_touching_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    response = client.get("/api/projects/default/terminal-sessions")

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["capability"]["available"] is False
    assert not (state_dir / REGISTRY_FILENAME).exists()


def test_create_app_honors_explicitly_disabled_terminal_host_policy(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        runtime=RuntimeConfig(
            web_terminal=RuntimeWebTerminalConfig(enabled=False),
        ),
    )
    client = TestClient(create_app(state_dir, config=config, project_root=tmp_path))

    response = client.get("/api/projects/default/terminal-sessions")

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert not (state_dir / REGISTRY_FILENAME).exists()


def test_host_terminal_policy_applies_to_authorized_target_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_root = tmp_path / "target"
    target_root.mkdir()
    target_state = target_root / ".zf"
    target_config = ZfConfig(
        project=ProjectConfig(name="target", state_dir=".zf"),
        orchestrator=OrchestratorConfig(backend="claude-code"),
        roles=[RoleConfig(name="developer", backend="claude-code")],
        runtime=RuntimeConfig(
            web_terminal=RuntimeWebTerminalConfig(
                enabled=False,
            )
        ),
    )
    target_id = stable_project_id(name="target", root=target_root)
    target_context = SimpleNamespace(
        state_dir=target_state,
        project_root=target_root,
        config=target_config,
    )
    fake_binary = Path(__file__).parent / "fixtures" / "fake-herdr"
    monkeypatch.setenv("ZF_FAKE_HERDR_STATE_DIR", str(tmp_path / "fake-herdr"))
    host_config = _config(herdr_binary=str(fake_binary))

    def authorize(
        action: str,
        *,
        authorization: str | None,
        x_zf_web_token: str | None,
        web_session_token: str | None,
    ) -> dict[str, object] | None:
        del action, authorization, web_session_token
        if x_zf_web_token == "long-lived-token":
            return None
        return {"_status_code": 403, "ok": False, "status": "unauthorized"}

    app = FastAPI()
    app.include_router(build_terminal_router(
        resolve_ctx=lambda project_id: target_context,
        authorize_mutation=authorize,
        host_config=host_config,
    ))
    client = TestClient(app)

    projection = client.get(f"/api/projects/{target_id}/terminal-sessions")
    denied = client.post(
        f"/api/projects/{target_id}/terminal-sessions",
        json={"provider": "codex", "slot": "primary"},
    )
    rejected = client.post(
        f"/api/projects/{target_id}/terminal-sessions",
        headers={"x-zf-web-token": "long-lived-token"},
        json={"provider": "codex", "slot": "primary"},
    )
    created = client.post(
        f"/api/projects/{target_id}/terminal-sessions",
        headers={"x-zf-web-token": "long-lived-token"},
        json={"provider": "claude-code", "slot": "primary"},
    )

    assert projection.status_code == 200
    assert projection.json()["enabled"] is True
    assert projection.json()["allowed_providers"] == ["claude-code"]
    assert denied.status_code == 403
    assert rejected.status_code == 422
    assert created.status_code == 200
    assert created.json()["session"]["project_id"] == target_id
    assert (target_state / REGISTRY_FILENAME).exists()


def test_create_app_host_policy_serves_every_registered_token_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "long-lived-token")
    fake_binary = Path(__file__).parent / "fixtures" / "fake-herdr"
    monkeypatch.setenv("ZF_FAKE_HERDR_STATE_DIR", str(tmp_path / "fake-herdr"))

    host_root = tmp_path / "host"
    host_root.mkdir()
    host_state = host_root / ".zf"
    host_state.mkdir()
    host_config = ZfConfig(
        project=ProjectConfig(name="host", state_dir=".zf"),
        runtime=RuntimeConfig(
            web_terminal=_config(
                herdr_binary=str(fake_binary),
            )
        ),
    )

    target_root = tmp_path / "target"
    target_root.mkdir()
    (target_root / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n  name: target\n  state_dir: .zf\n"
        "orchestrator:\n  backend: codex\n",
        encoding="utf-8",
    )
    target_state = target_root / ".zf"
    target_state.mkdir()
    (target_state / "events.jsonl").write_text("", encoding="utf-8")
    (target_state / "kanban.json").write_text("[]\n", encoding="utf-8")
    (target_state / "feature_list.json").write_text("[]\n", encoding="utf-8")
    target = WorkspaceRegistry().upsert_context(resolve_project_context(cwd=target_root))
    client = TestClient(create_app(host_state, config=host_config, project_root=host_root))

    projection = client.get(f"/api/projects/{target.project_id}/terminal-sessions")
    denied = client.post(
        f"/api/projects/{target.project_id}/terminal-sessions",
        json={"provider": "codex", "slot": "primary"},
    )
    created = client.post(
        f"/api/projects/{target.project_id}/terminal-sessions",
        headers={"x-zf-web-token": "long-lived-token"},
        json={"provider": "codex", "slot": "primary"},
    )
    unknown = client.get("/api/projects/not-registered/terminal-sessions")

    assert projection.status_code == 200
    assert projection.json()["enabled"] is True
    assert projection.json()["allowed_providers"] == ["codex"]
    assert denied.status_code == 403
    assert created.status_code == 200
    assert created.json()["session"]["project_id"] == target.project_id
    assert unknown.status_code == 404
    assert (target_state / REGISTRY_FILENAME).exists()
    assert not (host_state / REGISTRY_FILENAME).exists()


def test_default_project_alias_and_canonical_id_share_terminal_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    fake_binary = Path(__file__).parent / "fixtures" / "fake-herdr"
    monkeypatch.setenv("ZF_FAKE_HERDR_STATE_DIR", str(tmp_path / "fake-herdr"))
    monkeypatch.setenv("ZF_WEB_TRUSTED_SESSION", "1")
    terminal_config = _config(herdr_binary=str(fake_binary))
    config = ZfConfig(
        project=ProjectConfig(name="demo", state_dir="state"),
        orchestrator=OrchestratorConfig(backend="codex"),
        runtime=RuntimeConfig(web_terminal=terminal_config),
    )
    client = TestClient(create_app(state_dir, config=config, project_root=tmp_path))
    canonical_id = stable_project_id(name="demo", root=tmp_path)

    created = client.post(
        f"/api/projects/{canonical_id}/terminal-sessions",
        json={"provider": "codex", "slot": "primary"},
    )
    default_view = client.get("/api/projects/default/terminal-sessions")

    assert created.status_code == 200
    assert default_view.status_code == 200
    assert default_view.json()["sessions"][0]["session_id"] == created.json()["session"]["session_id"]


def test_managed_terminal_websocket_uses_verified_origin_with_private_upstream_host(tmp_path, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    from zf.integrations.controller.access import ManagedAccessMiddleware
    from zf.web import terminal_routes

    client, service = _router_client(tmp_path)
    origin = "https://workspace.example.test"
    class Verifier:
        def verify(self, token, method, path):
            assert token == "signed-fixture"
            return {"origin": origin}
        def current(self, claims):
            assert claims["origin"] == origin
    client.app.add_middleware(ManagedAccessMiddleware,
        config=SimpleNamespace(gateway_origin=origin), verifier=Verifier())
    async def relay(websocket, **kwargs):
        await websocket.send_text("fixture-frame")
    monkeypatch.setattr(terminal_routes, "relay_terminal_websocket", relay)
    sid = service.list_sessions()["sessions"][0]["session_id"]
    prefix = f"/api/projects/project-a/terminal-sessions/{sid}"
    headers = {"origin": origin, "x-zf-access-context": "signed-fixture", "x-zf-web-token": "secret"}
    ticket = client.post(prefix + "/attachments", json={"mode": "observe"}, headers=headers).json()
    protocols = [ticket["subprotocol"], ticket["ticket"]]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(prefix + "/observe", subprotocols=protocols,
                headers={**headers, "origin": "https://attacker.example", "x-forwarded-host": "workspace.example.test"}):
            pass
    with client.websocket_connect(prefix + "/observe", subprotocols=protocols, headers=headers) as socket:
        assert socket.accepted_subprotocol == "zf-terminal-v1"
        assert socket.receive_text() == "fixture-frame"


def test_unverified_forwarded_origin_does_not_relax_standalone_terminal_policy():
    ws = SimpleNamespace(headers={"origin": "https://workspace.example.test", "host": "private.service:8443",
        "x-forwarded-host": "workspace.example.test", "x-zf-access-context": "unverified"},
        url=SimpleNamespace(scheme="wss"), scope={"zf.access": {"origin": "https://workspace.example.test"}})
    assert _same_origin(ws, _config()) is False
