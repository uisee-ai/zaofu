"""Workspace-level exact Feishu project-group routing tests."""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.cli.main import main
from zf.core.config.project_context import ProjectContext
from zf.core.workspace.feishu_binding_index import (
    ProviderFeishuBindingConflict,
    ProviderFeishuInboundResolver,
    WorkspaceFeishuBindingConflict,
    WorkspaceFeishuBindingIndex,
    WorkspaceFeishuInboundResolver,
)
from zf.core.workspace.registry import WorkspaceRegistry
from zf.integrations.feishu.bridge_watch import BridgeWatch
from zf.integrations.feishu.project_group_binding import (
    FeishuProjectGroupBotBinding,
    ProjectFeishuGroupBinding,
    ProjectFeishuGroupBindingStore,
)
from zf.integrations.feishu.transport import MockFeishuTransport


def _context(root: Path, name: str) -> ProjectContext:
    root.mkdir(parents=True)
    (root / "zf.yaml").write_text(
        'version: "1.0"\n'
        f"project:\n  name: {name}\n  state_dir: runtime\n",
        encoding="utf-8",
    )
    state_dir = root / "runtime"
    state_dir.mkdir()
    return ProjectContext(
        project_root=root,
        config_path=root / "zf.yaml",
        config=load_config(root / "zf.yaml"),
        state_dir=state_dir,
    )


def _active_binding(
    project_id: str,
    *,
    chat_id: str,
    app_id: str,
    workspace_id: str = "team",
) -> ProjectFeishuGroupBinding:
    return ProjectFeishuGroupBinding(
        binding_id="project-collaboration",
        workspace_id=workspace_id,
        project_id=project_id,
        group_kind="collaboration",
        display_name="ZaoFu · test",
        status="active",
        chat_id=chat_id,
        owner_open_id="ou_owner",
        owner_open_id_env="ZF_OWNER",
        provisioner_purpose="kanban_agent",
        primary_responder="kanban_agent",
        channel_id="zaofu",
        bots=(FeishuProjectGroupBotBinding(
            purpose="kanban_agent",
            app_id=app_id,
            target="kanban_agent",
            default_member="zf-product-manager",
            membership_status="active",
        ),),
        config_digest="digest",
        created_at="now",
        updated_at="now",
    )


def test_workspace_index_routes_exact_app_and_chat_to_own_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    registry = WorkspaceRegistry(workspace="team")
    context_a = _context(tmp_path / "project-a", "alpha")
    context_b = _context(tmp_path / "project-b", "beta")
    project_a = registry.upsert_context(context_a)
    project_b = registry.upsert_context(context_b)
    ProjectFeishuGroupBindingStore(context_a.state_dir).upsert(
        _active_binding(project_a.project_id, chat_id="oc_a", app_id="cli_shared")
    )
    ProjectFeishuGroupBindingStore(context_b.state_dir).upsert(
        _active_binding(project_b.project_id, chat_id="oc_b", app_id="cli_shared")
    )

    index = WorkspaceFeishuBindingIndex(registry).rebuild()
    assert set(index) == {"cli_shared:oc_a", "cli_shared:oc_b"}
    resolved = WorkspaceFeishuInboundResolver(registry=registry).resolve(
        app_id="cli_shared", chat_id="oc_b"
    )

    assert resolved is not None
    assert resolved.binding.project_id == project_b.project_id
    assert resolved.context.project_root == context_b.project_root
    assert resolved.route.target == "kanban_agent"


def test_workspace_index_fails_closed_on_duplicate_app_chat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    registry = WorkspaceRegistry(workspace="team")
    context_a = _context(tmp_path / "project-a", "alpha")
    context_b = _context(tmp_path / "project-b", "beta")
    project_a = registry.upsert_context(context_a)
    project_b = registry.upsert_context(context_b)
    ProjectFeishuGroupBindingStore(context_a.state_dir).upsert(
        _active_binding(project_a.project_id, chat_id="oc_same", app_id="cli_same")
    )
    ProjectFeishuGroupBindingStore(context_b.state_dir).upsert(
        _active_binding(project_b.project_id, chat_id="oc_same", app_id="cli_same")
    )

    with pytest.raises(WorkspaceFeishuBindingConflict, match="duplicate active"):
        WorkspaceFeishuBindingIndex(registry).rebuild()


