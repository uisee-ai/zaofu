"""B9: contract health 投影(doc 91 §8)。"""

from __future__ import annotations

from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.core.task.contract_health_projection import build_contract_health


def _task(tid, feature="F-1", source_key="", status="ready"):
    return SimpleNamespace(
        id=tid, status=status,
        contract=SimpleNamespace(
            source_key=source_key, source_ref="", feature_id=feature,
        ),
    )


def test_degraded_anchor_and_quarantine_visible():
    tasks = [
        _task("pi-core", source_key="plan.md#a"),
        _task("gateway"),  # 无锚
    ]
    events = [
        ZfEvent(type="provenance.degraded", actor="zf-cli", payload={
            "missing_anchor_task_ids": ["gateway"],
        }),
        ZfEvent(type="issue.candidate.created", actor="zf-cli", payload={
            "source_kind": "quarantine", "pdd_id": "F-1",
        }),
    ]
    health = build_contract_health(tasks, events)
    by_id = {t["task_id"]: t for t in health["tasks"]}
    assert by_id["gateway"]["source_anchor"] == "degraded"
    assert "quarantined" in by_id["pi-core"]["signals"]
    assert health["summary"]["flagged"] == 2


def test_awaiting_approval_and_attempts():
    tasks = [_task("t1", feature="P-1", source_key="x")]
    events = [
        ZfEvent(type="task_map.ready", actor="zf-cli", payload={
            "pdd_id": "P-1", "rework_of": "evt-r",
        }),
        ZfEvent(type="plan.approval.requested", actor="zf-cli", payload={
            "plan_id": "evt-p", "pdd_id": "P-1",
        }),
    ]
    health = build_contract_health(tasks, events)
    t = health["tasks"][0]
    assert t["rework_attempts"] == 1
    assert "awaiting_approval" in t["signals"]
    # 裁决后 hold 消失
    events.append(ZfEvent(
        type="plan.approved", actor="operator", payload={"plan_id": "evt-p"},
    ))
    health2 = build_contract_health(tasks, events)
    assert "awaiting_approval" not in health2["tasks"][0]["signals"]
