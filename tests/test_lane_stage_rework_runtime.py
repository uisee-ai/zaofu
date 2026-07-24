from __future__ import annotations

from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.rework_feedback import (
    descriptor_from_payload as feedback_descriptor_from_payload,
    hydrate_rework_feedback,
)
from zf.runtime.run_manager_rework_triage import is_semantic_triage_cap
from zf.runtime.task_contract_snapshot import (
    descriptor_from_payload as contract_descriptor_from_payload,
    hydrate_task_contract_snapshot,
)

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


_TYPED_RESULT_SCHEMA = {
    "verify.child.completed": {"required": ["verification_result"]},
}


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


def test_final_failure_waits_while_failed_lane_rework_is_pending(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path, task_count=2)
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
    _fail_verify(orch, state_dir=state_dir, fanout_id=verify_1)
    assert [
        event for event in log.read_all()
        if event.type == "lane.stage.rework.requested"
        and event.task_id == "TASK-1"
    ]

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

    assert not [event for event in log.read_all() if event.type == "test.failed"]
    assert not [event for event in log.read_all() if event.type == "test.passed"]


def test_v4_contract_result_feedback_runtime_handoff(tmp_path: Path) -> None:
    state_dir, log, transport, orch = _state(
        tmp_path,
        task_count=1,
        lane_count=1,
        event_schemas=_TYPED_RESULT_SCHEMA,
        schema_profile="canonical-dag/v4",
    )
    _start(orch)
    impl_id = _fanout_id(log, "demo-impl")
    impl_manifest = _manifest(state_dir, impl_id)
    impl_child = _child(impl_manifest, "TASK-1")
    contract_ref = impl_child["contract_snapshot_ref"]
    assert impl_child["task_ref"] == "task/TASK-1"
    assert contract_ref in transport.sent[0][1].read_text(encoding="utf-8")

    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=impl_child,
        task_id="TASK-1",
        file_name="task-1.txt",
    )
    verify_id = _fanout_id(log, "demo-verify")
    verify_manifest = _manifest(state_dir, verify_id)
    verify_child = verify_manifest["children"][0]
    child_payload = verify_child["payload"]
    assert child_payload["contract_snapshot_ref"] == contract_ref
    assert child_payload["task_ref"] == "task/TASK-1"
    assert len(child_payload["target_commit"]) == 40
    assert child_payload["target_snapshot_ref"]
    assert contract_ref in transport.sent[-1][1].read_text(encoding="utf-8")

    contract = hydrate_task_contract_snapshot(
        state_dir,
        contract_descriptor_from_payload(child_payload),
    )
    criterion = contract["acceptance_criteria"][0]
    orch.run_once(events=[ZfEvent(
        type="verify.child.completed",
        actor=verify_child["role_instance"],
        correlation_id="trace-1",
        payload={
            **child_payload,
            "fanout_id": verify_id,
            "child_id": verify_child["child_id"],
            "run_id": verify_child["run_id"],
            "role_instance": verify_child["role_instance"],
            "status": "completed",
            "verification_result": {
                "execution_status": "completed",
                "verdict": "rejected",
                "summary": "contract verification failed",
                "requirement_results": [{
                    "acceptance_id": criterion["acceptance_id"],
                    "status": "failed",
                    "verification_owner": criterion["verification_owner"],
                    "verification_tier": criterion["verification_tier"],
                    "findings": [{"message": "task output is incorrect"}],
                    "reproduction_commands": ["test -f task-1.txt"],
                    "evidence_refs": ["artifacts/task-1-reject.log"],
                }],
            },
        },
    )])

    events = log.read_all()
    lane_failure = next(
        event for event in events
        if event.type == "lane.stage.failed"
        and event.payload.get("task_id") == "TASK-1"
    )
    assert lane_failure.payload["failure_class"] == "product_rejection"
    assert lane_failure.payload["task_ref"] == "task/TASK-1"
    feedback = hydrate_rework_feedback(
        state_dir,
        feedback_descriptor_from_payload(lane_failure.payload),
        expected_task_id="TASK-1",
        expected_fingerprint=lane_failure.payload["failure_fingerprint"],
    )
    assert feedback["failed_acceptance_ids"] == [criterion["acceptance_id"]]
    assert feedback["reproduction_commands"] == ["test -f task-1.txt"]
    rework = [event for event in events if event.type == "lane.stage.rework.requested"]
    assert len(rework) == 1
    assert rework[0].payload["lane_id"] == "lane0"
    assert rework[0].payload["task_ref"] == "task/TASK-1"
    assert "task output is incorrect" in transport.sent[-1][1].read_text(
        encoding="utf-8",
    )


