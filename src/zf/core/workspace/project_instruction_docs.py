"""Project-level instruction document scaffolding for ZaoFu init."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zf.core.agents_md import render_canonical_block, replace_managed_block
from zf.core.config.schema import ZfConfig


@dataclass(frozen=True)
class ProjectInstructionDocsResult:
    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


def ensure_project_instruction_docs(
    project_root: Path,
    *,
    config: ZfConfig | None,
    state_dir: Path,
) -> ProjectInstructionDocsResult:
    """Create or refresh root AGENTS.md / CLAUDE.md for a ZaoFu project."""
    root = Path(project_root).resolve()
    project_name = config.project.name if config is not None else root.name
    state_ref = _display_state_dir(root, state_dir)

    created: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []

    agents_path = root / "AGENTS.md"
    agents_created, agents_updated = _ensure_agents_md(
        agents_path,
        project_name=project_name,
        state_ref=state_ref,
        config=config,
    )
    if agents_created:
        created.append("AGENTS.md")
    elif agents_updated:
        updated.append("AGENTS.md")
    else:
        skipped.append("AGENTS.md")

    claude_path = root / "CLAUDE.md"
    if claude_path.exists():
        skipped.append("CLAUDE.md")
    else:
        claude_path.write_text(
            render_project_claude_md(project_name=project_name, state_ref=state_ref),
            encoding="utf-8",
        )
        created.append("CLAUDE.md")

    return ProjectInstructionDocsResult(
        created=tuple(created),
        updated=tuple(updated),
        skipped=tuple(skipped),
    )


def render_project_agents_md_shell(*, project_name: str, state_ref: str) -> str:
    """Return editable project guidance above the ZaoFu managed block."""
    return f"""# AGENTS.md

本仓库使用 ZaoFu 作为 multi-agent harness。

## Project Rules

- 项目名: `{project_name}`。
- `zf.yaml` 是唯一 ZaoFu 控制面配置。
- `project.state_dir` 当前解析为 `{state_ref}`;这是运行态目录,不是源码。
- 不要直接改写 `events.jsonl`、`kanban.json`、`session.yaml`、`feature_list.json`、`role_sessions.yaml`。
- 状态变更优先走 `zf` CLI、受控事件写入或 kernel helper。
- `events.jsonl` 记录 append-only 发生/因果/裁决引用;canonical stores 持有当前状态;
  required artifact/sidecar 持有完整语义或大证据。不要把三者互相冒充。
- Web/API/集成侧只做受控 action 或只读 projection,不要绕过 kernel 写业务状态。
- 开发、review、测试、交付报告默认使用中文,除非项目另有明确约定。

## Verification

- 修改 `zf.yaml`、运行态协议、Web/API 或 orchestration 行为后,运行对应的 focused test。
- 无法运行验证时,在交付说明里写清楚阻塞项和原计划命令。

## Harness Health Signals

- `zf validate --instructions` 通过。
- `zf update agents-md --check` 通过。
- 每个 accepted task 都有明确 verification evidence。
- event ledger、canonical stores 和 required artifacts 只能通过各自受控 writer 变更。
- long-running work 留下 heartbeat、handoff 或 recovery evidence。
"""


def render_project_claude_md(*, project_name: str, state_ref: str) -> str:
    """Return a Claude-specific bridge that points back to AGENTS.md."""
    return f"""# CLAUDE.md

本项目使用 ZaoFu 管理 multi-agent 开发流程。

## Claude Code Rules

- 开始工作前先阅读 `AGENTS.md`。
- 项目名: `{project_name}`。
- `zf.yaml` 是唯一 ZaoFu 控制面配置。
- `project.state_dir` 当前解析为 `{state_ref}`;不要把运行态文件当作源码维护。
- 不要直接写 `events.jsonl`、`kanban.json`、`session.yaml`、`feature_list.json`、`role_sessions.yaml`。
- 状态变更通过 `zf` CLI、受控事件写入或 kernel helper 完成。
- 普通交互式开发会话没有 `Active task: <task_id>` briefing 时,不要自行 emit
  task/workflow event 或 heartbeat。
- 修改代码时保持范围收敛,优先沿用项目现有模式。
- 交付前运行项目约定的测试;无法运行时说明阻塞项。
"""


def _ensure_agents_md(
    path: Path,
    *,
    project_name: str,
    state_ref: str,
    config: ZfConfig | None,
) -> tuple[bool, bool]:
    existed = path.exists()
    current = path.read_text(encoding="utf-8") if existed else ""
    base = current if existed else render_project_agents_md_shell(
        project_name=project_name,
        state_ref=state_ref,
    )
    updated = replace_managed_block(
        base,
        render_canonical_block(config=config).rstrip("\n"),
    )
    if updated == current:
        return False, False
    path.write_text(updated, encoding="utf-8")
    return (not existed), existed


def _display_state_dir(project_root: Path, state_dir: Path) -> str:
    resolved = Path(state_dir).resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)
