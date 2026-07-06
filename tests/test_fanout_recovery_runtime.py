from __future__ import annotations

import json

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.fanout_recovery import find_unrecorded_writer_fanout_results
from zf.runtime.run_manager import RUN_MANAGER_ACTION_APPLIED, run_manager_tick

from tests.test_writer_fanout_runtime import (
    _child,
    _fanout_id,
    _manifest,
    _seed_tasks,
    _start,
    _state,
)


def test_run_manager_recovers_unrecorded_writer_fanout_terminal(tmp_path):
    state_dir, log, transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {
                "task_id": "TASK-1",
                "scope": "pi-core",
                "affinity_tag": "pi-core",
                "allowed_paths": ["a.txt"],
            },
            {
                "task_id": "TASK-2",
                "scope": "gateway",
                "affinity_tag": "gateway",
                "allowed_paths": ["b.txt"],
            },
            {
                "task_id": "TASK-3",
                "scope": "web-tui",
                "affinity_tag": "web-tui",
                "allowed_paths": ["c.txt"],
            },
        ],
    }), encoding="utf-8")
    _seed_tasks(state_dir, task_ids=("TASK-1", "TASK-2", "TASK-3"))
    _start(orch)

    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    task2 = _child(manifest, "TASK-2")
    failed = ZfEvent(
        id="dev-failed-without-watcher",
        type="dev.failed",
        actor=task2["role_instance"],
        task_id="TASK-2",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task2["child_id"],
            "run_id": task2["run_id"],
            "pdd_id": "F-11111111",
            "status": "failed",
            "reason": "root package.json is assembly-owned",
        },
    )
    log.append(failed)
    assert find_unrecorded_writer_fanout_results(
        state_dir=state_dir,
        events=log.read_all(),
    )

    result = run_manager_tick(
        state_dir=state_dir,
        writer=EventWriter(log),
        config=orch.config,
        project_root=tmp_path,
        event_log=log,
        transport=transport,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.actions_applied >= 1
    assert any(
        event.type == "fanout.child.failed"
        and event.payload.get("child_id") == task2["child_id"]
        for event in events
    )
    assert any(
        event.type == RUN_MANAGER_ACTION_APPLIED
        and event.payload.get("action") == "fanout-terminal-recover"
        for event in events
    )
    final_manifest = _manifest(state_dir, fanout_id)
    task3 = _child(final_manifest, "TASK-3")
    assert task3["status"] == "dispatched"
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2", "dev-2"]
