"""Runner-side policy helpers for workflow roles."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

from zf.core.config.schema import ConstraintsConfig, RoleConfig, ZfConfig


PURE_AGGREGATOR_POLICY_ID = "pure_aggregator.v1"
GOAL_CLOSURE_JUDGE_POLICY_ID = "goal_closure_judge_readonly.v1"
GOAL_CLOSURE_SUCCESS_EVENT = "goal.closure.synthesized"

# R23 (synth 6h stall): the aggregator's whole job is READING child reports /
# briefings / instructions — without read tools in the allowlist every Read
# prompts interactively and a headless synth hangs forever. Read-only tools
# keep the policy's write-protection intent (no Edit/Write/bare Bash) intact.
CLAUDE_AGGREGATOR_READONLY_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep")

CLAUDE_AGGREGATOR_ALLOWED_TOOLS: tuple[str, ...] = (
    *CLAUDE_AGGREGATOR_READONLY_TOOLS,
    "Bash(zf emit *)",
    "Bash(zf events *)",
    "Bash(zf trace show *)",
    "Bash(zf artifact list *)",
    "Bash(zf artifact read *)",
    "Bash(cat .zf/artifacts/*)",
)


def claude_aggregator_allowed_tools(config: ZfConfig | None) -> tuple[str, ...]:
    """Aggregator allowlist with the artifacts path rooted at the REAL
    state_dir — the static ``.zf`` literal broke every project with a custom
    ``project.state_dir`` (state-dir-contract anti-pattern, R23)."""
    state_dir = ".zf"
    if config is not None:
        configured = str(
            getattr(getattr(config, "project", None), "state_dir", "") or ""
        ).strip().rstrip("/")
        if configured:
            state_dir = configured
    cli_command = os.environ.get("ZF_CLI_CMD", "").strip() or "zf"
    cli_tools = tuple(
        f"Bash({cli_command} {suffix} *)"
        for suffix in (
            "emit",
            "events",
            "trace show",
            "artifact list",
            "artifact read",
        )
    )
    return (
        *CLAUDE_AGGREGATOR_READONLY_TOOLS,
        *cli_tools,
        f"Bash(cat {state_dir}/artifacts/*)",
    )


def fanout_synth_role_refs(config: ZfConfig | None) -> set[str]:
    if config is None:
        return set()
    refs: set[str] = set()
    for stage in list(getattr(config.workflow, "stages", []) or []):
        aggregate = getattr(stage, "aggregate", None)
        synth_role = str(getattr(aggregate, "synth_role", "") or "").strip()
        if synth_role:
            refs.add(synth_role)
    return refs


def fanout_child_role_refs(config: ZfConfig | None) -> set[str]:
    """Roles that run as fanout children (``stage.roles``) and therefore do
    real, write-bearing per-task work."""
    if config is None:
        return set()
    refs: set[str] = set()
    for stage in list(getattr(config.workflow, "stages", []) or []):
        for ref in list(getattr(stage, "roles", []) or []):
            cleaned = str(ref or "").strip()
            if cleaned:
                refs.add(cleaned)
    return refs


def goal_closure_judge_role_refs(config: ZfConfig | None) -> set[str]:
    """Roles executing the final Thin Judge boundary."""

    if config is None:
        return set()
    refs: set[str] = set()
    for stage in list(getattr(config.workflow, "stages", []) or []):
        aggregate = getattr(stage, "aggregate", None)
        if str(getattr(aggregate, "success_event", "") or "") != GOAL_CLOSURE_SUCCESS_EVENT:
            continue
        refs.update(
            str(item).strip()
            for item in list(getattr(stage, "roles", []) or [])
            if str(item).strip()
        )
    for pipeline in list(getattr(config.workflow, "pipelines", []) or []):
        if str(getattr(pipeline, "final_success", "") or "") != GOAL_CLOSURE_SUCCESS_EVENT:
            continue
        role = str(getattr(pipeline, "final_role", "") or "").strip()
        if role:
            refs.add(role)
    return refs


def is_goal_closure_judge_role(config: ZfConfig | None, role: RoleConfig) -> bool:
    refs = goal_closure_judge_role_refs(config)
    return bool(refs and (role.name in refs or role.instance_id in refs))


def is_fanout_synth_role(config: ZfConfig | None, role: RoleConfig) -> bool:
    refs = fanout_synth_role_refs(config)
    if not refs:
        return False
    if role.name not in refs and role.instance_id not in refs:
        return False
    # 2026-06-19 e2e: a role declared as BOTH a fanout child and the synthRole
    # (e.g. issue-fanout's issue-plan) does real write-bearing planning on its
    # child pass — its child briefing tells it to Write the plan. The pane is
    # spawned once with a single --allowedTools set, so the read-only aggregator
    # narrowing would gate that child Write behind a permission prompt nothing
    # answers headless, hanging the whole fanout to timeout. Only narrow PURE
    # aggregators (synthRole that is never also a fanout child).
    child_refs = fanout_child_role_refs(config)
    if role.name in child_refs or role.instance_id in child_refs:
        return False
    return True


def pure_aggregator_policy_plan(
    config: ZfConfig | None,
    role: RoleConfig,
    *,
    state_dir: Any = None,
) -> dict[str, Any]:
    if not is_fanout_synth_role(config, role):
        return {"applies": False, "applied": False, "policy_id": ""}
    effective = apply_pure_aggregator_policy(config, role, state_dir=state_dir)
    changes = _policy_changes(role, effective)
    return {
        "applies": True,
        "applied": bool(changes),
        "policy_id": PURE_AGGREGATOR_POLICY_ID,
        "role": role.name,
        "instance_id": role.instance_id,
        "backend": role.backend,
        "changes": changes,
        "original": _role_policy_payload(role),
        "effective": _role_policy_payload(effective),
    }


def goal_closure_judge_policy_plan(
    config: ZfConfig | None,
    role: RoleConfig,
    *,
    state_dir: Any = None,
) -> dict[str, Any]:
    if not is_goal_closure_judge_role(config, role):
        return {"applies": False, "applied": False, "policy_id": ""}
    effective = _apply_readonly_role_policy(config, role, state_dir=state_dir)
    changes = _policy_changes(role, effective)
    return {
        "applies": True,
        "applied": bool(changes),
        "policy_id": GOAL_CLOSURE_JUDGE_POLICY_ID,
        "role": role.name,
        "instance_id": role.instance_id,
        "backend": role.backend,
        "changes": changes,
        "original": _role_policy_payload(role),
        "effective": _role_policy_payload(effective),
    }


def apply_pure_aggregator_policy(
    config: ZfConfig | None,
    role: RoleConfig,
    *,
    state_dir: Any = None,
) -> RoleConfig:
    if not is_fanout_synth_role(config, role):
        return role
    if str(role.backend or "") == "codex":
        # ZF-PLAN-SYNTH-HEADLESS-01 (2026-07-22 real Provider E2E): Codex
        # restricted maps to `-a untrusted -s read-only`, which prompts even
        # when the synth only reads its Kernel-issued briefing outside the
        # worktree. There is no unattended per-tool allowlist equivalent.
        # Keep the configured headless-safe mode; the role remains isolated in
        # its own worktree and pre-tool scope guards enforce the no-source-write
        # boundary. This is the same provider constraint as Thin Judge.
        return role
    return _apply_readonly_role_policy(config, role, state_dir=state_dir)


def apply_goal_closure_judge_policy(
    config: ZfConfig | None,
    role: RoleConfig,
    *,
    state_dir: Any = None,
) -> RoleConfig:
    if not is_goal_closure_judge_role(config, role):
        return role
    if str(role.backend or "") == "codex":
        # ZF-JUDGE-HEADLESS-01(07-16/17 两轮实弹):restricted 映射
        # `-a untrusted` = headless 每命令等确认必超时;default 档
        # (`-s workspace-write`)仍走 bwrap 沙箱,宿主 bwrap 不可用时
        # (loopback RTM_NEWADDR EPERM)worker 活着但双手全废。codex
        # judge 保持 profile 原档(通常 bypass,与其他角色一致);只读
        # 语义由 L0-a pre_tool_use scope 守卫 + 真相边界承担(judge
        # 工作树写入不进 candidate,事实事件有 actor/dispatch 判官)。
        return role
    return _apply_readonly_role_policy(config, role, state_dir=state_dir)


def _apply_readonly_role_policy(
    config: ZfConfig | None,
    role: RoleConfig,
    *,
    state_dir: Any = None,
) -> RoleConfig:
    backend = str(role.backend or "")
    if backend == "codex":
        constraints = role.constraints
        if constraints.allowed_paths:
            constraints = replace(
                constraints,
                allowed_paths=[],
            )
        if (
            role.permission_mode not in {"restricted", "allowlist"}
            or role.allowed_tools
            or constraints is not role.constraints
        ):
            return replace(
                role,
                permission_mode="restricted",
                allowed_tools=[],
                constraints=constraints,
            )
        return role
    if backend == "claude-code":
        default_tools = claude_aggregator_allowed_tools(config)
        allowed_tools = [
            tool
            for tool in role.allowed_tools
            if _is_allowed_claude_aggregator_tool(tool, default_tools)
        ]
        if not allowed_tools:
            allowed_tools = list(default_tools)
        else:
            # The read-only tools are load-bearing (a synth without Read
            # stalls headless on permission prompts) — always include them.
            allowed_tools = [
                *[t for t in CLAUDE_AGGREGATOR_READONLY_TOOLS if t not in allowed_tools],
                *allowed_tools,
            ]
        # R24: the tool allowlist alone is not enough — briefings/instructions/
        # child reports live in the STATE DIR, outside the synth's worktree
        # cwd, and Claude's directory-trust gate prompts on every cross-dir
        # read (a second 6h-hang class). Grant the state dir via --add-dir
        # (ClaudeCodeAdapter emits constraints.allowed_paths in allowlist mode).
        constraints = role.constraints
        if state_dir is not None:
            add_dir = str(state_dir)
            if add_dir and add_dir not in list(constraints.allowed_paths):
                constraints = replace(
                    constraints,
                    allowed_paths=[*constraints.allowed_paths, add_dir],
                )
        if (
            role.permission_mode != "allowlist"
            or allowed_tools != role.allowed_tools
            or constraints is not role.constraints
        ):
            return replace(
                role,
                permission_mode="allowlist",
                allowed_tools=allowed_tools,
                constraints=constraints,
            )
    return role


def _is_allowed_claude_aggregator_tool(
    tool: str,
    default_tools: tuple[str, ...],
) -> bool:
    normalized = " ".join(str(tool or "").strip().split())
    return (
        normalized in default_tools
        or normalized in CLAUDE_AGGREGATOR_ALLOWED_TOOLS
    )


def _policy_changes(original: RoleConfig, effective: RoleConfig) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if original.permission_mode != effective.permission_mode:
        changes["permission_mode"] = {
            "from": original.permission_mode,
            "to": effective.permission_mode,
        }
    if list(original.allowed_tools) != list(effective.allowed_tools):
        changes["allowed_tools"] = {
            "from": list(original.allowed_tools),
            "to": list(effective.allowed_tools),
        }
    if list(original.constraints.allowed_paths) != list(effective.constraints.allowed_paths):
        changes["constraints.allowed_paths"] = {
            "from": list(original.constraints.allowed_paths),
            "to": list(effective.constraints.allowed_paths),
        }
    return changes


def _role_policy_payload(role: RoleConfig) -> dict[str, Any]:
    constraints: ConstraintsConfig = role.constraints
    return {
        "permission_mode": role.permission_mode,
        "allowed_tools": list(role.allowed_tools),
        "constraints": {
            "allowed_paths": list(constraints.allowed_paths),
            "blocked_paths": list(constraints.blocked_paths),
            "max_steps": constraints.max_steps,
        },
    }
