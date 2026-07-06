"""BF-2(r6.1 断点复盘):同一失败周期的重复 repair 批检查点去重。

r6.1 实弹:每个失败周期 human.escalate 与 integration.failed 各生成一个
repair_failed_children 检查点,6 分钟内起两个 impl fanout,后者立即
supersede 前者(16:25/16:31 起连续 6 个周期复现)。guard:source 事件
之后同 stage 同 pdd 域已有新 fanout.started → 第二个检查点判 superseded。
"""
from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_resume import (
    WorkflowBatchResumeCheckpoint,
    _batch_checkpoint_superseded_reason,
)

_PDD = "AVBS-PRD-REBUILD-R61"
_STAGE = "avbs-impl"


def _checkpoint(source_event_id: str, action: str = "repair_failed_children") -> WorkflowBatchResumeCheckpoint:
    return WorkflowBatchResumeCheckpoint(
        checkpoint_id=f"wfres-{source_event_id}",
        source_event_id=source_event_id,
        source_event_type="integration.failed",
        blocking_event_id=source_event_id,
        safe_resume_action=action,
        pdd_id=_PDD,
        fanout_id="f-old",
        stage_id=_STAGE,
    )


def _events() -> list[ZfEvent]:
    return [
        ZfEvent(id="evt-old-start", type="fanout.started", actor="zf-cli",
                payload={"fanout_id": "f-old", "stage_id": _STAGE, "pdd_id": _PDD}),
        ZfEvent(id="evt-timeout", type="fanout.timed_out", actor="zf-cli",
                payload={"fanout_id": "f-old"}),
        ZfEvent(id="evt-esc", type="human.escalate", actor="zf-cli",
                payload={"fanout_id": "f-old"}),
        ZfEvent(id="evt-int", type="integration.failed", actor="zf-cli",
                payload={"fanout_id": "f-old"}),
        ZfEvent(id="evt-repair-start", type="fanout.started", actor="zf-cli",
                payload={"fanout_id": "f-repair", "stage_id": _STAGE, "pdd_id": _PDD}),
    ]


def test_second_checkpoint_rejected_after_repair_started() -> None:
    reason = _batch_checkpoint_superseded_reason(_events(), _checkpoint("evt-int"))
    assert "already started by f-repair" in reason


def test_first_checkpoint_passes_before_repair_started() -> None:
    # 16:25 首个检查点 apply 时 repair 尚未起 → 放行
    assert _batch_checkpoint_superseded_reason(_events()[:4], _checkpoint("evt-esc")) == ""


def test_next_failure_cycle_passes() -> None:
    # 新一轮失败(repair 超时后的新 escalate)必须放行
    events = _events() + [
        ZfEvent(id="evt-timeout-2", type="fanout.timed_out", actor="zf-cli",
                payload={"fanout_id": "f-repair"}),
        ZfEvent(id="evt-esc-2", type="human.escalate", actor="zf-cli",
                payload={"fanout_id": "f-repair"}),
    ]
    assert _batch_checkpoint_superseded_reason(events, _checkpoint("evt-esc-2")) == ""


def test_other_stage_fanout_does_not_supersede() -> None:
    events = _events()
    events[-1] = ZfEvent(id="evt-repair-start", type="fanout.started", actor="zf-cli",
                         payload={"fanout_id": "f-repair", "stage_id": "avbs-review", "pdd_id": _PDD})
    assert _batch_checkpoint_superseded_reason(events, _checkpoint("evt-int")) == ""


def test_other_pdd_scope_does_not_supersede() -> None:
    events = _events()
    events[-1] = ZfEvent(id="evt-repair-start", type="fanout.started", actor="zf-cli",
                         payload={"fanout_id": "f-repair", "stage_id": _STAGE, "pdd_id": "OTHER-PDD"})
    assert _batch_checkpoint_superseded_reason(events, _checkpoint("evt-int")) == ""


def test_trigger_rework_also_deduped() -> None:
    reason = _batch_checkpoint_superseded_reason(
        _events(), _checkpoint("evt-int", action="trigger_rework"),
    )
    assert "already started by f-repair" in reason
