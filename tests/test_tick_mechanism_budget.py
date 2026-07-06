"""I44 — tick 机制数预算(doc 87 §7-1 / rev4 双计数)。

doc 80 shadow 烂尾的教训:统一器加在旧机制旁边,退役永远 defer。本测试是
"退役真发生了"的机械证明:AST 静态数两处周期机制,断言 ≤ 写死的预算;
预算随 doc 87 §9 各迁移阶段**只准下调**。加新 sweep——无论加在
`start.py _on_tick` 还是 `Orchestrator.run_once`——= 本测试红。

双计数理由(rev4):单计 _on_tick 有 Goodhart 盲区——机制会迁移到计数器
看不见的那侧。实证:R23 的修复(9e00e89)正是在 run_once 侧给
`_check_fanout_timeouts` 扩 synth 覆盖,_on_tick 预算对此完全不可见。

合法下调:迁移 PR 退役机制后,把对应预算常量改小(同 PR)。
非法上调:任何调大预算的改动必须先过 doc 87 §3.1 概念集封闭条款评审,
并在本文件留下评审引用——否则按 I44 违宪处理。
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# ---- 预算(2026-06-11 AST 实测基线;doc 87 §9 迁移路线只准下调) ----
# P1 接管 stall-recovery + dispatch_sweep 后 → 11;P2 SM 转正退役旧
# remediation sweeps → 8;P3 收编 heartbeat/redrive/bug_scan → 5 → 4。
ON_TICK_BUDGET = 2
# 13→14 上调评审依据(doc 87 §3.1 概念集封闭条款):B18 new-task 牧养
# (commit 00ea10c)在 run_once 加 _safe_housekeeping("unclaimed_new_tasks"),
# 为 unclaimed-SLA 防死卡——属 doc 96 §5 kernel recovery hard rule
# (无人值守不能 dead-end),非可迁出投影,故并入 housekeeping 预算。
# 14→15 上调评审依据(merge 2026-06-22): candidate_rework moved into
# run_once as an event-triggered recovery path for stale candidate/task-map
# failures while the shared tick_services runner still covers periodic idle
# recovery. Freeze at the merged count; new recovery sweeps still require
# one-in-one-out.
# 15→16 上调评审依据(merge 2026-07-03, origin 9a82839e audit B1):
# channel_reply_remediation — channel 回复 no-dead-end 补救(714b5fa0),
# 属 doc 96 §5 无人值守不能 dead-end 的 kernel recovery hard rule,与
# unclaimed_new_tasks 同类,并入预算。合流方代账(远端批未同步本快照);
# 新 sweep 仍要求 one-in-one-out。
RUN_ONCE_HOUSEKEEPING_BUDGET = 16
RUN_ONCE_SWEEP_BUDGET = 7

_PERIODIC_PREFIXES = (
    "_run_", "run_", "emit_", "deliver_", "dispatch_", "redrive_",
)


def _on_tick_mechanisms() -> list[str]:
    tree = ast.parse((_REPO / "src/zf/cli/start.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_on_tick":
            names: set[str] = set()
            for n in ast.walk(node):
                if isinstance(n, ast.Call):
                    f = n.func
                    name = (
                        f.id if isinstance(f, ast.Name)
                        else (f.attr if isinstance(f, ast.Attribute) else None)
                    )
                    if name and name.startswith(_PERIODIC_PREFIXES):
                        names.add(name)
            return sorted(names)
    raise AssertionError("start.py _on_tick not found")


def _run_once_calls() -> tuple[list[str], list[str]]:
    tree = ast.parse(
        (_REPO / "src/zf/runtime/orchestrator.py").read_text()
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_once":
            housekeeping: list[str] = []
            sweeps: list[str] = []
            for n in ast.walk(node):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                attr = f.attr if isinstance(f, ast.Attribute) else None
                if (
                    attr == "_safe_housekeeping"
                    and n.args
                    and isinstance(n.args[0], ast.Constant)
                ):
                    housekeeping.append(str(n.args[0].value))
                if attr == "extend" and n.args and isinstance(n.args[0], ast.Call):
                    inner = n.args[0].func
                    if (
                        isinstance(inner, ast.Attribute)
                        and isinstance(inner.value, ast.Name)
                        and inner.value.id == "self"
                    ):
                        sweeps.append(inner.attr)
            return housekeeping, sweeps
    raise AssertionError("Orchestrator.run_once not found")


def test_on_tick_mechanism_budget():
    mechanisms = _on_tick_mechanisms()
    assert len(mechanisms) <= ON_TICK_BUDGET, (
        f"start.py _on_tick has {len(mechanisms)} periodic mechanisms "
        f"(budget {ON_TICK_BUDGET}): {mechanisms}.\n"
        f"I44: new sweeps are not allowed — express the behavior as a "
        f"reconciler expected_next rule (doc 87 §3.1 closure clause), or "
        f"retire an existing mechanism in the same PR (one-in-one-out)."
    )


def test_run_once_housekeeping_budget():
    housekeeping, _ = _run_once_calls()
    assert len(housekeeping) <= RUN_ONCE_HOUSEKEEPING_BUDGET, (
        f"Orchestrator.run_once has {len(housekeeping)} _safe_housekeeping "
        f"steps (budget {RUN_ONCE_HOUSEKEEPING_BUDGET}): {housekeeping}.\n"
        f"I44 rev4: run_once is counted precisely because mechanisms "
        f"migrate to wherever the counter is not looking (9e00e89)."
    )


def test_run_once_sweep_budget():
    _, sweeps = _run_once_calls()
    assert len(sweeps) <= RUN_ONCE_SWEEP_BUDGET, (
        f"Orchestrator.run_once has {len(sweeps)} decision-sweep calls "
        f"(budget {RUN_ONCE_SWEEP_BUDGET}): {sweeps}."
    )


def test_budgets_match_reality_exactly():
    """预算必须贴着实测走:退役后忘了下调预算 = 预算松弛,新 sweep 可以
    钻进松弛空间而测试仍绿。锁相等,下调与退役强制同 PR。"""
    assert len(_on_tick_mechanisms()) == ON_TICK_BUDGET
    housekeeping, sweeps = _run_once_calls()
    assert len(housekeeping) == RUN_ONCE_HOUSEKEEPING_BUDGET
    assert len(sweeps) == RUN_ONCE_SWEEP_BUDGET
