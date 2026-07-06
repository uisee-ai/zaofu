"""avbs-r4 F8: reader child 失败事件的 task_id 消费侧富化。

`review.child.failed`/`verify.child.failed` 由 agent 直接 emit,实战不带
task_id,导致 rework triage / same_lane backedge 对 fanout_reader 聚合
拓扑结构性哑火(r3 归档 impl.rework.requested 0 次)。
"""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.reader_child_task_resolution import resolve_reader_child_task_id

from tests.test_writer_fanout_runtime import _seed_tasks, _state


def _manifest(task_id: str = "AVBS-FLOW-001") -> dict:
    # 形状取自 avbs-r4 归档 fanout-avbs-review-* manifest(2026-07-03)
    return {
        "fanout_id": "fanout-avbs-review-evt-x",
        "topology": "fanout_reader",
        "children": [{
            "child_id": "review-flow-flow",
            "task_id": task_id,
            "payload": {"task_id": task_id, "upstream_task_id": task_id},
        }],
    }


def _event(**overrides) -> ZfEvent:
    payload = {
        "fanout_id": "fanout-avbs-review-evt-x",
        "child_id": "review-flow-flow",
        "status": "failed",
        "reason": "blocking findings",
    }
    payload.update(overrides.pop("payload", {}))
    return ZfEvent(type="review.child.failed", actor="review-flow", payload=payload, **overrides)


def test_resolves_task_id_from_manifest_child() -> None:
    resolved = resolve_reader_child_task_id(
        _event(), manifest_loader=lambda fid: _manifest(),
    )
    assert resolved == "AVBS-FLOW-001"


def test_falls_back_to_payload_upstream_task_id() -> None:
    manifest = _manifest()
    manifest["children"][0].pop("task_id")
    manifest["children"][0]["payload"] = {"upstream_task_id": "T-UP"}
    resolved = resolve_reader_child_task_id(
        _event(), manifest_loader=lambda fid: manifest,
    )
    assert resolved == "T-UP"


def test_leaves_event_with_existing_task_id_alone() -> None:
    resolved = resolve_reader_child_task_id(
        _event(task_id="T-KEEP"), manifest_loader=lambda fid: _manifest(),
    )
    assert resolved == ""


def test_ignores_non_reader_child_events_and_missing_manifest() -> None:
    other = ZfEvent(type="review.rejected", payload={"fanout_id": "f", "child_id": "c"})
    assert resolve_reader_child_task_id(other, manifest_loader=lambda fid: _manifest()) == ""
    assert resolve_reader_child_task_id(_event(), manifest_loader=lambda fid: None) == ""


def test_run_once_enriches_reader_child_failure_and_bumps_retry(tmp_path: Path) -> None:
    # 集成:无 task_id 的 review.child.failed 经 manifest 反查后,
    # _apply_housekeeping 的 retry_count bump(以 task_id 为前置)生效。
    state_dir, log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    fanout_dir = state_dir / "fanouts" / "fanout-avbs-review-evt-x"
    fanout_dir.mkdir(parents=True)
    (fanout_dir / "manifest.json").write_text(
        json.dumps(_manifest(task_id="TASK-1")), encoding="utf-8",
    )

    orch.run_once(events=[_event()])

    store = TaskStore(state_dir / "kanban.json")
    assert store.get("TASK-1").retry_count == 1


def test_run_once_without_manifest_stays_inert(tmp_path: Path) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)

    orch.run_once(events=[_event()])

    store = TaskStore(state_dir / "kanban.json")
    assert store.get("TASK-1").retry_count == 0
