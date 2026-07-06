"""BF-1(r6.1 断点复盘):跨代收编真实完成。

r6.1 实弹:dev 16:41 的完整 rework 因 fanout 多代换代(至停机 13+ 代)
被 stale_completion 丢弃两次,review 环整晚未再转。收编规则:同
logical_key 当前代中同 task 的 child 未终局即收编;目标查找走 identity
投影一跳直达。
"""
from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.fanout_completion_adoption import (
    AdoptionTarget,
    find_writer_adoption_target,
)
from zf.runtime.fanout_identity import current_sibling_instance

# r6.1 真实换代链头五代(尾部为当前代;真实链至停机 13+ 代)
_CHAIN = [
    "fanout-avbs-impl-evt-0fcec457",
    "fanout-avbs-impl-evt-5ba300b8",
    "fanout-avbs-impl-evt-3899d6b9",
    "fanout-avbs-impl-evt-d63bc8d7",
    "fanout-avbs-impl-evt-8b04613c",
]
_TASK = "AVBS-METRICS-ASSEMBLY-001"
_STAGE = "avbs-impl"
_PDD = "AVBS-PRD-REBUILD-R61"


def _chain_events() -> list[ZfEvent]:
    return [
        ZfEvent(
            type="fanout.started",
            actor="zf-cli",
            payload={"fanout_id": fid, "stage_id": _STAGE, "pdd_id": _PDD,
                     "topology": "fanout_writer_scoped"},
        )
        for fid in _CHAIN
    ]


def _sibling_lookup(events: list[ZfEvent]):
    return lambda fanout_id: current_sibling_instance(events, fanout_id)


def _manifest(child_status: str) -> dict:
    return {
        "fanout_id": _CHAIN[-1],
        "topology": "fanout_writer_scoped",
        "stage_id": _STAGE,
        "children": [
            {
                "child_id": f"dev-metrics-{_TASK}",
                "task_id": _TASK,
                "status": child_status,
                "run_id": "run-new",
            },
        ],
    }


def test_adopts_across_r61_supersede_chain() -> None:
    events = _chain_events()
    manifests = {_CHAIN[-1]: _manifest("dispatched")}
    target = find_writer_adoption_target(
        fanout_id=_CHAIN[0],  # 16:41 完成事件携带的旧代身份
        task_id=_TASK,
        current_sibling_lookup=_sibling_lookup(events),
        manifest_loader=manifests.get,
    )
    assert isinstance(target, AdoptionTarget)
    assert target.adopted_into == _CHAIN[-1]
    assert target.child["task_id"] == _TASK


def test_no_adoption_when_current_child_terminal() -> None:
    # 新一代已交付 → 来件才是真正的陈旧完成,维持丢弃
    manifests = {_CHAIN[-1]: _manifest("completed")}
    assert find_writer_adoption_target(
        fanout_id=_CHAIN[0],
        task_id=_TASK,
        current_sibling_lookup=_sibling_lookup(_chain_events()),
        manifest_loader=manifests.get,
    ) is None


def test_no_adoption_when_task_absent_from_current_generation() -> None:
    manifests = {_CHAIN[-1]: _manifest("dispatched")}
    assert find_writer_adoption_target(
        fanout_id=_CHAIN[0],
        task_id="OTHER-TASK",
        current_sibling_lookup=_sibling_lookup(_chain_events()),
        manifest_loader=manifests.get,
    ) is None


def test_no_adoption_when_no_current_sibling() -> None:
    # 整条链已 cancelled 收尾(current 里没有同键实例)
    events = _chain_events()
    events.append(ZfEvent(
        type="fanout.cancelled", actor="zf-cli",
        payload={"fanout_id": _CHAIN[-1], "reason": "operator_stop"},
    ))
    # cancelled 不改 current 标记本身,但换代到不同 stage 的键查不到 sibling
    assert find_writer_adoption_target(
        fanout_id="fanout-unknown",
        task_id=_TASK,
        current_sibling_lookup=_sibling_lookup(events),
        manifest_loader=lambda _: None,
    ) is None


def test_no_adoption_for_non_writer_topology() -> None:
    manifests = {
        _CHAIN[-1]: {**_manifest("dispatched"), "topology": "fanout_reader"},
    }
    assert find_writer_adoption_target(
        fanout_id=_CHAIN[0],
        task_id=_TASK,
        current_sibling_lookup=_sibling_lookup(_chain_events()),
        manifest_loader=manifests.get,
    ) is None


def test_current_sibling_instance_resolves_deep_chain() -> None:
    sibling = current_sibling_instance(_chain_events(), _CHAIN[0])
    assert sibling is not None
    assert sibling["fanout_id"] == _CHAIN[-1]
    # 当前代自身没有 sibling(自己就是 current)
    assert current_sibling_instance(_chain_events(), _CHAIN[-1]) is None
