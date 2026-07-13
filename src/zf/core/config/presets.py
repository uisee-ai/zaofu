"""Config presets — minimal, code-assist, design-first templates."""

from __future__ import annotations

import yaml
from pathlib import Path


PRESETS: dict[str, dict] = {
    "safe-team": {
        "version": "1.0",
        "preset": "safe-team",
        "project": {"name": "{project_name}", "state_dir": ".zf"},
        "session": {"tmux_session": "zf"},
        "orchestrator": {"backend": "claude-code"},
        "workflow": {
            "dag": {
                "enabled": True,
                "default_gate_level": "permissive",
                "design_to_backlog_owner": "orchestrator",
                "stage_order": [
                    "task.assigned",
                    "arch.proposal.done",
                    "design.critique.done",
                    "dev.build.done",
                    "static_gate.passed",
                    "review.approved",
                    "test.passed",
                    "judge.passed",
                ],
            },
            "rework_routing": {
                "static_gate.failed": "dev",
                "review.rejected": "dev",
                "test.failed": "dev",
                "judge.failed": "dev",
                "gate.failed": "arch",
            },
        },
        "stage_labels": {
            "intake": "intake & breakdown",
            "design": "PRD / design",
            "design_critique": "design critique",
            "implement": "implementation",
            "code_review": "verify lane: code review",
            "independent_test": "verify lane: independent test",
            "judge": "terminal judge",
        },
        # quality_gates: leave required_checks empty here — it depends on
        # the project. Bare `pytest` / `mypy` / `ruff` would silently fail
        # if the project has no test config, no type stubs, or no linter
        # rules. Show concrete examples in examples/safe-team.yaml.
        "quality_gates": {
            "static": {"enabled": True, "required_checks": []},
        },
        "roles": [
            # Layer 2 — orchestrator agent (LLM, stream-json, decision maker)
            {
                "name": "orchestrator",
                "backend": "claude-code",
                "transport": "stream-json",
                "permission_mode": "allowlist",
                "allowed_tools": [
                    "Bash(zf feature *)",
                    "Bash(zf kanban *)",
                    "Bash(zf emit *)",
                    "Bash(zf events *)",
                    "Bash(zf status)",
                    "Read",
                ],
                "stages": ["meta"],
                "triggers": [
                    "user.message",
                    "design.critique.done",
                    "clarification.needed",
                    "dev.blocked",
                    "review.rejected",
                    "test.failed",
                    "judge.failed",
                    # doc 78 W2: kernel sweep emits this when a candidate failure
                    # is plan-level (slice overlap / spec mismatch / phase gate);
                    # the orchestrator wakes to RE-DECOMPOSE the task_map rather
                    # than re-implement the same slices.
                    "orchestrator.replan_requested",
                    "task.contract.invalid",
                    "dispatch.silent_stall",
                    "worker.stuck",
                ],
                "publishes": ["task.dispatched", "task.created", "feature.created"],
            },
            # Layer 3 — worker agents (tmux by default)
            {
                "name": "arch",
                "backend": "claude-code",
                "role_kind": "reader",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
                "stages": ["design"],
                "triggers": ["task.assigned"],
                "publishes": ["arch.proposal.done", "clarification.needed"],
            },
            {
                "name": "critic",
                "backend": "claude-code",
                "role_kind": "reader",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
                "stages": ["design_critique"],
                "triggers": ["arch.proposal.done"],
                "publishes": ["design.critique.done", "gate.failed"],
            },
            {
                "name": "dev",
                "backend": "claude-code",
                "role_kind": "writer",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
                "replicas": 2,
                "autoscale": {
                    "enabled": True,
                    "min_replicas": 2,
                    "max_replicas": 4,
                    "target_ready_tasks_per_worker": 1,
                    "scale_up_pending_seconds": 120,
                    "scale_down_idle_seconds": 900,
                    "cooldown_seconds": 180,
                    "drain_before_stop": True,
                },
                "stages": ["implement"],
                "triggers": ["task.assigned"],
                "publishes": ["dev.build.done", "dev.blocked"],
            },
            {
                "name": "review",
                "backend": "claude-code",
                "role_kind": "reader",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
                "autoscale": {
                    "enabled": True,
                    "min_replicas": 1,
                    "max_replicas": 2,
                    "target_ready_tasks_per_worker": 1,
                    "scale_up_pending_seconds": 180,
                    "scale_down_idle_seconds": 900,
                    "cooldown_seconds": 180,
                    "drain_before_stop": True,
                },
                "stages": ["code_review"],
                "triggers": ["static_gate.passed"],
                "publishes": ["review.approved", "review.rejected"],
            },
            {
                "name": "test",
                "backend": "claude-code",
                "role_kind": "reader",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
                "replicas": 2,
                "stages": ["independent_test"],
                "triggers": ["review.approved"],
                "publishes": ["test.passed", "test.failed"],
            },
            {
                "name": "judge",
                "backend": "claude-code",
                "role_kind": "reader",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
                "stages": ["judge"],
                "triggers": ["test.passed"],
                "publishes": ["judge.passed", "judge.failed"],
            },
        ],
    },
    "safe-local": {
        "version": "1.0",
        "preset": "safe-local",
        "project": {"name": "{project_name}", "state_dir": ".zf"},
        "session": {"tmux_session": "zf"},
        "orchestrator": {"backend": "claude-code"},
        "stage_labels": {
            "intake": "intake & breakdown",
            "implement": "implementation",
            "code_review": "verify lane: code review",
            "independent_test": "verify lane: independent test",
        },
        "quality_gates": {
            "static": {"enabled": True, "required_checks": []},
        },
        "roles": [
            {"name": "dev", "backend": "claude-code",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
             "stages": ["implement"], "triggers": ["task.assigned"],
             "publishes": ["dev.build.done"]},
        ],
    },
    "minimal": {
        "version": "1.0",
        "preset": "minimal",
        "project": {"name": "{project_name}", "state_dir": ".zf"},
        "session": {"tmux_session": "zf"},
        "orchestrator": {"backend": "claude-code"},
        "roles": [
            {"name": "dev", "backend": "claude-code",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
             "stages": ["implement"], "triggers": ["task.assigned"],
             "publishes": ["dev.build.done"]},
        ],
    },
    "code-assist": {
        "version": "1.0",
        "preset": "code-assist",
        "project": {"name": "{project_name}", "state_dir": ".zf"},
        "session": {"tmux_session": "zf"},
        "orchestrator": {"backend": "claude-code"},
        "roles": [
            {"name": "dev", "backend": "claude-code",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
             "stages": ["implement"], "triggers": ["task.assigned"],
             "publishes": ["dev.build.done"]},
            {"name": "review", "backend": "claude-code",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
             "stages": ["code_review"], "triggers": ["dev.build.done"],
             "publishes": ["review.approved", "review.rejected"]},
            {"name": "test", "backend": "claude-code",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
             "stages": ["independent_test"], "triggers": ["dev.build.done"],
             "publishes": ["test.passed", "test.failed"]},
        ],
    },
    "design-first": {
        "version": "1.0",
        "preset": "design-first",
        "project": {"name": "{project_name}", "state_dir": ".zf"},
        "session": {"tmux_session": "zf"},
        "orchestrator": {"backend": "claude-code"},
        "roles": [
            {"name": "arch", "backend": "claude-code",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
             "stages": ["prd_design", "implementation_options"],
             "publishes": ["arch.proposal.done"]},
            {"name": "dev", "backend": "claude-code",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
             "stages": ["implement"], "triggers": ["task.assigned"],
             "publishes": ["dev.build.done"]},
            {"name": "review", "backend": "claude-code",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
             "stages": ["code_review"], "triggers": ["dev.build.done"],
             "publishes": ["review.approved", "review.rejected"]},
            {"name": "test", "backend": "claude-code",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
             "stages": ["independent_test"], "triggers": ["dev.build.done"],
             "publishes": ["test.passed", "test.failed"]},
            {"name": "judge", "backend": "claude-code",
                "permission_mode": "bypass",  # explicit acknowledgment of full-access mode
             "stages": ["final_adversarial_review"], "triggers": ["review.approved"],
             "publishes": ["judge.passed", "judge.failed"]},
        ],
    },
}


