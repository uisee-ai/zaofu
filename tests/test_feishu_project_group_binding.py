"""Project Feishu collaboration-group binding lifecycle tests."""

from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.config.project_context import ProjectContext
from zf.core.workspace.registry import WorkspaceRegistry
from zf.cli.main import main
from zf.integrations.feishu import project_group_binding
from zf.integrations.feishu.project_group_binding import (
    ProjectFeishuGroupBindingStore,
    ensure_project_feishu_group_binding,
)
from zf.integrations.feishu.transport import FeishuTransportError

_REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeChatAdmin:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.ensured: list[dict] = []

    def create_group(self, **kwargs):
        self.created.append(kwargs)
        return {"chat_id": "oc_project", "name": kwargs["name"]}

    def ensure_members(self, chat_id, *, owner_open_id, bot_app_ids):
        self.ensured.append({
            "chat_id": chat_id,
            "owner_open_id": owner_open_id,
            "bot_app_ids": list(bot_app_ids),
        })
        return {
            "members": {
                "users": {owner_open_id},
                "bots": set(bot_app_ids),
            },
            "verified": True,
            "missing_users": [],
            "missing_bots": [],
        }


class FailingChatAdmin:
    def __init__(self, error: str, *, create_succeeds: bool = False) -> None:
        self.error = error
        self.create_succeeds = create_succeeds

    def create_group(self, **_kwargs):
        if not self.create_succeeds:
            raise FeishuTransportError(self.error)
        return {"chat_id": "oc_project", "name": "project"}

    def ensure_members(self, *_args, **_kwargs):
        raise FeishuTransportError(self.error)


def _context(root: Path, *, auto_provision: bool = False) -> ProjectContext:
    root.mkdir(parents=True)
    (root / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: binding-test\n"
        "  state_dir: runtime\n"
        "integrations:\n"
        "  feishu_project_group:\n"
        "    enabled: true\n"
        f"    auto_provision: {'true' if auto_provision else 'false'}\n"
        "    owner_open_id_env: ZF_TEST_OWNER_OPEN_ID\n"
        "    bot_purposes: [kanban_agent, run_manager]\n",
        encoding="utf-8",
    )
    config = load_config(root / "zf.yaml")
    state_dir = root / "runtime"
    state_dir.mkdir()
    return ProjectContext(
        project_root=root,
        config_path=root / "zf.yaml",
        config=config,
        state_dir=state_dir,
    )


def _env() -> dict[str, str]:
    return {
        "ZF_TEST_OWNER_OPEN_ID": "ou_owner",
        "FEISHU_KANBAN": "cli_kanban",
        "FEISHU_KANBAN_SECRET": "kanban-secret",
        "FEISHU_RUNM": "cli_runm",
        "FEISHU_RUNM_SECRET": "runm-secret",
    }


def test_env_template_includes_project_group_owner_identity() -> None:
    template = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ZF_FEISHU_PROVISIONER_OWNER_OPEN_ID=" in template


