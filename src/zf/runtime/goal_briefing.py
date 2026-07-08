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
) -> list[str]:
    goal = getattr(config, "goal", None)
    if not bool(getattr(goal, "enabled", False)):
        return []
    from zf.runtime.run_manager import build_run_goal_projection

    projection = build_run_goal_projection(list(events))
    objective = str(projection.get("objective") or "").strip()
    if not objective:
        return []
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
        "- 发完成事件前跑全量自检(单测/e2e/不变量),证明\"没弄坏别处\"",
        "  而不只是\"修了这点\";运行时证据一并再生成并随 commit 提交。",
        "",
    ]


__all__ = ["goal_briefing_section"]