def get_preset(name: str) -> dict:
    """Get a preset config by name."""
    if name not in PRESETS:
        raise ValueError(f"Unknown preset: {name!r}. Available: {list(PRESETS)}")
    return PRESETS[name]


def list_presets() -> list[str]:
    """List available preset names."""
    return list(PRESETS.keys())


# One-line archetype descriptions (VS-style template blurb, doc 102 §6.2).
PRESET_DESCRIPTIONS: dict[str, str] = {
    "safe-team": "全链 7 角色(arch+critic+dev+review+test+judge)+ GAN+判别器 — 多人 / 高风险 / 全栈",
    "code-assist": "dev+review+test — 单语言、有测试的常规开发",
    "design-first": "设计 / 评审重的拓扑 — 重构或新架构",
    "minimal": "单 dev — 脚本 / 玩具 / 快速原型",
    "safe-local": "单 dev + 本地安全约束 — 个人受控环境",
}


def list_presets_detailed() -> list[dict]:
    """Name + one-line description + role count per archetype (for the wizard)."""
    out: list[dict] = []
    for name in PRESETS:
        roles = [r.get("name", "") for r in PRESETS[name].get("roles", []) if isinstance(r, dict)]
        out.append({
            "name": name,
            "description": PRESET_DESCRIPTIONS.get(name, ""),
            "roles": [r for r in roles if r],
        })
    return out