def test_binding_is_pending_without_explicit_external_provision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    context = _context(tmp_path / "project")
    project = WorkspaceRegistry(workspace="team").upsert_context(context)

    binding = ensure_project_feishu_group_binding(
        context,
        workspace_id="team",
        project_id=project.project_id,
        env=_env(),
    )

    assert binding is not None
    assert binding.status == "pending"
    assert binding.chat_id == ""
    stored = ProjectFeishuGroupBindingStore(context.state_dir).get(
        "project-collaboration"
    )
    assert stored == binding
    events = (context.state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "feishu.project_group.binding.requested" in events


def test_binding_provision_creates_group_verifies_members_and_indexes_route(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    context = _context(tmp_path / "project")
    registry = WorkspaceRegistry(workspace="team")
    project = registry.upsert_context(context)
    fake = FakeChatAdmin()

    binding = ensure_project_feishu_group_binding(
        context,
        workspace_id="team",
        project_id=project.project_id,
        provision=True,
        env=_env(),
        client_factory=lambda _credential: fake,
    )

    assert binding is not None
    assert binding.status == "active"
    assert binding.chat_id == "oc_project"
    assert [bot.membership_status for bot in binding.bots] == ["active", "active"]
    assert fake.created[0]["owner_open_id"] == "ou_owner"
    assert set(fake.ensured[0]["bot_app_ids"]) == {"cli_kanban", "cli_runm"}
    index = (registry.path.parent / "feishu_route_index.json").read_text(
        encoding="utf-8"
    )
    assert "cli_kanban:oc_project" in index
    assert "cli_runm:oc_project" in index


def test_binding_honors_explicit_auto_provision_opt_in(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    context = _context(tmp_path / "project", auto_provision=True)
    project = WorkspaceRegistry(workspace="team").upsert_context(context)
    fake = FakeChatAdmin()

    binding = ensure_project_feishu_group_binding(
        context,
        workspace_id="team",
        project_id=project.project_id,
        env=_env(),
        client_factory=lambda _credential: fake,
    )

    assert binding is not None and binding.status == "active"
    assert len(fake.created) == 1


def test_binding_missing_credentials_is_durable_repair_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    context = _context(tmp_path / "project")
    project = WorkspaceRegistry(workspace="team").upsert_context(context)

    binding = ensure_project_feishu_group_binding(
        context,
        workspace_id="team",
        project_id=project.project_id,
        provision=True,
        env={"ZF_TEST_OWNER_OPEN_ID": "ou_owner"},
    )

    assert binding is not None
    assert binding.status == "repair_required"
    assert "missing bot credentials" in binding.error
    events = (context.state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "feishu.project_group.repair_required" in events


def test_binding_cross_app_owner_error_is_actionable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    context = _context(tmp_path / "project")
    project = WorkspaceRegistry(workspace="team").upsert_context(context)

    binding = ensure_project_feishu_group_binding(
        context,
        workspace_id="team",
        project_id=project.project_id,
        provision=True,
        env=_env(),
        client_factory=lambda _credential: FailingChatAdmin("open_id cross app"),
    )

    assert binding is not None
    assert binding.status == "repair_required"
    assert "owner open_id is not visible to provisioner run_manager" in binding.error
    assert "ZF_TEST_OWNER_OPEN_ID" in binding.error


def test_binding_scope_error_is_actionable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    context = _context(tmp_path / "project")
    project = WorkspaceRegistry(workspace="team").upsert_context(context)

    binding = ensure_project_feishu_group_binding(
        context,
        workspace_id="team",
        project_id=project.project_id,
        provision=True,
        env=_env(),
        client_factory=lambda _credential: FailingChatAdmin(
            "app_scope_not_applied",
            create_succeeds=True,
        ),
    )

    assert binding is not None
    assert binding.status == "repair_required"
    assert "required Feishu group scopes" in binding.error
    assert "im:chat.members:read" in binding.error


def test_group_cli_status_is_wired_and_provision_requires_confirmation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n  name: binding-cli\n  state_dir: runtime\n"
        "integrations:\n"
        "  feishu_project_group:\n"
        "    enabled: true\n"
        "    owner_open_id_env: ZF_TEST_OWNER_OPEN_ID\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)

    assert main(["init", "--workspace", "team"]) == 0
    assert main(["feishu", "group", "status"]) == 0
    assert "project-collaboration" in capsys.readouterr().out
    assert main(["feishu", "group", "provision"]) == 2


def test_group_cli_provision_preserves_initialized_workspace_when_omitted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    for name in (
        "ZF_TEST_OWNER_OPEN_ID",
        "FEISHU_KANBAN",
        "FEISHU_KANBAN_SECRET",
        "FEISHU_RUNM",
        "FEISHU_RUNM_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "project"
    root.mkdir()
    (root / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n  name: binding-cli-workspace\n  state_dir: runtime\n"
        "integrations:\n"
        "  feishu_project_group:\n"
        "    enabled: true\n"
        "    owner_open_id_env: ZF_TEST_OWNER_OPEN_ID\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)

    assert main(["init", "--workspace", "team"]) == 0
    # The missing credentials deliberately stop before any real Feishu write.
    assert main(["feishu", "group", "provision", "--confirm"]) == 1

    binding = ProjectFeishuGroupBindingStore(root / "runtime").get(
        "project-collaboration"
    )
    assert binding is not None
    assert binding.workspace_id == "team"
    assert binding.status == "repair_required"
    assert WorkspaceRegistry(workspace="team").get(binding.project_id) is not None
    assert WorkspaceRegistry(workspace="default").get(binding.project_id) is None


def test_group_cli_loads_project_dotenv_before_real_provision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    for name in (
        "ZF_TEST_OWNER_OPEN_ID",
        "FEISHU_KANBAN",
        "FEISHU_KANBAN_SECRET",
        "FEISHU_RUNM",
        "FEISHU_RUNM_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "project"
    root.mkdir()
    (root / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n  name: binding-cli-dotenv\n  state_dir: runtime\n"
        "integrations:\n"
        "  feishu_project_group:\n"
        "    enabled: true\n"
        "    owner_open_id_env: ZF_TEST_OWNER_OPEN_ID\n"
        "    bot_purposes: [kanban_agent, run_manager]\n",
        encoding="utf-8",
    )
    (root / ".env").write_text(
        "ZF_TEST_OWNER_OPEN_ID=ou_owner\n"
        "FEISHU_KANBAN=cli_kanban\n"
        "FEISHU_KANBAN_SECRET=kanban-secret\n"
        "FEISHU_RUNM=cli_runm\n"
        "FEISHU_RUNM_SECRET=runm-secret\n",
        encoding="utf-8",
    )
    fake = FakeChatAdmin()
    monkeypatch.setattr(
        project_group_binding,
        "_default_chat_client",
        lambda _credential: fake,
    )
    monkeypatch.chdir(root)

    assert main(["init", "--workspace", "team"]) == 0
    assert main(["feishu", "group", "provision", "--confirm"]) == 0
    binding = ProjectFeishuGroupBindingStore(root / "runtime").get(
        "project-collaboration"
    )
    assert binding is not None and binding.status == "active"
    assert binding.chat_id == "oc_project"
    assert len(fake.created) == 1


def test_init_auto_provisions_group_and_builds_workspace_route_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    for name, value in _env().items():
        monkeypatch.setenv(name, value)
    root = tmp_path / "project"
    root.mkdir()
    (root / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n  name: binding-cli-auto\n  state_dir: runtime\n"
        "integrations:\n"
        "  feishu_project_group:\n"
        "    enabled: true\n"
        "    auto_provision: true\n"
        "    owner_open_id_env: ZF_TEST_OWNER_OPEN_ID\n"
        "    bot_purposes: [kanban_agent, run_manager]\n",
        encoding="utf-8",
    )
    fake = FakeChatAdmin()
    monkeypatch.setattr(
        project_group_binding,
        "_default_chat_client",
        lambda _credential: fake,
    )
    monkeypatch.chdir(root)

    assert main(["init", "--workspace", "team"]) == 0

    binding = ProjectFeishuGroupBindingStore(root / "runtime").get(
        "project-collaboration"
    )
    assert binding is not None
    assert binding.workspace_id == "team"
    assert binding.status == "active"
    assert binding.chat_id == "oc_project"
    assert len(fake.created) == 1
    route_index = (
        tmp_path / "workspace-home" / "workspaces" / "team" / "feishu_route_index.json"
    ).read_text(encoding="utf-8")
    assert "cli_kanban:oc_project" in route_index
    assert "cli_runm:oc_project" in route_index
