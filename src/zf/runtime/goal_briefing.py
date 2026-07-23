"""G4(133)+U21:briefing 的 goal 块与地面真值/自检条款。

codex 的 objective 每轮从 DB 重渲染注入,并带"worktree 权威、不信
对话记忆"的审计条款(continuation.md:19-20/30-42);U21 追加 dev 完成
自检条款(防"修了这点、弄坏别处"的回归循环——r6.1 续跑实弹,
c24f1c6a 自发做对了全套证据再生成,此处升格为合同条款)。
灰度 goal.enabled,默认关。
"""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent


def goal_briefing_section(
    events: Iterable[ZfEvent],
    *,
    config: Any,
    role: str = "",
    stage: str = "",
    output_profile: str = "",
) -> list[str]:
    goal = getattr(config, "goal", None)
    if not bool(getattr(goal, "enabled", False)):
        return []
    from zf.runtime.run_manager import build_run_goal_projection

    projection = build_run_goal_projection(list(events))
    objective = str(projection.get("objective") or "").strip()
    if not objective:
        return []
    identity = " ".join((role, stage, output_profile)).lower()
    if any(token in identity for token in ("judge", "goal-closure", "closure")):
        role_clause = [
            "Judge 职责:",
            "- 只消费已准入结果、closure facts 与 waiver refs，综合 Goal closure。",
            "- 不运行测试、不扫描源码、不修改产品代码、不 commit，也不发起 replan。",
        ]
    elif any(token in identity for token in ("verify", "review", "critic")):
        role_clause = [
            "Verify/Critic 职责:",
            "- 先复用与当前 target 精确绑定且仍有效的 receipts；只补独立风险 probe。",
            "- 不修改产品代码，不把重复执行 Impl 已通过命令当作独立验证。",
        ]
    elif any(token in identity for token in ("impl", "dev", "writer", "rework")):
        role_clause = [
            "Impl/Rework 职责:",
            "- 完成当前 Task Contract，并运行合同声明的验证和必要的邻接回归。",
            "- 提交本任务改动与当前 target 绑定的 self-check/receipt；不要自行扩大为全量回归。",
        ]
    elif any(token in identity for token in ("plan", "planner", "synth")):
        role_clause = [
            "Planner/Synth 职责:",
            "- 检查 Goal/AC 覆盖、风险、依赖和可执行切片，产出当前阶段规划 artifact。",
            "- 不实施产品代码，不把 proposal 当作已准入的 canonical plan。",
        ]
    elif any(token in identity for token in ("recover", "run-manager", "supervisor", "autoresearch")):
        role_clause = [
            "Recovery 职责:",
            "- 只诊断当前 failure package，并产出观察、恢复决定或修复 proposal。",
            "- 不替代业务 Impl/Verify，也不直接修改 Kernel canonical state。",
        ]
    else:
        role_clause = [
            "当前阶段职责:",
            "- 按 briefing 的 stage scope 和 output contract 工作，不扩大职责。",
        ]
    return [
        "## Run Goal (persistent)",
        "",
        f"- objective: {objective}",
        f"- status: {projection.get('status') or 'active'}",
        "",
        "本 goal 跨轮持续;结束本轮不需要缩小 objective,也不得把成功",
        "重定义为更小更容易的任务。",
        "",
        "Ground-truth 纪律:",
        "- 以 workdir 当前状态与外部实物为权威;先前对话结论仅作线索,",
        "  依赖前先核当前状态;不得以对早期工作的记忆作为完成证明。",
        "- 完成审计:把完成当作未证明——对每条验收标准找到当前状态下",
        "  的权威证据(文件/命令输出/测试结果/运行时行为)后方可声称;",
        "  不确定或间接证据 = 未完成,继续工作或收集更强证据。",
        "",
        *role_clause,
        "",
    ]


__all__ = ["goal_briefing_section"]
