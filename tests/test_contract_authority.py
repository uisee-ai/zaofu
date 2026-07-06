"""avbs-r4 F4/F9: kanban 契约唯一权威源 + contract.update 批量同型应用。

r4 三向真相分叉:task.contract.update 修 kanban 后,briefing 源
(plan-synth workdir 副本)与 reviewer 源(candidate 树副本)仍是旧
命令;且 Layer-2 修了 flow 没修 scene(一致性靠 agent 记性)。
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.contract_authority import apply_contract_authority
from zf.runtime.housekeeping import apply_task_contract_event


def _store(tmp_path: Path) -> TaskStore:
    store = TaskStore(tmp_path / "kanban.json")
    for tid, verification in (
        ("AVBS-SCENE-001", "npm test -- --run tests/scene"),
        ("AVBS-FLOW-001", "npm test -- --run tests/flow"),
    ):
        store.add(Task(
            id=tid, title=tid, status="in_progress",
            contract=TaskContract(
                feature_id="F-1", verification=verification,
                verification_tiers=["runtime"],
            ),
        ))
    return store


def test_dispatch_payload_prefers_kanban_contract(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.update_contract = None  # guard against accidental API drift  # noqa: B010
    # 模拟 task.contract.update 后的权威值
    task = store.get("AVBS-SCENE-001")
    task.contract.verification = (
        "npm test -- --config tests/scene/vitest.config.ts --run tests/scene"
    )
    store.update(task.id, contract=task.contract)

    stale_item = {
        "task_id": "AVBS-SCENE-001",
        "verification": "npm test -- --run tests/scene",
        "allowed_paths": ["src/scenario/**"],
    }
    fixed = apply_contract_authority(stale_item, store)
    assert fixed["verification"] == (
        "npm test -- --config tests/scene/vitest.config.ts --run tests/scene"
    )
    assert fixed["verification_source"] == "kanban_contract"
    assert fixed["allowed_paths"] == ["src/scenario/**"]  # 其余字段不动


def test_authority_noop_without_canonical_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "kanban.json")
    item = {"task_id": "UNKNOWN-1", "verification": "make check"}
    assert apply_contract_authority(item, store) == item


def test_contract_update_applies_to_additional_task_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="AVBS-FLOW-001",
        payload={
            "contract": {
                "verification": "npm test -- --config tests/flow/vitest.config.ts --run tests/flow",
            },
            "additional_task_ids": ["AVBS-SCENE-001", "AVBS-FLOW-001"],
        },
    )
    apply_task_contract_event(store, event)
    expected = "npm test -- --config tests/flow/vitest.config.ts --run tests/flow"
    assert store.get("AVBS-FLOW-001").contract.verification == expected
    assert store.get("AVBS-SCENE-001").contract.verification == expected
    # 各自的其余字段保持原值(字段级回退到任务现值)
    assert store.get("AVBS-SCENE-001").contract.feature_id == "F-1"