def test_provider_index_routes_one_bot_across_different_workspaces(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    team = WorkspaceRegistry(workspace="team")
    product = WorkspaceRegistry(workspace="product")
    context_a = _context(tmp_path / "project-a", "alpha")
    context_b = _context(tmp_path / "project-b", "beta")
    project_a = team.upsert_context(context_a)
    project_b = product.upsert_context(context_b)
    ProjectFeishuGroupBindingStore(context_a.state_dir).upsert(
        _active_binding(
            project_a.project_id,
            chat_id="oc_team",
            app_id="cli_shared",
            workspace_id="team",
        )
    )
    ProjectFeishuGroupBindingStore(context_b.state_dir).upsert(
        _active_binding(
            project_b.project_id,
            chat_id="oc_product",
            app_id="cli_shared",
            workspace_id="product",
        )
    )

    resolver = ProviderFeishuInboundResolver()
    routes = resolver.refresh()
    resolved = resolver.resolve(app_id="cli_shared", chat_id="oc_product")

    assert set(routes) == {"cli_shared:oc_team", "cli_shared:oc_product"}
    assert resolved is not None
    assert resolved.binding.workspace_id == "product"
    assert resolved.context.project_root == context_b.project_root


def test_provider_index_fails_closed_on_cross_workspace_duplicate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    team = WorkspaceRegistry(workspace="team")
    product = WorkspaceRegistry(workspace="product")
    context_a = _context(tmp_path / "project-a", "alpha")
    context_b = _context(tmp_path / "project-b", "beta")
    project_a = team.upsert_context(context_a)
    project_b = product.upsert_context(context_b)
    ProjectFeishuGroupBindingStore(context_a.state_dir).upsert(
        _active_binding(
            project_a.project_id,
            chat_id="oc_same",
            app_id="cli_shared",
            workspace_id="team",
        )
    )
    ProjectFeishuGroupBindingStore(context_b.state_dir).upsert(
        _active_binding(
            project_b.project_id,
            chat_id="oc_same",
            app_id="cli_shared",
            workspace_id="product",
        )
    )

    with pytest.raises(ProviderFeishuBindingConflict, match="duplicate active"):
        ProviderFeishuInboundResolver().refresh()


def test_bridge_watch_dispatches_with_workspace_selected_context() -> None:
    selected_context = object()
    selected_route = object()
    selected_binding = type("Binding", (), {"binding_id": "binding-1"})()
    selected_index_route = type("Route", (), {"purpose": "kanban_agent"})()
    resolution = type("Resolution", (), {
        "context": selected_context,
        "route": selected_route,
        "binding": selected_binding,
        "index_route": selected_index_route,
    })()
    submitted: list[tuple[object, object]] = []

    def dispatch(event, *, context, transport):
        submitted.append((event, context))
        future = concurrent.futures.Future()
        future.set_result({"status": "routed"})
        return future

    bridge = BridgeWatch(
        object(),
        MockFeishuTransport(),
        dispatch=dispatch,
        inbound_resolver=lambda _event: resolution,
    )
    bridge._on_flush("oc_group", [{
        "text": "hello",
        "message_id": "om_1",
        "chat_id": "oc_group",
        "user_id": "ou_owner",
        "app_id": "cli_app",
    }])

    assert submitted[0][1] is selected_context
    assert getattr(submitted[0][0], "route") is selected_route
    assert getattr(submitted[0][0], "feishu_binding_id") == "binding-1"


def test_workspace_bridge_cli_uses_exact_binding_for_one_shot_event(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    monkeypatch.chdir(tmp_path)
    registry = WorkspaceRegistry(workspace="team")
    context = _context(tmp_path / "project", "alpha")
    project = registry.upsert_context(context)
    ProjectFeishuGroupBindingStore(context.state_dir).upsert(
        _active_binding(project.project_id, chat_id="oc_group", app_id="cli_app")
    )
    received = {}

    def fake_bridge(event, *, context, transport=None):
        assert transport is not None
        received["context"] = context
        received["route"] = event.route
        return {"status": "replied", "kind": "test"}

    monkeypatch.setattr("zf.cli.feishu_consume.bridge_inbound_message", fake_bridge)
    rc = main([
        "feishu",
        "bridge",
        "--workspace",
        "team",
        "--app-id",
        "cli_app",
        "--event-json",
        json.dumps({
            "type": "message",
            "payload": {"text": "status", "message_id": "om_1", "app_id": "cli_app"},
            "user_id": "ou_owner",
            "chat_id": "oc_group",
        }),
    ])

    assert rc == 0
    assert received["context"].project_root == context.project_root
    assert received["route"].target == "kanban_agent"
    assert json.loads(capsys.readouterr().out.strip())["kind"] == "test"


def test_provider_bridge_cli_routes_to_the_right_workspace_for_one_shot_event(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    monkeypatch.chdir(tmp_path)
    team = WorkspaceRegistry(workspace="team")
    product = WorkspaceRegistry(workspace="product")
    context_a = _context(tmp_path / "project-a", "alpha")
    context_b = _context(tmp_path / "project-b", "beta")
    project_a = team.upsert_context(context_a)
    project_b = product.upsert_context(context_b)
    ProjectFeishuGroupBindingStore(context_a.state_dir).upsert(
        _active_binding(
            project_a.project_id,
            chat_id="oc_team",
            app_id="cli_shared",
            workspace_id="team",
        )
    )
    ProjectFeishuGroupBindingStore(context_b.state_dir).upsert(
        _active_binding(
            project_b.project_id,
            chat_id="oc_product",
            app_id="cli_shared",
            workspace_id="product",
        )
    )
    received = {}

    def fake_bridge(event, *, context, transport=None):
        assert transport is not None
        received["context"] = context
        received["route"] = event.route
        return {"status": "replied", "kind": "provider-test"}

    monkeypatch.setattr("zf.cli.feishu_consume.bridge_inbound_message", fake_bridge)
    rc = main([
        "feishu",
        "bridge",
        "--all-workspaces",
        "--app-id",
        "cli_shared",
        "--event-json",
        json.dumps({
            "type": "message",
            "payload": {
                "text": "status",
                "message_id": "om_1",
                "app_id": "cli_shared",
            },
            "user_id": "ou_owner",
            "chat_id": "oc_product",
        }),
    ])

    assert rc == 0
    assert received["context"].project_root == context_b.project_root
    assert received["route"].target == "kanban_agent"
    assert json.loads(capsys.readouterr().out.strip())["kind"] == "provider-test"
