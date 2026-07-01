"""B18: kanban new-task 牧养路由 + SLA 防死卡(doc 93 §7.4)。"""

from __future__ import annotations

import time
from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.runtime.new_task_shepherd import (
    route_new_task,
    unclaimed_warnings,
)


def _task(assignee="", scope=None, verification=""):
    return SimpleNamespace(
        assigned_to=assignee,
        contract=SimpleNamespace(scope=scope or [], verification=verification),
    )


def test_default_assignee_is_shepherd():
    r = route_new_task(_task(), lane_profiles={"product-3-lane"})
    assert r.mode == "shepherd"
    assert r.target == "kanban-agent"


def test_squad_with_full_contract_dispatches():
    r = route_new_task(
        _task("squad:product-3-lane", ["packages/x/**"], "pytest -q"),
        lane_profiles={"product-3-lane"},
    )
    assert r.mode == "squad"
    assert r.target == "product-3-lane"


def test_squad_without_contract_falls_back_to_shepherd_not_dead():
    r = route_new_task(
        _task("squad:product-3-lane"),
        lane_profiles={"product-3-lane"},
    )
    assert r.mode == "shepherd"  # 不拒死,降级补全


def test_unclaimed_sla_warning_and_idempotency():
    now = time.time()
    from datetime import datetime, timezone

    old_ts = datetime.fromtimestamp(now - 700, tz=timezone.utc).isoformat()
    created = ZfEvent(
        type="task.created", actor="operator", task_id="T-1", payload={},
    )
    object.__setattr__(created, "ts", old_ts) if hasattr(
        created, "__dataclass_fields__",
    ) else None
    created.ts = old_ts
    tasks = [SimpleNamespace(id="T-1", status="ready")]
    warnings = unclaimed_warnings([*tasks], [created], now_ts=now)
    assert warnings and warnings[0]["task_id"] == "T-1"
    # 已 warn → 不重发
    warned = ZfEvent(
        type="task.unclaimed.warning", actor="zf-cli", task_id="T-1",
        payload={},
    )
    assert unclaimed_warnings(tasks, [created, warned], now_ts=now) == []
    # 已派发 → 不告警
    dispatched = ZfEvent(
        type="task.dispatched", actor="zf-cli", task_id="T-1", payload={},
    )
    assert unclaimed_warnings(tasks, [created, dispatched], now_ts=now) == []