def test_v4_valid_pass_closes_task_and_advances_final_stage(tmp_path: Path) -> None:
    state_dir, log, transport, orch = _state(
        tmp_path,
        task_count=1,
        lane_count=1,
        event_schemas=_TYPED_RESULT_SCHEMA,
        schema_profile="canonical-dag/v4",
    )
    _start(orch)
    impl_id = _fanout_id(log, "demo-impl")
    impl_child = _child(_manifest(state_dir, impl_id), "TASK-1")
    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=impl_child,
        task_id="TASK-1",
        file_name="task-1.txt",
    )
    verify_id = _fanout_id(log, "demo-verify")
    verify_child = _manifest(state_dir, verify_id)["children"][0]
    child_payload = verify_child["payload"]
    contract = hydrate_task_contract_snapshot(
        state_dir,
        contract_descriptor_from_payload(child_payload),
    )
    requirement_results = [{
        "acceptance_id": criterion["acceptance_id"],
        "status": "passed",
        "verification_owner": criterion["verification_owner"],
        "verification_tier": criterion["verification_tier"],
        "findings": [],
        "reproduction_commands": ["test -f task-1.txt"],
        "evidence_refs": ["artifacts/task-1-pass.log"],
    } for criterion in contract["acceptance_criteria"]]

    completion = ZfEvent(
        type="verify.child.completed",
        actor=verify_child["role_instance"],
        correlation_id="trace-1",
        payload={
            **child_payload,
            "fanout_id": verify_id,
            "child_id": verify_child["child_id"],
            "run_id": verify_child["run_id"],
            "role_instance": verify_child["role_instance"],
            "status": "completed",
            "verification_result": {
                "execution_status": "completed",
                "verdict": "passed",
                "summary": "all task requirements passed",
                "requirement_results": requirement_results,
            },
        },
    )
    orch.run_once(events=[completion])
    orch.run_once(events=[completion])

    events = log.read_all()
    done_evidence = [event for event in events if event.type == "task.done.evidence"]
    assert len(done_evidence) == 1
    assert done_evidence[0].payload["source"] == "lane_pipeline_final_stage"
    assert done_evidence[0].payload["evidence_refs"] == [
        "artifacts/task-1-pass.log",
    ]
    assert TaskStore(state_dir / "kanban.json").get("TASK-1").status == "done"
    completed = [event for event in events if event.type == "lane.stage.completed"][-1]
    assert completed.payload["failure_class"] == "none"
    assert completed.payload["evidence_refs"] == ["artifacts/task-1-pass.log"]
    assert [event for event in events if event.type == "test.passed"]
    assert [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "demo-final"
    ]
    assert not [event for event in events if event.type == "lane.stage.failed"]
    assert transport.sent[-1][0] == "judge"


