"""K2:唤醒面收窄 — 快照锁 + 删除证明 + cangjie 重放基线。"""

from __future__ import annotations

from zf.runtime.wake_patterns import (
    WAKE_CATEGORY_BATCH_PROCESSED,
    WAKE_CATEGORY_HANDLER_TRIGGERING,
    WAKE_CATEGORY_LAYER1_WAKE_LAYER2_NOISE,
    WAKE_CATEGORY_PROJECTION_ONLY,
    WAKE_CATEGORY_OBSERVED_ONLY,
    WAKE_PATTERNS,
    wake_contract_diagnostics,
    wake_event_category,
)

# K2 移除的 7 个(变更此列表 = 显式决策,需同步 wake_patterns.py 头注)
REMOVED = frozenset({
    "memory.note", "agent.usage",
    "gan.round.started", "gan.round.completed",
    "discriminator.passed",
    "fanout.aggregate.started", "fanout.aggregate.completed",
})
# 显式保留(各有消费者证明)
KEPT = frozenset({
    "fanout.serialize",        # 需 Layer 2 路由
    "cost.budget.exceeded",    # kernel halt 路径未证明前不删
    "fanout.timed_out",        # reactor 有 handler(审计原表偏差)
    "dispatch.silent_stall",   # K2 补了 explicit handler
})


def test_removed_events_not_in_wake_list():
    assert REMOVED.isdisjoint(set(WAKE_PATTERNS))


def test_kept_events_still_wake():
    assert KEPT <= set(WAKE_PATTERNS)


def test_wake_list_changes_are_explicit():
    # 快照锁:数量带 ±0 容差——增删唤醒源必须改本测试(显式决策)。
    assert len(WAKE_PATTERNS) == len(set(WAKE_PATTERNS))  # 无重复
    assert len(WAKE_PATTERNS) == 101, (
        f"WAKE_PATTERNS={len(WAKE_PATTERNS)}; 唤醒面变更需同步本快照"
        f"(K2 基线 103-7=96;B14 plan 审核门显式 +3:plan.approval.requested /"
        f" plan.approved / plan.rejected —— 均 workflow 控制事件需唤醒"
        f"[plan.rejected∈CANDIDATE_FAIL_EVENTS、plan.approved 触发 fanout 重入]"
        f" → 96+3=99; merge 2026-06-22 fanout.child.completed/failed +2"
        f" → 101)"
    )


def test_wake_events_have_explicit_classification():
    diagnostics = wake_contract_diagnostics()

    assert diagnostics["duplicate_wake_patterns"] == []
    assert diagnostics["unclassified"] == []
    assert diagnostics["batch_processed_that_wake"] == []
    assert diagnostics["wake_count"] == len(WAKE_PATTERNS)


def test_wake_classification_examples_are_locked():
    assert (
        wake_event_category("dev.build.done")
        == WAKE_CATEGORY_HANDLER_TRIGGERING
    )
    assert (
        wake_event_category("codex.hook.pre_tool_use")
        == WAKE_CATEGORY_LAYER1_WAKE_LAYER2_NOISE
    )
    assert wake_event_category("memory.note") == WAKE_CATEGORY_PROJECTION_ONLY
    assert wake_event_category("gan.round.started") == WAKE_CATEGORY_BATCH_PROCESSED
    assert wake_event_category("hook.orphan_event") == WAKE_CATEGORY_OBSERVED_ONLY


def test_silent_stall_handler_registered():
    from zf.runtime.orchestrator_reactor import _BUILTIN_HANDLER_METHODS
    assert ("dispatch.silent_stall", "_on_dispatch_silent_stall") in (
        _BUILTIN_HANDLER_METHODS
    )


# 注:审计的"cangjie 142 次空唤醒"基线来自轮转前 trace;当前
# events.jsonl 是 respawn 风暴形(1531×worker.respawn.failed),其唤醒
# 浪费源是 respawn 族 —— 那是 Layer 2 notify/escalation 的消费面,
# 不属第一波删除。重放削减断言对该 trace 无意义,不在此造伪证;
# respawn 族唤醒的处置归 K3 相 3(remediation 接入)重新评估。