def generate_preset_yaml(name: str, project_name: str) -> str:
    """Generate a zf.yaml string from a preset."""
    config = get_preset(name)
    # Deep-substitute project name
    yaml_str = yaml.dump(config, default_flow_style=False, allow_unicode=True)
    return yaml_str.replace("{project_name}", project_name)


# ---------------------------------------------------------------- V3
# 版本化 policy preset(load 期 merge;/vN 不可变 —— 引用必须带版本,
# "跟随最新"被禁止:否则项目行为随 zaofu 升级静默漂移 = 第二控制面)。
# 裸名 preset(ln/code-assist/...)保持既有语义:init 期模板拷贝的
# provenance 标记,load 期忽略 —— 零迁移。

VERSIONED_PRESETS: dict[str, dict] = {
    "refactor-strict/v1": {
        "budget_enforcement_enabled": True,
        # P1-3 (2026-07-09): required belongs under `contract`. The loader reads
        # verification.contract.required, so the old top-level `required` was dead
        # config — this "strict" preset never actually required the contract. The
        # new fail-closed key check surfaced it.
        "verification": {"contract": {"required": True}},
        "constraints": {"max_file_lines": 500},
        "workflow": {"harness_profile": "strict"},
    },
}


class PresetError(ValueError):
    """版本化 preset 引用错误——loader 包装为 ConfigError。"""


def resolve_versioned_preset(name: str) -> dict:
    import copy

    if name not in VERSIONED_PRESETS:
        raise PresetError(
            f"unknown versioned preset {name!r}; shipped: "
            f"{sorted(VERSIONED_PRESETS)} (裸名 preset 为 init 标记,"
            f"不参与 load merge)"
        )
    return copy.deepcopy(VERSIONED_PRESETS[name])


def merge_preset_base(raw: dict, base: dict) -> dict:
    """preset 为基线,项目字段最高(显式覆盖即决策,不告警)。"""
    def _merge(base_node, over_node):
        if isinstance(base_node, dict) and isinstance(over_node, dict):
            out = dict(base_node)
            for key, value in over_node.items():
                out[key] = _merge(base_node.get(key), value)
            return out
        return over_node if over_node is not None else base_node
    return _merge(base, raw)
