"""R23: pure_aggregator allowlist must be sufficient for the synth's job.

review-synth (permission_mode: bypass in yaml) is narrowed by pure_aggregator.v1
to an allowlist. R23: the allowlist had no Read tools and a hard-coded
``.zf/artifacts`` cat path, so a headless synth stalled 6h+ on interactive
permission prompts. The narrowing (no write tools) is intentional and stays.
"""
from __future__ import annotations

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.workflow.runner_policy import (
    apply_goal_closure_judge_policy,
    apply_pure_aggregator_policy,
    claude_aggregator_allowed_tools,
    goal_closure_judge_role_refs,
)
from zf.core.workflow.lane_pipeline import parse_lane_pipeline


def _config(state_dir: str = ".zf-custom") -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t", state_dir=state_dir),
        roles=[],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review", trigger="candidate.ready", topology="fanout_reader",
                aggregate=FanoutAggregateConfig(
                    synth_role="review-synth",
                    success_event="review.approved",
                    failure_event="review.rejected",
                ),
            ),
        ]),
    )


def _synth_role() -> RoleConfig:
    return RoleConfig(
        name="review-synth", instance_id="review-synth",
        backend="claude-code", role_kind="reader", permission_mode="bypass",
    )


def test_aggregator_allowlist_includes_read_tools():
    effective = apply_pure_aggregator_policy(_config(), _synth_role())
    assert effective.permission_mode == "allowlist"
    for tool in ("Read", "Glob", "Grep"):
        assert tool in effective.allowed_tools, (
            f"{tool} missing — headless synth stalls on permission prompts"
        )


def test_aggregator_cat_path_uses_real_state_dir():
    tools = claude_aggregator_allowed_tools(_config(state_dir=".zf-cj-min-refactor"))
    assert "Bash(cat .zf-cj-min-refactor/artifacts/*)" in tools
    assert not any(".zf/artifacts" in t for t in tools)


def test_aggregator_cat_path_defaults_without_config():
    tools = claude_aggregator_allowed_tools(None)
    assert "Bash(cat .zf/artifacts/*)" in tools


def test_aggregator_write_protection_intact():
    effective = apply_pure_aggregator_policy(_config(), _synth_role())
    for tool in ("Edit", "Write", "NotebookEdit"):
        assert tool not in effective.allowed_tools
    bash_tools = [t for t in effective.allowed_tools if t.startswith("Bash")]
    assert bash_tools, "zf emit whitelist must survive"
    assert all("zf " in t or "cat " in t for t in bash_tools), (
        "no bare/broad Bash in the aggregator allowlist"
    )


def test_role_declared_tools_still_gain_read():
    role = _synth_role()
    role = type(role)(**{**role.__dict__, "allowed_tools": ["Bash(zf emit *)"]})
    effective = apply_pure_aggregator_policy(_config(), role)
    assert "Read" in effective.allowed_tools
    assert "Bash(zf emit *)" in effective.allowed_tools


def test_aggregator_grants_state_dir_via_allowed_paths():
    # R24: tool allowlist alone is insufficient — cross-directory reads
    # (briefings/instructions in the state dir, outside the synth worktree)
    # hit Claude's directory-trust prompt and a headless pane hangs. The
    # policy must carry the state dir so the adapter emits --add-dir.
    effective = apply_pure_aggregator_policy(
        _config(), _synth_role(), state_dir="/abs/proj/.zf-custom",
    )
    assert "/abs/proj/.zf-custom" in effective.constraints.allowed_paths


def test_claude_adapter_emits_add_dir_for_allowlist_paths():
    from dataclasses import replace as dc_replace

    from zf.runtime.backend import ClaudeCodeAdapter

    role = apply_pure_aggregator_policy(
        _config(), _synth_role(), state_dir="/abs/proj/.zf-custom",
    )
    cmd = ClaudeCodeAdapter().build_command(role)
    assert "--add-dir" in cmd
    assert "/abs/proj/.zf-custom" in cmd
    # bypass roles are unaffected (skip-permissions already covers dirs)
    bypass = dc_replace(role, permission_mode="bypass")
    assert "--add-dir" not in ClaudeCodeAdapter().build_command(bypass)


def test_lane_pipeline_final_judge_gets_goal_closure_readonly_policy():
    pipeline = parse_lane_pipeline({
        "id": "prd-lanes",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "task_source": {"task_map_ref": "artifacts/task-map.json"},
        "affinity_key": "lane_affinity",
        "lane_count": 1,
        "assembly": "none",
        "stages": [{
            "id": "impl",
            "role_pattern": "dev-lane-{lane}",
            "terminal": {
                "success": "dev.build.done",
                "failure": "dev.failed",
            },
        }],
        "final": {
            "when": "all_tasks_verified",
            "role": "judge-prd",
            "success": "goal.closure.synthesized",
            "failure": "goal.closure.synthesis.failed",
        },
    })
    config = ZfConfig(
        project=ProjectConfig(name="prd"),
        workflow=WorkflowConfig(pipelines=[pipeline]),
    )
    role = RoleConfig(
        name="judge-prd",
        instance_id="judge-prd",
        backend="claude-code",
        permission_mode="bypass",
    )

    assert goal_closure_judge_role_refs(config) == {"judge-prd"}
    effective = apply_goal_closure_judge_policy(config, role)
    assert effective.permission_mode == "allowlist"
    assert {"Read", "Glob", "Grep"} <= set(effective.allowed_tools)
    assert "Edit" not in effective.allowed_tools


def test_codex_goal_closure_judge_stays_headless_safe():
    """ZF-JUDGE-HEADLESS-01:codex judge 不得落入 untrusted 交互档。

    restricted → `-a untrusted` 在 headless tmux 下每命令等待确认,
    judge 必超时(07-16 实弹判决两次成孤儿)。codex judge 走 default
    档(-a never -s workspace-write),只读语义由 hook scope 守卫承担。
    """
    pipeline = parse_lane_pipeline({
        "id": "prd-lanes",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "task_source": {"task_map_ref": "artifacts/task-map.json"},
        "affinity_key": "lane_affinity",
        "lane_count": 1,
        "assembly": "none",
        "stages": [{
            "id": "impl",
            "role_pattern": "dev-lane-{lane}",
            "terminal": {
                "success": "dev.build.done",
                "failure": "dev.failed",
            },
        }],
        "final": {
            "when": "all_tasks_verified",
            "role": "judge-prd",
            "success": "goal.closure.synthesized",
            "failure": "goal.closure.synthesis.failed",
        },
    })
    config = ZfConfig(
        project=ProjectConfig(name="prd"),
        workflow=WorkflowConfig(pipelines=[pipeline]),
    )
    role = RoleConfig(
        name="judge-prd",
        instance_id="judge-prd",
        backend="codex",
        permission_mode="bypass",
    )
    effective = apply_goal_closure_judge_policy(config, role)
    # 07-17 二轮实弹:default 档的 workspace-write 沙箱在 bwrap 不可用
    # 宿主上让 judge 双手全废 → codex judge 保持 profile 原档(bypass)
    assert effective.permission_mode == "bypass"
    # claude-code 分支不受影响(仍走 allowlist 只读)
    cc = RoleConfig(
        name="judge-prd", instance_id="judge-prd",
        backend="claude-code", permission_mode="bypass",
    )
    assert apply_goal_closure_judge_policy(config, cc).permission_mode == "allowlist"