def test_v4_missing_target_sidecar_supersedes_result_without_closing_task(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch = _state(
        tmp_path,
        task_count=1,
        lane_count=1,
        event_schemas=_TYPED_RESULT_SCHEMA,
        schema_profile="canonical-dag/v4",
    )
    _start(orch)
    impl_id = _fanout_id(log, "demo-impl")
    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=_child(_manifest(state_dir, impl_id), "TASK-1"),
        task_id="TASK-1",
        file_name="task-1.txt",
    )
    verify_id = _fanout_id(log, "demo-verify")
    verify_child = _manifest(state_dir, verify_id)["children"][0]
    child_payload = verify_child["payload"]
    contract = hydrate_task_contract_snapshot(
        state_dir,
        contract_descriptor_from_payload(child_payload),
    )
    requirement_results = [{
        "acceptance_id": criterion["acceptance_id"],
        "status": "passed",
        "verification_owner": criterion["verification_owner"],
        "verification_tier": criterion["verification_tier"],
        "findings": [],
        "reproduction_commands": ["test -f task-1.txt"],
        "evidence_refs": ["artifacts/task-1-pass.log"],
    } for criterion in contract["acceptance_criteria"]]
    missing_ref = "artifacts/task-verification-targets/missing.json"
    completion = ZfEvent(
        type="verify.child.completed",
        actor=verify_child["role_instance"],
        correlation_id="trace-1",
        payload={
            **child_payload,
            "fanout_id": verify_id,
            "child_id": verify_child["child_id"],
            "run_id": verify_child["run_id"],
            "role_instance": verify_child["role_instance"],
            "status": "completed",
            "target_snapshot_ref": missing_ref,
            "target_snapshot_digest": "f" * 64,
            "verification_result": {
                "execution_status": "completed",
                "verdict": "passed",
                "summary": "claims pass but target sidecar is absent",
                "target_snapshot_ref": missing_ref,
                "target_snapshot_digest": "f" * 64,
                "requirement_results": requirement_results,
            },
        },
    )

    orch.run_once(events=[completion])

    events = log.read_all()
    invalid = next(
        event for event in events
        if event.type == "workflow.call.result.invalid"
        and event.payload.get("reason") == "stale_call_result_superseded"
    )
    assert invalid.payload["semantic_attempt_incremented"] is False
    assert any(
        issue.get("code") == "stale_target_snapshot"
        and "sidecar ref missing" in str(issue.get("message") or "")
        for issue in invalid.payload["issues"]
    )
    assert not [event for event in events if event.type == "lane.stage.failed"]
    assert not [event for event in events if event.type == "task.done.evidence"]
    assert TaskStore(state_dir / "kanban.json").get("TASK-1").status != "done"


def test_blocked_terminal_schema_event_fails_reader_child_immediately(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch = _state(
        tmp_path,
        task_count=1,
        lane_count=1,
        event_schemas=_TYPED_RESULT_SCHEMA,
        schema_profile="canonical-dag/v4",
        schema_mode="blocking",
    )
    _start(orch)
    impl_id = _fanout_id(log, "demo-impl")
    impl_child = _child(_manifest(state_dir, impl_id), "TASK-1")
    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=impl_child,
        task_id="TASK-1",
        file_name="task-1.txt",
    )
    verify_id = _fanout_id(log, "demo-verify")
    verify_child = _manifest(state_dir, verify_id)["children"][0]
    written = orch.event_writer.append(ZfEvent(
        type="verify.child.completed",
        actor=verify_child["role_instance"],
        correlation_id="trace-1",
        payload={
            **verify_child["payload"],
            "fanout_id": verify_id,
            "child_id": verify_child["child_id"],
            "run_id": verify_child["run_id"],
            "role_instance": verify_child["role_instance"],
            "status": "completed",
        },
    ))
    assert written.type == "discriminator.failed"

    orch.run_once(events=[written])
    events = log.read_all()
    child_failure = next(
        event for event in events
        if event.type == "fanout.child.failed"
        and event.payload.get("fanout_id") == verify_id
    )
    assert child_failure.causation_id == written.id
    assert any(
        event.type == "lane.stage.recovery.deferred"
        and event.task_id == "TASK-1"
        for event in events
    )
    assert not [event for event in events if event.type == "lane.stage.rework.requested"]


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
    capped = [event for event in events if event.type == "task.rework.capped"]
    assert len(capped) == 1
    assert capped[0].payload["failure_count"] == 3
    assert capped[0].payload["semantic_triage_required"] is True
    assert is_semantic_triage_cap(capped[0], threshold=3)
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
