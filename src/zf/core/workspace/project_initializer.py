"""Shared Project initializer used by CLI and Web workspace flows."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import ProjectContext, resolve_project_context
from zf.core.events import ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.state.session import SessionStore
from zf.core.workspace.registry import (
    WorkspaceProject,
    WorkspaceRegistry,
    should_skip_default_register,
)
from zf.core.workspace.project_instruction_docs import (
    ProjectInstructionDocsResult,
    ensure_project_instruction_docs,
)


@dataclass(frozen=True)
class ProjectInitResult:
    ok: bool
    state_dir: Path
    context: ProjectContext
    registered_project: WorkspaceProject | None = None
    bootstrap_installed: bool = False
    instruction_docs: ProjectInstructionDocsResult = field(
        default_factory=ProjectInstructionDocsResult
    )
    feishu_channel_binding: str = ""
    feishu_channel_bootstrap: str = ""
    # Backward-compatible alias for older callers. New code should use
    # feishu_channel_binding because the default route now targets a channel.
    feishu_kanban_agent_binding: str = ""
    reason: str = ""
    # onboarding(CLI 与 Web init 入口共用,防单入口打通):
    # git_hook_status: installed | exists | no-git | skipped
    git_hook_status: str = ""
    # 建议的 project.scripts.setup 命令,空串 = 已声明或无依赖清单
    setup_suggestion: str = ""
    # 操作员备注写入 CLAUDE.md 的结果(created|appended|noop),空串 = 未提供 notes
    notes_applied: str = ""


class ProjectInitializer:
    def __init__(self, *, workspace: str = "default") -> None:
        self.workspace = workspace

    def initialize(
        self,
        *,
        cwd: Path,
        explicit_state_dir: str | Path | None = None,
        force: bool = False,
        preset: str | None = None,
        with_bootstrap: bool = False,
        with_instruction_docs: bool = True,
        with_git_hooks: bool = True,
        workspace_register: bool | None = None,
        create_root: bool = False,
        notes: str = "",
    ) -> ProjectInitResult:
        project_root = Path(cwd).resolve()
        if create_root:
            project_root.mkdir(parents=True, exist_ok=True)
        if preset:
            self._ensure_preset(project_root, preset=preset, force=force)

        context = resolve_project_context(
            cwd=project_root,
            explicit_state_dir=explicit_state_dir,
            load_config_with_explicit=True,
        )
        state_dir = context.state_dir
        if state_dir.exists() and not force:
            raise FileExistsError(
                f"{state_dir} already exists. Use --force to re-initialize."
            )

        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "memory").mkdir(exist_ok=True)
        (state_dir / "logs").mkdir(exist_ok=True)
        (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

        if force:
            (state_dir / "events.jsonl").write_text("", encoding="utf-8")
        event_log = event_log_from_project(state_dir, config=context.config)
        event_log.append(ZfEvent(type="session.started", actor="zf-cli"))

        SessionStore(state_dir / "session.yaml").create(
            project_root=str(project_root),
        )

        bootstrap_installed = False
        if with_bootstrap:
            from zf.core.bootstrap import install_bootstrap_feature

            bootstrap_installed = install_bootstrap_feature(
                state_dir,
                context.config,
                skip=False,
                overwrite=False,
            )

        instruction_docs = ProjectInstructionDocsResult()
        notes_applied = ""
        if with_instruction_docs:
            instruction_docs = ensure_project_instruction_docs(
                project_root,
                config=context.config,
                state_dir=state_dir,
            )
            # doc-125:操作员备注 → CLAUDE.md。收进共享 initializer,CLI(`zf init
            # --notes`)与 Web New Project(description)走同一路径,消除入口不对称。
            if notes.strip():
                from zf.core.profile.apply import apply_project_notes
                notes_applied = apply_project_notes(
                    project_root / "CLAUDE.md", notes, write=True,
                )["action"]

        git_hook_status = "skipped"
        if with_git_hooks:
            from zf.core.workspace.git_hooks import install_pre_commit_hook

            git_hook_status = install_pre_commit_hook(project_root)
        from zf.core.workspace.setup_suggestion import suggest_setup_script

        setup_suggestion = suggest_setup_script(project_root)

        feishu_channel_binding = ensure_feishu_kanban_agent_binding(
            project_root,
            config=context.config,
        )
        feishu_channel_bootstrap = ensure_feishu_default_channel_bootstrap(
            event_log,
            config=context.config,
        )

        registered = None
        registry = WorkspaceRegistry(workspace=self.workspace)
        # FIX-7(bizsim r4 F7):root 已注册的项目,重 init(换 state_dir/
        # 改名)必须无条件回写注册表——r4 实锚:CLI 重 init 后 hint 仍指向
        # 已删除的旧 state_dir,web 只读投影按坏 hint 读空。
        already_registered = workspace_register is not False and any(
            str(item.root) == str(project_root)
            for item in registry.list_projects()
        )
        if already_registered or self._should_register(
            context, requested=workspace_register,
        ):
            registered = registry.upsert_context(
                context,
                display_name=os.environ.get("ZF_WORKSPACE_PROJECT_DISPLAY_NAME", ""),
            )

        return ProjectInitResult(
            ok=True,
            state_dir=state_dir,
            context=context,
            registered_project=registered,
            bootstrap_installed=bootstrap_installed,
            instruction_docs=instruction_docs,
            feishu_channel_binding=feishu_channel_binding,
            feishu_channel_bootstrap=feishu_channel_bootstrap,
            feishu_kanban_agent_binding=feishu_channel_binding,
            git_hook_status=git_hook_status,
            setup_suggestion=setup_suggestion,
            notes_applied=notes_applied,
        )

    def _ensure_preset(self, project_root: Path, *, preset: str, force: bool) -> None:
        from zf.core.config.presets import generate_preset_yaml, list_presets

        if preset not in list_presets():
            raise ConfigError(
                f"Unknown preset {preset!r}. Available: {list_presets()}"
            )
        yaml_path = project_root / "zf.yaml"
        if not yaml_path.exists() or force:
            yaml_path.write_text(
                generate_preset_yaml(preset, project_root.name),
                encoding="utf-8",
            )

    def _should_register(
        self,
        context: ProjectContext,
        *,
        requested: bool | None,
    ) -> bool:
        if requested is False:
            return False
        env = _workspace_register_env()
        if env is False:
            return False
        if env is True or requested is True:
            return _has_project_config(context)
        if should_skip_default_register(context.project_root):
            return False
        return bool(sys.stdout.isatty() and _has_project_config(context))


def _has_project_config(context: ProjectContext) -> bool:
    return context.config is not None and context.config_path.exists()


def _workspace_register_env() -> bool | None:
    raw = ""
    try:
        import os

        raw = os.environ.get("ZF_WORKSPACE_REGISTER", "").strip().lower()
    except Exception:
        raw = ""
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return None


DEFAULT_FEISHU_CHANNEL_ID = "zaofu"
DEFAULT_FEISHU_CHANNEL_NAME = "# zaofu"
DEFAULT_FEISHU_LEADER_ID = "zaofu-leader"


def _feishu_inbound_enabled(config: object | None) -> bool:
    runtime = getattr(config, "runtime", None)
    inbound = getattr(runtime, "feishu_inbound", None)
    return bool(inbound and bool(getattr(inbound, "enabled", False)))


def ensure_feishu_kanban_agent_binding(
    project_root: Path,
    *,
    config: object | None,
) -> str:
    """Scaffold the project-level Feishu inbound route at init time.

    `zf start` owns bridge process lifecycle; init owns the stable project
    binding. Only opt-in projects with runtime.feishu_inbound.enabled get this
    template, so ordinary projects are not silently wired to Feishu.
    """
    if not _feishu_inbound_enabled(config):
        return ""

    feishu_path = project_root / "feishu.yaml"
    route_key = '"${ZF_FEISHU_INBOUND_CHAT_ID:-*}"'
    leader_openid_key = '"${ZF_LEADER_FEISHU_OPENID:-__zaofu_leader_openid_unset__}"'
    pm_openid_key = '"${ZF_PM_FEISHU_OPENID:-__zf_pm_openid_unset__}"'
    pm_route_key = '"${ZF_PM_FEISHU_INBOUND_CHAT_ID:-__zf_pm_chat_unset__}"'
    kanban_app_route_key = '"${FEISHU_KANBAN:-__zf_kanban_app_unset__}:${ZF_FEISHU_INBOUND_CHAT_ID:-*}"'
    run_manager_app_route_key = '"${FEISHU_RUNM:-__zf_runm_app_unset__}:${ZF_FEISHU_INBOUND_CHAT_ID:-*}"'
    identity_block = (
        "feishu_identity:\n"
        "  enabled: true\n"
        "  users:\n"
        f"    {leader_openid_key}:\n"
        f"      operator: {DEFAULT_FEISHU_LEADER_ID}\n"
        "      level: approver\n"
        f"    {pm_openid_key}:\n"
        "      operator: zf-product-manager\n"
        "      level: operator\n"
    )
    route_block = (
        "feishu_routing:\n"
        "  # Prefer app-id scoped routes from .env when multiple Feishu bots share a group.\n"
        f"  {kanban_app_route_key}:\n"
        "    target: kanban_agent\n"
        "    default_member: zf-product-manager\n"
        f"  {run_manager_app_route_key}:\n"
        "    target: run_manager\n"
        "    default_member: run-manager\n"
        "  # Set ZF_FEISHU_INBOUND_CHAT_ID to a concrete Feishu chat id for production.\n"
        "  # The fallback wildcard is still mention-gated by the bridge watcher in groups.\n"
        f"  {route_key}:\n"
        "    target: channel\n"
        f"    channel_id: {DEFAULT_FEISHU_CHANNEL_ID}\n"
        f"    default_member: {DEFAULT_FEISHU_LEADER_ID}\n"
        "  # Optional ZaoFu product-manager direct binding. Exact chat route wins over wildcard.\n"
        f"  {pm_route_key}:\n"
        "    target: kanban_agent\n"
        "    default_member: zf-product-manager\n"
    )

    if not feishu_path.exists():
        feishu_path.write_text(
            "# Feishu adapter config for this ZaoFu project.\n"
            "# Secrets stay in .env; this file is merged into integrations.\n"
            f"{identity_block}"
            f"{route_block}",
            encoding="utf-8",
        )
        return "created"

    text = feishu_path.read_text(encoding="utf-8")
    if "feishu_routing:" in text:
        # Existing Feishu templates may be hand-curated. Do not append a second
        # top-level feishu_routing block; operators can add the PM route inside
        # the existing block when they want that binding.
        return "exists"

    suffix = "" if text.endswith("\n") else "\n"
    if "feishu_identity:" not in text:
        text = f"{text}{suffix}{identity_block}"
        suffix = "" if text.endswith("\n") else "\n"
    feishu_path.write_text(f"{text}{suffix}{route_block}", encoding="utf-8")
    return "updated"


def ensure_feishu_default_channel_bootstrap(
    event_log,
    *,
    config: object | None,
) -> str:
    """Append the default Feishu channel/member bootstrap for new state dirs."""
    if not _feishu_inbound_enabled(config):
        return ""
    channel = None
    try:
        from zf.runtime.channel_projection import project_channel

        channel = project_channel(event_log.path.parent, DEFAULT_FEISHU_CHANNEL_ID)
    except Exception:
        channel = None
    members = list((channel or {}).get("members") or [])
    leader = next(
        (
            item for item in members
            if isinstance(item, dict)
            and str(item.get("member_id") or "") == DEFAULT_FEISHU_LEADER_ID
        ),
        None,
    )
    discussion = (channel or {}).get("discussion")
    default_responder_id = ""
    if isinstance(discussion, dict):
        default_responder_id = str(discussion.get("default_responder_id") or "")
    if channel and leader and default_responder_id == DEFAULT_FEISHU_LEADER_ID:
        return "exists"

    status = "updated" if channel else "created"
    causation_id = ""
    if not channel:
        created = ZfEvent(
            type="channel.created",
            actor="zf-cli",
            correlation_id=DEFAULT_FEISHU_CHANNEL_ID,
            payload={
                "channel_id": DEFAULT_FEISHU_CHANNEL_ID,
                "name": DEFAULT_FEISHU_CHANNEL_NAME,
                "source": "zf-init",
                "created_by": "zf-cli",
                "scope": {"kind": "feishu_default_group"},
            },
        )
        event_log.append(created)
        causation_id = created.id

    if not leader:
        added = ZfEvent(
            type="channel.member.added",
            actor="zf-cli",
            correlation_id=DEFAULT_FEISHU_CHANNEL_ID,
            causation_id=causation_id,
            payload={
                "channel_id": DEFAULT_FEISHU_CHANNEL_ID,
                "member_id": DEFAULT_FEISHU_LEADER_ID,
                "persona": "ZaoFu Leader",
                "display_name": "ZaoFu Leader",
                "member_type": "owner_delegate",
                "channel_role": "owner_delegate",
                "visibility_profile": "owner_report",
                "permission_profile": "project_writer",
                "permissions": [
                    "read",
                    "message",
                    "summarize",
                    "propose_workflow",
                    "read_reports",
                    "report_owner",
                ],
                "source": "zf-init",
                "reason": "default Feishu group leader",
            },
        )
        event_log.append(added)
        causation_id = added.id

    if default_responder_id != DEFAULT_FEISHU_LEADER_ID:
        mode = ZfEvent(
            type="channel.discussion.mode.set",
            actor="zf-cli",
            correlation_id=DEFAULT_FEISHU_CHANNEL_ID,
            causation_id=causation_id,
            payload={
                "channel_id": DEFAULT_FEISHU_CHANNEL_ID,
                "mode": "leader_delegation",
                "default_responder_id": DEFAULT_FEISHU_LEADER_ID,
                "source": "zf-init",
            },
        )
        event_log.append(mode)
    return status
