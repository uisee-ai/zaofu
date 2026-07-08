from __future__ import annotations

from pathlib import Path

from tests.test_lane_stage_streaming_runtime import (
    _child,
    _complete_verify,
    _complete_writer,
    _fail_verify,
    _fanout_id,
    _manifest,
    _start,
    _state,
)


def test_final_readiness_waits_for_all_latest_verify_lane_results(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path, task_count=2)
    _start(orch)
    impl_id = _fanout_id(log, "demo-impl")
    impl_manifest = _manifest(state_dir, impl_id)

    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=_child(impl_manifest, "TASK-1"),
        task_id="TASK-1",
        file_name="task-1.txt",
    )
    verify_1 = _fanout_id(log, "demo-verify")
    _complete_verify(orch, state_dir=state_dir, fanout_id=verify_1)
    assert not [event for event in log.read_all() if event.type == "test.passed"]

    impl_manifest = _manifest(state_dir, impl_id)
    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=_child(impl_manifest, "TASK-2"),
        task_id="TASK-2",
        file_name="task-2.txt",
    )
    verify_ids = [
        event.payload["fanout_id"]
        for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "demo-verify"
    ]
    _complete_verify(orch, state_dir=state_dir, fanout_id=verify_ids[-1])

    events = log.read_all()
    passed = [event for event in events if event.type == "test.passed"]
    assert len(passed) == 1
    assert passed[0].payload["completed_task_ids"] == ["TASK-1", "TASK-2"]
    assert passed[0].payload["root_fanout_id"] == impl_id
    assert [
        event.payload["stage_id"] for event in events
        if event.type == "fanout.started"
    ].count("demo-final") == 1
    assert [sent[0] for sent in transport.sent] == [
        "dev-1", "dev-2", "test-1", "test-2", "judge",
    ]


def test_verify_failure_rearms_same_lane_impl_without_touching_other_lanes(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path, task_count=2)
    _start(orch)
    impl_id = _fanout_id(log, "demo-impl")
    impl_manifest = _manifest(state_dir, impl_id)

    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=_child(impl_manifest, "TASK-1"),
        task_id="TASK-1",
        file_name="task-1.txt",
    )
    verify_id = _fanout_id(log, "demo-verify")
    _fail_verify(orch, state_dir=state_dir, fanout_id=verify_id)

    events = log.read_all()
    failed = [
        event for event in events
        if event.type == "lane.stage.failed"
        and event.payload.get("stage_slot") == "verify"
    ]
    assert len(failed) == 1
    assert failed[0].payload["task_id"] == "TASK-1"
    assert failed[0].payload["lane_id"] == "lane0"
    assert failed[0].payload["failure_target"] == "impl"

    rework = [
        event for event in events
        if event.type == "lane.stage.rework.requested"
    ]
    assert len(rework) == 1
    assert rework[0].payload["task_id"] == "TASK-1"
    assert rework[0].payload["lane_id"] == "lane0"
    assert rework[0].payload["target_stage_slot"] == "impl"
    assert rework[0].payload["attempt"] == 1

    rework_fanouts = [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "demo-impl"
        and event.payload.get("trigger_event_id") == rework[0].id
    ]
    assert len(rework_fanouts) == 1
    rework_manifest = _manifest(state_dir, rework_fanouts[0].payload["fanout_id"])
    assert len(rework_manifest["children"]) == 1
    rework_child = rework_manifest["children"][0]
    assert rework_child["task_id"] == "TASK-1"
    assert rework_child["lane_id"] == "lane0"
    assert rework_child["role_instance"] == "dev-1"
    assert rework_child["root_fanout_id"] == impl_id
    assert not [
        event for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload.get("task_id") == "TASK-2"
        and event.payload.get("fanout_id") == rework_manifest["fanout_id"]
    ]
    assert transport.sent[-1][0] == "dev-1"


