"""131-P1 E1: actionable 事件的 registry forcing 闭包(双向 ratchet)。

审计「机制在但没上岗」家族在 registry 维度的机械化:运行时发射的
actionable 事件必须有 EVENT_PROBLEM_SPECS 条目,否则 Supervisor 发现
不了、Run Manager 不知道怎么修(doc 121/131-P1)。存量 36 个未注册
事件冻结在豁免表——只许缩小:注册一个就必须从表里删一个;新增
producer 不注册直接红。
"""

from __future__ import annotations

import re
from pathlib import Path

from zf.runtime.event_problem_registry import EVENT_PROBLEM_SPECS

_SRC = Path(__file__).resolve().parents[1] / "src" / "zf"
_TYPE_LITERAL = re.compile(r'type="([a-z][a-z0-9_.]+)"')
_ACTIONABLE = re.compile(
    r"(\.failed|\.blocked|\.rejected|\.quarantined|_suppressed|_mismatch"
    r"|lag_warning|\.escalate$|\.waived|\.revoked|\.timed_out|\.stuck"
    r"|\.capped|\.dead)"
)

LEGACY_UNREGISTERED: frozenset[str] = frozenset()  # E3-4 清偿完毕(2026-07-04):存量 36 个全部注册,本表回零。只许为空——新增 actionable 事件必须注册,不得复活豁免。


def _emitted_actionable_events() -> set[str]:
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _TYPE_LITERAL.finditer(text):
            event_type = match.group(1)
            if _ACTIONABLE.search(event_type):
                found.add(event_type)
    return found


def test_new_actionable_producers_must_register() -> None:
    emitted = _emitted_actionable_events()
    unregistered = {
        ev for ev in emitted
        if ev not in EVENT_PROBLEM_SPECS and ev not in LEGACY_UNREGISTERED
    }
    assert not unregistered, (
        f"新增 actionable 事件未注册 registry spec: {sorted(unregistered)}。"
        f"按 doc 121/131-P1,给 EVENT_PROBLEM_SPECS 补条目"
        f"(event_class/problem_class/owner_route/attention),"
        f"不要把它加进 LEGACY_UNREGISTERED(该表只许缩小)。"
    )


def test_legacy_exemption_only_shrinks() -> None:
    # 注册后必须同步从豁免表删除,防「注册了但表不缩」稀释 ratchet。
    both = LEGACY_UNREGISTERED & set(EVENT_PROBLEM_SPECS)
    assert not both, f"已注册仍留在豁免表(应删除): {sorted(both)}"


def test_legacy_exemption_entries_still_emitted() -> None:
    # 豁免表不许滞留已停发事件,保持清点诚实。
    emitted = _emitted_actionable_events()
    stale = LEGACY_UNREGISTERED - emitted
    assert not stale, f"豁免表含已不再发射的事件(应删除): {sorted(stale)}"


def test_f_batch_events_registered_with_sane_specs() -> None:
    expectations = {
        "fanout.duplicate_suppressed": ("projection_only", "low"),
        "rework.routing.scope_mismatch": ("abnormal", "high"),
        "runtime.watcher.lag_warning": ("abnormal", "high"),
        "verification.waived": ("projection_only", "low"),
        "verification.waiver.revoked": ("projection_only", "low"),
        "candidate.rework.quarantined": ("expected_negative", "medium"),
    }
    for event_type, (event_class, severity) in expectations.items():
        spec = EVENT_PROBLEM_SPECS.get(event_type)
        assert spec is not None, f"{event_type} 未注册"
        assert spec.event_class == event_class, (event_type, spec.event_class)
        assert spec.severity == severity, (event_type, spec.severity)
        assert spec.owner_route == "run_manager"


# --- 第二扫描源(2026-07-04 D 批):KNOWN_EVENT_TYPES 全量 ---
# 字面量扫描的盲区实证:run.manager.action.failed(常量发射)落 unknown、
# flow.discovery.*(flow 展开)整族漏注册打红基线。本表冻结存量缺口,
# **只许缩小**——KNOWN_EVENT_TYPES 新增 actionable 形状事件必须带 spec。
KNOWN_TYPES_UNREGISTERED: frozenset[str] = frozenset({
    "agent.session.part.failed",
    "agent.session.run.failed",
    "artifact.manifest.rejected",
    "artifact.promote.blocked",
    "automation.proposal.failed",
    "automation.proposal.rejected",
    "automation.run.failed",
    "autoresearch.loop.failed",
    "autoresearch.review_gate.failed",
    "autoresearch.validation.failed",
    "bridge.inbound.rejected",
    "bridge.message.failed",
    "channel.agent.reply.failed",
    "channel.artifact.rejected",
    "channel.consensus.blocked",
    "channel.context_pack.rejected",
    "channel.handoff.rejected",
    "channel.member.add.rejected",
    "channel.message.failed",
    "channel.question.resolve.rejected",
    "feishu.action.failed",
    "feishu.inbound_bridge.failed",
    "human.escalation.failed",
    "kanban.agent.turn.failed",
    "loop.action.rejected",
    "loop.learning.promotion.rejected",
    "openclaw_feishu_bridge.action.failed",
    "operator.action.failed",
    "operator.input.failed",
    "operator.intent.rejected",
    "operator.session.failed",
    "phase.regression.blocked",
    "recovery.step.failed",
    "repair.action.rejected",
    "replan.owner_decision.rejected",
    "run.manager.action.blocked",
    "run.manager.action.failed",
    "run.manager.action.verify.failed",
    "run.manager.human_decision.rejected",
    "run.manager.repair.blocked",
    "run.manager.repair.rejected",
    "run.manager.tick.failed",
    "runtime.action.attempt.failed",
    "runtime.action.failed",
    "runtime.action.rejected",
    "task.doc.ingest.rejected",
    "web.action.failed",
    "worker.drain.failed",
    "worker.recovery.blocked",
    "worker.reply.failed",
    "workflow.adjust.rejected",
    "workflow.resume.rejected",
})


def test_known_event_types_actionable_must_register_or_be_frozen() -> None:
    """第二扫描源 ratchet:新增 actionable 已知类型未注册 → 红。"""
    from zf.core.events.known_types import KNOWN_EVENT_TYPES

    actionable = {
        t for t in KNOWN_EVENT_TYPES
        if _ACTIONABLE.search(t) and t not in EVENT_PROBLEM_SPECS
    }
    new_unregistered = actionable - KNOWN_TYPES_UNREGISTERED
    assert not new_unregistered, (
        "KNOWN_EVENT_TYPES 中新增 actionable 事件必须注册 EVENT_PROBLEM_SPECS:"
        f"{sorted(new_unregistered)}"
    )


def test_known_types_exemption_shrinks_only() -> None:
    """豁免表陈旧检测:已注册/已移除的事件必须同步从冻结表删除。"""
    from zf.core.events.known_types import KNOWN_EVENT_TYPES

    stale = {
        t for t in KNOWN_TYPES_UNREGISTERED
        if t in EVENT_PROBLEM_SPECS or t not in KNOWN_EVENT_TYPES
    }
    assert not stale, f"冻结表陈旧条目,请删除:{sorted(stale)}"
