"""U2:rework cap 指纹计数(goal.rework_fingerprint 灰度)。

r6.1 续跑实弹:findings 逐轮收窄(wiring→bridge→适配器)时 per-pdd
总数计满,6 次误报 escalate;传输错位(candidate 落后)下同 findings
是伪拒,也被计满并触发了停机决策。指纹计数 + 驳回有效性前置切开三种
情形:前进不计满 / 真停滞计满 / 伪拒不计。
"""
from __future__ import annotations

from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_rework import plan_candidate_rework

_PDD = "PDD-1"
_TASK = "T-1"


def _cfg(fingerprint: bool = True):
    return SimpleNamespace(goal=SimpleNamespace(rework_fingerprint=fingerprint))


def _rejection(eid: str, message: str) -> ZfEvent:
    return ZfEvent(
        type="review.rejected", id=eid, actor="zf-cli",
        payload={
            "pdd_id": _PDD, "trace_id": "trace-1", "target_ref": "main",
            "reason": message,
            "findings": [{"severity": "high", "message": message}],
            "failed_task_ids": [_TASK],
        },
    )


def _rework_marker(eid: str, rework_of: str) -> ZfEvent:
    return ZfEvent(
        type="task_map.ready", id=eid, actor="zf-cli",
        payload={
            "pdd_id": _PDD, "rework_of": rework_of,
            "rework_source": "review.rejected",
        },
    )


def _completion(eid: str, commit: str) -> ZfEvent:
    return ZfEvent(
        type="dev.build.done", id=eid, actor="dev-1", task_id=_TASK,
        payload={"source_commit": commit},
    )


def _integrated(eid: str, commit: str) -> ZfEvent:
    return ZfEvent(
        type="candidate.task_ref.applied", id=eid, actor="zf-cli",
        task_id=_TASK, payload={"source_commit": commit},
    )


def _current_pipeline(commit: str, *steps: ZfEvent) -> list[ZfEvent]:
    # candidate 与最新交付一致的基础事件序
    return [_completion("e-build-" + commit, commit), _integrated("e-int-" + commit, commit), *steps]


def test_progressing_findings_do_not_exhaust_cap() -> None:
    # 两次驳回 findings 不同 → 指纹不同 → 不 escalate(现行会 escalate)
    events = _current_pipeline(
        "c1",
        _rejection("r1", "wiring missing"),
        _rework_marker("m1", "r1"),
        _rejection("r2", "bridge misaligned"),
    )
    plans = plan_candidate_rework(events, max_attempts=2, config=_cfg())
    assert len(plans) == 1
    assert plans[0].action != "escalate"


def test_same_findings_still_escalate_at_cap() -> None:
    events = _current_pipeline(
        "c1",
        _rejection("r1", "same defect"),
        _rework_marker("m1", "r1"),
        _rejection("r2", "same defect"),
        _rework_marker("m2", "r2"),
        _rejection("r3", "same defect"),
    )
    plans = plan_candidate_rework(events, max_attempts=2, config=_cfg())
    assert len(plans) == 1
    assert plans[0].action == "escalate"


def test_ineffective_rejection_never_counts() -> None:
    # 第 12 轮复刻:交付 c2 后集成仍是 c1,驳回(即使同指纹×N)不计 cap
    events = [
        _completion("b1", "c1"),
        _integrated("i1", "c1"),
        _rejection("r1", "same defect"),
        _rework_marker("m1", "r1"),
        _rejection("r2", "same defect"),
        _rework_marker("m2", "r2"),
        _completion("b2", "c2"),          # 新交付
        _integrated("i2", "c1"),          # 集成落后
        _rejection("r3", "same defect"),  # 伪拒
    ]
    plans = plan_candidate_rework(events, max_attempts=2, config=_cfg())
    assert len(plans) == 1
    assert plans[0].action == "retrigger"
    assert plans[0].classification == "rejection_ineffective_candidate_behind"


def test_switch_off_preserves_current_behavior() -> None:
    events = _current_pipeline(
        "c1",
        _rejection("r1", "wiring missing"),
        _rework_marker("m1", "r1"),
        _rejection("r2", "bridge misaligned"),
        _rework_marker("m2", "r2"),
        _rejection("r3", "adapter broken"),
    )
    plans = plan_candidate_rework(events, max_attempts=2, config=_cfg(fingerprint=False))
    assert len(plans) == 1
    assert plans[0].action == "escalate"  # 现行 per-pdd 总数计满