def test_lane_rework_cap_quarantines_without_dispatching_new_child(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path, task_count=1)
    _start(orch)
    impl_id = _fanout_id(log, "demo-impl")
    impl_manifest = _manifest(state_dir, impl_id)

    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=_child(impl_manifest, "TASK-1"),
        task_id="TASK-1",
        file_name="task-1.txt",
    )
    verify_id = _fanout_id(log, "demo-verify")
    _fail_verify(orch, state_dir=state_dir, fanout_id=verify_id)

    first_rework_id = [
        event.payload["fanout_id"]
        for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "demo-impl"
        and event.payload.get("trigger_event_id") != ""
    ][-1]
    first_rework_manifest = _manifest(state_dir, first_rework_id)
    _complete_writer(
        orch,
        fanout_id=first_rework_id,
        child=first_rework_manifest["children"][0],
        task_id="TASK-1",
        file_name="task-1.txt",
        content="TASK-1 rework 1\n",
    )
    verify_ids = [
        event.payload["fanout_id"]
        for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "demo-verify"
    ]
    _fail_verify(orch, state_dir=state_dir, fanout_id=verify_ids[-1])

    second_rework_id = [
        event.payload["fanout_id"]
        for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "demo-impl"
    ][-1]
    second_rework_manifest = _manifest(state_dir, second_rework_id)
    _complete_writer(
        orch,
        fanout_id=second_rework_id,
        child=second_rework_manifest["children"][0],
        task_id="TASK-1",
        file_name="task-1.txt",
        content="TASK-1 rework 2\n",
    )
    verify_ids = [
        event.payload["fanout_id"]
        for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "demo-verify"
    ]
    before_dispatches = len(transport.sent)
    _fail_verify(orch, state_dir=state_dir, fanout_id=verify_ids[-1])

    events = log.read_all()
    assert [
        event.payload["attempt"] for event in events
        if event.type == "lane.stage.rework.requested"
    ] == [1, 2]
    quarantined = [
        event for event in events
        if event.type == "lane.stage.rework.quarantined"
    ]
    assert len(quarantined) == 1
    assert quarantined[0].payload["attempt"] == 3
    assert quarantined[0].payload["max_attempts"] == 2
    assert len(transport.sent) == before_dispatches


def test_lane_rework_fanout_does_not_supersede_sibling_lanes(
    tmp_path: Path,
) -> None:
    """F4(bizsim r4 实锚):per-lane rework 重生的 stage 级 fanout 不得与原代
    共享 fanout identity logical_key,否则兄弟 lane 在飞完工被 stale 连坐。"""
    from zf.runtime.fanout_identity import fanout_current_status

    state_dir, log, transport, orch = _state(tmp_path, task_count=2)
    _start(orch)
    impl_id = _fanout_id(log, "demo-impl")
    impl_manifest = _manifest(state_dir, impl_id)

    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=_child(impl_manifest, "TASK-1"),
        task_id="TASK-1",
        file_name="task-1.txt",
    )
    verify_id = _fanout_id(log, "demo-verify")
    _fail_verify(orch, state_dir=state_dir, fanout_id=verify_id)

    events = log.read_all()
    rework_fanouts = [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "demo-impl"
        and event.payload.get("rework_of_lane_stage_event_id")
    ]
    assert len(rework_fanouts) == 1
    rework_started = rework_fanouts[0]
    # 代际隔离字段:task_id + rework_attempt 必须进 started payload,
    # 使 logical_key 与原代(task 位为空)不同。
    assert rework_started.payload.get("task_id") == "TASK-1"
    assert rework_started.payload.get("rework_attempt") == 1

    # 原代必须仍为 current —— 兄弟 lane(TASK-2)的在飞完工不得被 stale。
    status = fanout_current_status(events, impl_id)
    assert status.known and status.current, (
        f"gen-1 impl fanout 被 rework fanout 取代: {status}"
    )


def test_workflow_reconcile_revives_unconsumed_stage_trigger(
    tmp_path: Path,
) -> None:
    """FIX-6(bizsim r4 F2):阻塞期被 wait 掉的 stage 触发边沿,经
    workflow.reconcile.requested 由 reactor 补孵化;幂等不重复孵化。"""
    from zf.core.events.model import ZfEvent

    state_dir, log, transport, orch = _state(tmp_path, task_count=2)

    # 直接落账一条 task_map.ready(模拟阻塞期错过的边沿——orchestrator
    # 从未对它 run_once)。
    missed = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111"},
    )
    log.append(missed)
    assert not [e for e in log.read_all() if e.type == "fanout.started"]

    orch.run_once(events=[ZfEvent(
        type="workflow.reconcile.requested",
        actor="run-manager",
        payload={"source": "human_decision_applied"},
    )])
    started = [e for e in log.read_all() if e.type == "fanout.started"]
    assert started, "重扫必须补孵化被错过的 stage 触发"

    # 幂等:再次重扫不重复孵化。
    orch.run_once(events=[ZfEvent(
        type="workflow.reconcile.requested",
        actor="run-manager",
        payload={"source": "human_decision_applied"},
    )])
    assert len([
        e for e in log.read_all() if e.type == "fanout.started"
    ]) == len(started)
