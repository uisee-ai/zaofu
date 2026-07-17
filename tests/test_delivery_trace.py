"""Tests for delivery-trace.v1 — feature-level idea→ship spine."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.delivery_trace import build_delivery_trace

_NOW = "2026-05-29T00:00:00+00:00"


def _task(task_id: str, *, status: str = "backlog", wave: int = 0) -> Task:
    return Task(id=task_id, title=f"title-{task_id}", status=status,
                contract=TaskContract(feature_id="F-1", wave=wave))


def _task_map() -> dict:
    return {
        "schema_version": "task-map.v1",
        "feature_id": "F-1",
        "tasks": [
            {"task_id": "T1", "title": "schema", "owner_role": "dev", "wave": 1},
            {"task_id": "T2", "title": "router", "owner_role": "dev",
             "wave": 2, "blocked_by": ["T1"]},
        ],
    }


def test_builds_feature_trace_with_composed_graph():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="in_progress", wave=2)}
    trace = build_delivery_trace(
        feature_id="F-1", generated_at=_NOW, tasks=tasks, task_map=_task_map(),
        idea={"event_id": "evt-u", "summary": "build api", "source": "user.message"},
        plan={"status": "accepted", "spec_ref": "docs/spec.md"},
        project_id="cangjie-mono", task_map_ref=".zf/artifacts/F-1/task-map.json",
    )
    assert trace["schema_version"] == "delivery-trace.v1"
    assert trace["feature_id"] == "F-1"
    assert trace["trace_id"] == "trace-F-1"
    assert trace["synthetic"] is False
    assert trace["generated_at"] == _NOW
    assert trace["idea"]["summary"] == "build api"
    assert trace["plan"]["spec_ref"] == "docs/spec.md"
    # composed execution graph (reuses build_execution_graph)
    eg = trace["execution_graph"]
    assert eg["task_count"] == 2
    assert eg["done_count"] == 1
    assert eg["in_progress_count"] == 1
    assert {n["task_id"] for n in eg["nodes"]} == {"T1", "T2"}
    assert trace["task_map"]["task_count"] == 2


def test_status_in_progress_then_done():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="in_progress", wave=2)}
    trace = build_delivery_trace(feature_id="F-1", generated_at=_NOW,
                                 tasks=tasks, task_map=_task_map())
    assert trace["status"] == "in_progress"

    tasks["T2"] = _task("T2", status="done", wave=2)
    trace2 = build_delivery_trace(feature_id="F-1", generated_at=_NOW,
                                  tasks=tasks, task_map=_task_map())
    assert trace2["status"] == "done"


def test_ship_readiness_blocked_until_all_done():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="in_progress", wave=2)}
    trace = build_delivery_trace(feature_id="F-1", generated_at=_NOW,
                                 tasks=tasks, task_map=_task_map())
    ship = trace["ship"]
    assert ship["status"] == "blocked"
    assert ship["required_tasks"] == 2
    assert ship["done_tasks"] == 1
    assert {m["task_id"] for m in ship["missing_evidence"]} == {"T2"}

    tasks["T2"] = _task("T2", status="done", wave=2)
    ready = build_delivery_trace(feature_id="F-1", generated_at=_NOW,
                                 tasks=tasks, task_map=_task_map())["ship"]
    assert ready["status"] == "ready"
    assert ready["missing_evidence"] == []


def test_ship_blocked_by_error_drift_even_when_all_done():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="done", wave=2)}
    drift = {"status": "error", "items": [{"severity": "error", "kind": "scope_drift"}]}
    trace = build_delivery_trace(feature_id="F-1", generated_at=_NOW, tasks=tasks,
                                 task_map=_task_map(), drift_report=drift)
    assert trace["ship"]["status"] == "blocked"
    assert trace["drift_report"]["status"] == "error"
    # embedded drift_report must carry summary (CLI renderer depends on it)
    assert "summary" in trace["drift_report"]
    assert set(trace["drift_report"]["summary"]) == {"error", "warning", "info"}


def test_synthetic_trace_when_feature_id_missing():
    tasks = {"X1": _task("X1", status="in_progress")}
    trace = build_delivery_trace(feature_id="", generated_at=_NOW, tasks=tasks,
                                 task_map=None, project_id="proj-a")
    assert trace["synthetic"] is True
    assert trace["trace_id"] == "synthetic:proj-a"
    kinds = {d["kind"] for d in trace["diagnostics"]}
    assert "synthetic_trace" in kinds
    # task-map missing degrades, doesn't crash
    assert "task_map_missing" in kinds
    assert trace["task_map"]["status"] == "missing"


def test_source_event_ids_included():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="done", wave=2)}
    events = [(1, ZfEvent(type="dev.build.done", id="e1", task_id="T1")),
              (2, ZfEvent(type="task.done", id="e2", task_id="T2"))]
    trace = build_delivery_trace(feature_id="F-1", generated_at=_NOW, tasks=tasks,
                                 task_map=_task_map(), events=events)
    assert trace["source_event_ids"] == ["e1", "e2"]


def test_workflow_spine_projects_fanout_bundle_task_and_candidate_nodes():
    tasks = {"T1": _task("T1", status="in_progress", wave=1)}
    task_map_ref = ".zf/artifacts/F-1/task-map.json"
    events = list(enumerate([
        ZfEvent(
            type="fanout.started",
            id="fr1",
            payload={
                "fanout_id": "fanout-scan",
                "topology": "fanout_reader",
                "stage_id": "scan",
                "pdd_id": "F-1",
            },
        ),
        ZfEvent(
            type="product_delivery.task_map.accepted",
            id="pd1",
            payload={"feature_id": "F-1", "task_map_ref": task_map_ref},
        ),
        ZfEvent(
            type="product_delivery.wave.ready",
            id="wv1",
            payload={"feature_id": "F-1", "task_map_ref": task_map_ref, "task_ids": ["T1"]},
        ),
        ZfEvent(type="task.created", id="tc1", task_id="T1"),
        ZfEvent(
            type="fanout.started",
            id="fw1",
            payload={
                "fanout_id": "fanout-write",
                "topology": "fanout_writer_scoped",
                "stage_id": "write",
                "feature_id": "F-1",
                "task_map_ref": task_map_ref,
            },
        ),
        ZfEvent(
            type="fanout.child.dispatched",
            id="fc1",
            task_id="T1",
            payload={
                "fanout_id": "fanout-write",
                "child_id": "dev-1-T1",
                "task_map_ref": task_map_ref,
            },
        ),
        ZfEvent(
            type="candidate.ready",
            id="cr1",
            payload={"feature_id": "F-1", "task_map_ref": task_map_ref},
        ),
    ]))

    trace = build_delivery_trace(
        feature_id="F-1",
        generated_at=_NOW,
        tasks=tasks,
        task_map=_task_map(),
        events=events,
        task_map_ref=task_map_ref,
    )

    kinds = [node["kind"] for node in trace["workflow_spine"]["nodes"]]
    assert "reader_fanout" in kinds
    assert "accepted_delivery_bundle" in kinds
    assert "delivery_wave_ready" in kinds
    assert "kanban_task" in kinds
    assert "writer_fanout" in kinds
    assert "writer_child_run" in kinds
    assert "candidate_gate" in kinds
    writer_child = next(node for node in trace["workflow_spine"]["nodes"] if node["kind"] == "writer_child_run")
    assert writer_child["workflow_run_id"] == "fanout-write"
    assert writer_child["task_id"] == "T1"


def test_delivery_trace_projects_module_parity_gap_amend_loop():
    tasks = {
        "T1": _task("T1", status="done", wave=1),
        "T3-GAP": _task("T3-GAP", status="in_progress", wave=3),
    }
    base_task_map_ref = ".zf/artifacts/F-1/task-map.json"
    amended_task_map_ref = ".zf/artifacts/F-1/gap-amends/evt-gap/task_map.json"
    gap_plan_ref = ".zf/artifacts/F-1/gap-plan.json"
    events = list(enumerate([
        ZfEvent(
            type="verify.parity_scan.requested",
            id="psr1",
            payload={
                "pdd_id": "F-1",
                "fanout_id": "verify-parity-scan-1",
                "task_map_ref": base_task_map_ref,
            },
        ),
        ZfEvent(
            type="module.parity.scan.completed",
            id="psc1",
            payload={
                "pdd_id": "F-1",
                "fanout_id": "verify-parity-scan-1",
                "gap_count": 1,
                "task_map_ref": base_task_map_ref,
            },
        ),
        ZfEvent(
            type="gap_plan.ready",
            id="gpr1",
            payload={
                "pdd_id": "F-1",
                "task_map_ref": base_task_map_ref,
                "gap_plan_ref": gap_plan_ref,
                "gap_tasks": [{
                    "task_id": "T3-GAP",
                    "title": "fill missing web chat parity",
                    "owner_role": "dev",
                }],
            },
        ),
        ZfEvent(
            type="task_map.amended",
            id="tma1",
            payload={
                "pdd_id": "F-1",
                "task_map_ref": base_task_map_ref,
                "new_task_map_ref": amended_task_map_ref,
                "gap_plan_ref": gap_plan_ref,
                "gap_task_ids": ["T3-GAP"],
            },
        ),
        ZfEvent(
            type="task_map.ready",
            id="tmr1",
            payload={
                "pdd_id": "F-1",
                "task_map_ref": amended_task_map_ref,
                "gap_plan_ref": gap_plan_ref,
                "resume_scope": "gap_tasks_only",
                "task_ids": ["T3-GAP"],
            },
        ),
    ]))

    trace = build_delivery_trace(
        feature_id="F-1",
        generated_at=_NOW,
        tasks=tasks,
        task_map=_task_map(),
        events=events,
        task_map_ref=base_task_map_ref,
    )

    loop = trace["module_parity_loop"]
    assert loop["schema_version"] == "module-parity-loop.v1"
    assert loop["status"] == "gap_tasks_dispatched"
    assert loop["scan_request_count"] == 1
    assert loop["scan_result_count"] == 1
    assert loop["gap_plan_count"] == 1
    assert loop["amend_count"] == 2
    assert loop["gap_task_ids"] == ["T3-GAP"]
    assert loop["latest_gap_plan_ref"] == gap_plan_ref
    assert loop["latest_task_map_ref"] == amended_task_map_ref
    assert loop["source_event_ids"] == ["psr1", "psc1", "gpr1", "tma1", "tmr1"]
    goal_loop = trace["goal_closure_loop"]
    assert goal_loop["schema_version"] == "goal-closure-loop.v2"
    assert goal_loop["compatibility_projection"] == "module_parity_loop"
    assert goal_loop["status"] == "gap_tasks_dispatched"
    assert goal_loop["gap_task_ids"] == ["T3-GAP"]


def test_delivery_trace_projects_control_room_contract():
    tasks = {
        "T1": _task("T1", status="done", wave=1),
        "T2": _task("T2", status="blocked", wave=2),
    }
    tasks["T2"].blocked_reason = "waiting for product demo evidence"
    tasks["T2"].contract.owner_role = "dev"
    events = list(enumerate([
        ZfEvent(
            type="flow.gap_plan.ready",
            id="gap1",
            payload={
                "feature_id": "F-1",
                "gap_plan_ref": "reports/F-1/gap-plan.json",
                "gap_tasks": [{"task_id": "T2"}],
            },
        ),
        ZfEvent(
            type="flow.goal.blocked",
            id="blocked1",
            payload={
                "feature_id": "F-1",
                "reason": "demo gap remains",
                "open_p0_p1_gap_count": 1,
                "artifact_refs": ["reports/F-1/discovery.md"],
            },
        ),
    ]))

    trace = build_delivery_trace(
        feature_id="F-1",
        generated_at=_NOW,
        tasks=tasks,
        task_map=_task_map(),
        events=events,
    )

    control = trace["control_room"]
    assert control["schema_version"] == "control-room.v1"
    assert control["current_stage"] == "flow.goal.blocked"
    assert control["blocked_reason"] == "demo gap remains"
    assert control["next_owner"] == "dev"
    assert control["pending_action"] == "run_manager_diagnose_gap"
    assert control["latest_evidence"] == ["reports/F-1/discovery.md"]
    assert control["gap_count"] == 1


def test_delivery_trace_projects_generic_goal_closure_gap_loop():
    tasks = {
        "ISSUE-123-PLAN-001": _task("ISSUE-123-PLAN-001", status="done", wave=1),
        "ISSUE-123-GAP-001": _task("ISSUE-123-GAP-001", status="todo", wave=2),
    }
    base_task_map_ref = ".zf/artifacts/ISSUE-123/task-map.json"
    amended_task_map_ref = ".zf/artifacts/ISSUE-123/gap-amends/evt-gap/task_map.json"
    gap_plan_ref = "reports/ISSUE-123/goal-gap-plan.json"
    events = list(enumerate([
        ZfEvent(
            type="goal.rescan.requested",
            id="grr1",
            payload={
                "pdd_id": "ISSUE-123",
                "goal_kind": "issue",
                "fanout_id": "goal-rescan-1",
                "task_map_ref": base_task_map_ref,
            },
        ),
        ZfEvent(
            type="goal.rescan.completed",
            id="grc1",
            payload={
                "pdd_id": "ISSUE-123",
                "goal_kind": "issue",
                "fanout_id": "goal-rescan-1",
                "task_map_ref": base_task_map_ref,
            },
        ),
        ZfEvent(
            type="goal.gap_plan.ready",
            id="ggp1",
            payload={
                "pdd_id": "ISSUE-123",
                "goal_kind": "issue",
                "gap_category": "issue_gap",
                "task_map_ref": base_task_map_ref,
                "gap_plan_ref": gap_plan_ref,
                "replan_history_ref": "docs/plans/ISSUE-123/replan-history.jsonl",
                "gap_tasks": [{
                    "task_id": "ISSUE-123-GAP-001",
                    "title": "fill issue regression gap",
                }],
            },
        ),
        ZfEvent(
            type="task_map.amended",
            id="tma1",
            payload={
                "pdd_id": "ISSUE-123",
                "task_map_ref": base_task_map_ref,
                "new_task_map_ref": amended_task_map_ref,
                "gap_plan_ref": gap_plan_ref,
                "gap_task_ids": ["ISSUE-123-GAP-001"],
            },
        ),
        ZfEvent(
            type="task_map.ready",
            id="tmr1",
            payload={
                "pdd_id": "ISSUE-123",
                "task_map_ref": amended_task_map_ref,
                "gap_plan_ref": gap_plan_ref,
                "resume_scope": "gap_tasks_only",
                "task_ids": ["ISSUE-123-GAP-001"],
            },
        ),
    ]))

    trace = build_delivery_trace(
        feature_id="ISSUE-123",
        generated_at=_NOW,
        tasks=tasks,
        task_map=_task_map(),
        events=events,
        task_map_ref=base_task_map_ref,
    )

    loop = trace["goal_closure_loop"]
    assert loop["schema_version"] == "goal-closure-loop.v2"
    assert loop["status"] == "gap_tasks_dispatched"
    assert loop["scan_request_count"] == 1
    assert loop["scan_result_count"] == 1
    assert loop["gap_plan_count"] == 1
    assert loop["amend_count"] == 2
    assert loop["gap_task_ids"] == ["ISSUE-123-GAP-001"]
    assert loop["latest_gap_plan_ref"] == gap_plan_ref
    assert loop["latest_replan_history_ref"] == "docs/plans/ISSUE-123/replan-history.jsonl"
    assert loop["latest_task_map_ref"] == amended_task_map_ref


def test_delivery_trace_projects_flow_neutral_goal_closure_gap_loop():
    tasks = {
        "PRD-1-PLAN-001": _task("PRD-1-PLAN-001", status="done", wave=1),
        "PRD-1-GAP-001": _task("PRD-1-GAP-001", status="todo", wave=2),
    }
    base_task_map_ref = ".zf/artifacts/PRD-1/task-map.json"
    amended_task_map_ref = ".zf/artifacts/PRD-1/gap-amends/evt-gap/task_map.json"
    gap_plan_ref = "reports/PRD-1/flow-gap-plan.json"
    events = list(enumerate([
        ZfEvent(
            type="flow.discovery.requested",
            id="fdr1",
            payload={
                "pdd_id": "PRD-1",
                "flow_kind": "prd",
                "fanout_id": "flow-discovery-1",
                "task_map_ref": base_task_map_ref,
            },
        ),
        ZfEvent(
            type="flow.discovery.completed",
            id="fdc1",
            payload={
                "pdd_id": "PRD-1",
                "flow_kind": "prd",
                "fanout_id": "flow-discovery-1",
                "task_map_ref": base_task_map_ref,
            },
        ),
        ZfEvent(
            type="flow.gap_plan.ready",
            id="fgp1",
            payload={
                "pdd_id": "PRD-1",
                "flow_kind": "prd",
                "goal_kind": "prd",
                "gap_category": "acceptance_gap",
                "task_map_ref": base_task_map_ref,
                "gap_plan_ref": gap_plan_ref,
                "gap_tasks": [{
                    "task_id": "PRD-1-GAP-001",
                    "title": "fill product completeness gap",
                }],
            },
        ),
        ZfEvent(
            type="task_map.amended",
            id="tma1",
            payload={
                "pdd_id": "PRD-1",
                "task_map_ref": base_task_map_ref,
                "new_task_map_ref": amended_task_map_ref,
                "gap_plan_ref": gap_plan_ref,
                "gap_task_ids": ["PRD-1-GAP-001"],
            },
        ),
        ZfEvent(
            type="task_map.ready",
            id="tmr1",
            payload={
                "pdd_id": "PRD-1",
                "task_map_ref": amended_task_map_ref,
                "gap_plan_ref": gap_plan_ref,
                "resume_scope": "gap_tasks_only",
                "task_ids": ["PRD-1-GAP-001"],
            },
        ),
    ]))

    trace = build_delivery_trace(
        feature_id="PRD-1",
        generated_at=_NOW,
        tasks=tasks,
        task_map=_task_map(),
        events=events,
        task_map_ref=base_task_map_ref,
    )

    loop = trace["goal_closure_loop"]
    assert loop["schema_version"] == "goal-closure-loop.v2"
    assert loop["status"] == "gap_tasks_dispatched"
    assert loop["scan_request_count"] == 1
    assert loop["scan_result_count"] == 1
    assert loop["gap_plan_count"] == 1
    assert loop["amend_count"] == 2
    assert loop["gap_task_ids"] == ["PRD-1-GAP-001"]
    assert loop["latest_gap_plan_ref"] == gap_plan_ref
    assert loop["latest_task_map_ref"] == amended_task_map_ref


def test_goal_closure_lifecycle_does_not_mix_another_run() -> None:
    events = list(enumerate([
        ZfEvent(
            type="flow.goal.closed",
            id="closed-a",
            correlation_id="run-a",
            payload={
                "workflow_run_id": "run-a",
                "feature_id": "F-1",
                "goal_id": "F-1",
            },
        ),
        ZfEvent(
            type="run.goal.completion.claimed",
            id="claim-b",
            correlation_id="run-b",
            payload={
                "workflow_run_id": "run-b",
                "claim_id": "claim-b",
            },
        ),
        ZfEvent(
            type="run.goal.completion.claimed",
            id="claim-a",
            correlation_id="run-a",
            payload={
                "workflow_run_id": "run-a",
                "claim_id": "claim-a",
            },
        ),
        ZfEvent(
            type="run.goal.completed",
            id="complete-a",
            correlation_id="run-a",
            payload={
                "workflow_run_id": "run-a",
                "claim_id": "claim-a",
            },
        ),
    ]))

    trace = build_delivery_trace(
        feature_id="F-1",
        generated_at=_NOW,
        tasks={},
        events=events,
    )

    loop = trace["goal_closure_loop"]
    assert loop["status"] == "goal_completed"
    assert [row["event_id"] for row in loop["lifecycle"]] == [
        "closed-a", "claim-a", "complete-a",
    ]
    assert loop["completion_event_id"] == "complete-a"


def test_ship_consumes_real_ship_event():
    # doc 69 S-e: ship section reflects actual ship.completed, not just readiness
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="done", wave=2)}
    events = [
        (1, ZfEvent(type="ship.completed", id="sh1", task_id="T1",
                    payload={"final_commit": "abc123", "feature_id": "F-1"})),
        (2, ZfEvent(type="candidate.integration.completed", id="ci1", task_id="T2")),
    ]
    trace = build_delivery_trace(feature_id="F-1", generated_at=_NOW, tasks=tasks,
                                 task_map=_task_map(), events=events)
    ship = trace["ship"]
    assert ship["shipped"] is True
    assert ship["ship_status"] == "completed"
    assert ship["merge_ref"] == "abc123"
    assert ship["candidate_status"] == "integrated"
    assert ship["readiness"] == "ready"  # all done, no error drift


def test_ship_blocker_event_populates_release_blockers():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="in_progress", wave=2)}
    events = [(1, ZfEvent(type="ship.blocked", id="sb1", task_id="T1",
                          payload={"feature_id": "F-1"}))]
    trace = build_delivery_trace(feature_id="F-1", generated_at=_NOW, tasks=tasks,
                                 task_map=_task_map(), events=events)
    ship = trace["ship"]
    assert ship["shipped"] is False
    assert ship["ship_status"] == "blocked"
    assert any(b["kind"] == "ship.blocked" for b in ship["release_blockers"])


def test_ship_ignores_other_feature_events():
    # ship event tied to a task NOT in this feature → not attributed
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="done", wave=2)}
    events = [(1, ZfEvent(type="ship.completed", id="x", task_id="OTHER-9",
                          payload={"final_commit": "zzz"}))]
    trace = build_delivery_trace(feature_id="F-1", generated_at=_NOW, tasks=tasks,
                                 task_map=_task_map(), events=events)
    assert trace["ship"]["shipped"] is False  # not this feature


def _tm_publish(event_id, *, version, supersedes="", summary="task map"):
    from zf.core.events.model import ZfEvent
    return ZfEvent(
        id=event_id, type="artifact.manifest.published", actor="arch",
        task_id="T-arch", ts="2026-05-30T00:00:00+00:00",
        payload={"feature_id": "F-1", "role": "arch", "artifact_refs": [{
            "kind": "task_map", "path": ".zf/artifacts/F-1/task_map.json",
            "sha256": "a" * 64, "summary": summary, "status": "accepted",
            "artifact_id": f"tm-v{version}", "version": version,
            "supersedes": supersedes,
        }]},
    )


def test_replan_history_recorded_in_trace():
    # doc 69 §14.10 (S-k): plan re-cut v1 -> v2 surfaces a version chain.
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="in_progress", wave=2)}
    events = list(enumerate([
        _tm_publish("e1", version=1),
        _tm_publish("e2", version=2, supersedes="tm-v1", summary="re-cut router"),
    ]))
    trace = build_delivery_trace(feature_id="F-1", generated_at=_NOW,
                                 tasks=tasks, task_map=_task_map(), events=events)
    hist = trace["task_map_history"]
    assert len(hist) == 2
    assert hist[0]["artifact_id"] == "tm-v1" and hist[0]["superseded"] is True
    assert hist[1]["is_current"] is True and hist[1]["reason"] == "re-cut router"


def test_no_replan_single_history_entry():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="in_progress", wave=2)}
    events = list(enumerate([_tm_publish("e1", version=1)]))
    trace = build_delivery_trace(feature_id="F-1", generated_at=_NOW,
                                 tasks=tasks, task_map=_task_map(), events=events)
    assert len(trace["task_map_history"]) == 1
    assert trace["task_map_history"][0]["is_current"] is True


def test_no_task_map_history_when_no_manifest():
    tasks = {"T1": _task("T1", status="done", wave=1)}
    trace = build_delivery_trace(feature_id="F-1", generated_at=_NOW,
                                 tasks=tasks, task_map=_task_map())
    assert trace["task_map_history"] == []


def test_replan_appends_superseded_node_without_skewing_metrics():
    # doc 69 §14.10 (S-k): T3 dropped from current task-map (only T1,T2) but
    # lingers cancelled in kanban → appears as a greyed superseded node.
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="in_progress", wave=2),
             "T3": _task("T3", status="cancelled", wave=2)}
    events = list(enumerate([
        _tm_publish("e1", version=1),
        _tm_publish("e2", version=2, supersedes="tm-v1", summary="re-cut router"),
    ]))
    trace = build_delivery_trace(feature_id="F-1", generated_at=_NOW,
                                 tasks=tasks, task_map=_task_map(), events=events)
    eg = trace["execution_graph"]
    nodes = {n["task_id"]: n for n in eg["nodes"]}
    assert nodes["T3"].get("superseded") is True
    assert "superseded" not in nodes["T1"] and "superseded" not in nodes["T2"]
    # appended after metrics → counts/nodes reflect overlay-only semantics
    assert eg["task_count"] == 2          # planned tasks only
    assert len(eg["nodes"]) == 3          # + 1 superseded overlay
    assert eg["done_count"] == 1          # T3(cancelled) NOT counted as done


def test_no_replan_does_not_append_superseded_nodes():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="in_progress", wave=2),
             "T3": _task("T3", status="cancelled", wave=2)}
    events = list(enumerate([_tm_publish("e1", version=1)]))  # single version
    trace = build_delivery_trace(feature_id="F-1", generated_at=_NOW,
                                 tasks=tasks, task_map=_task_map(), events=events)
    nodes = {n["task_id"] for n in trace["execution_graph"]["nodes"]}
    assert nodes == {"T1", "T2"}  # T3 not surfaced without a re-plan


def test_delivery_trace_includes_replan_contract_eval_card():
    tasks = {"T1": _task("T1", status="done", wave=1)}
    events = [(1, ZfEvent(
        type="replan.contract_eval.completed",
        id="eval-event-1",
        payload={
            "feature_id": "F-1",
            "eval_id": "eval-1",
            "decision": "adopt",
            "profile": "baseline",
            "old_task_map_ref": "tm-v1",
            "new_task_map_ref": "tm-v2",
            "contract_delta_counts": {
                "preserve": 0,
                "cancel": 1,
                "rewrite": 0,
                "new": 1,
            },
            "refs": {"artifact_ref": ".zf/artifacts/F-1/replan-eval.json"},
        },
    ))]

    trace = build_delivery_trace(
        feature_id="F-1",
        generated_at=_NOW,
        tasks=tasks,
        task_map=_task_map(),
        events=events,
        task_map_ref="tm-v2",
    )

    gate = trace["replan_contract_gate"]
    assert gate["schema_version"] == "replan-contract-gate-projection.v1"
    assert gate["status"] == "ready_to_adopt"
    assert gate["latest_eval"]["decision"] == "adopt"
    assert gate["latest_eval"]["contract_delta_counts"]["new"] == 1
    assert gate["latest_eval"]["artifact_ref"].endswith("replan-eval.json")


def test_delivery_trace_marks_stale_replan_adoption_rejected():
    tasks = {"T1": _task("T1", status="in_progress", wave=1)}
    events = [(1, ZfEvent(
        type="replan.adoption.stale_rejected",
        id="stale-1",
        payload={
            "feature_id": "F-1",
            "task_map_ref": "tm-v2",
            "expected_current_task_map_ref": "tm-v1",
            "latest_task_map_ref": "tm-v3",
            "eval": {
                "eval_id": "eval-stale",
                "decision": "adopt",
                "old_task_map_ref": "tm-v1",
                "new_task_map_ref": "tm-v2",
            },
        },
    ))]

    trace = build_delivery_trace(
        feature_id="F-1",
        generated_at=_NOW,
        tasks=tasks,
        task_map=_task_map(),
        events=events,
        task_map_ref="tm-v1",
    )

    assert trace["task_map"]["task_map_ref"] == "tm-v1"
    assert trace["replan_contract_gate"]["status"] == "stale_rejected"
    assert trace["replan_contract_gate"]["latest_eval"]["new_task_map_ref"] == "tm-v2"


def test_delivery_trace_projects_delivery_cycles_from_events():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="in_progress", wave=2)}
    events = [
        (1, ZfEvent(
            type="fanout.started",
            id="fo-start",
            payload={
                "fanout_id": "fanout-impl",
                "feature_id": "F-1",
                "task_ids": ["T1", "T2"],
                "topology": "fanout_writer_scoped",
                "stage_id": "impl",
            },
        )),
        (2, ZfEvent(
            type="fanout.aggregate.completed",
            id="fo-done",
            payload={
                "fanout_id": "fanout-impl",
                "feature_id": "F-1",
                "task_ids": ["T1", "T2"],
                "status": "completed",
            },
        )),
        (3, ZfEvent(
            type="task.rework.requested",
            id="rw1",
            task_id="T2",
            payload={"reason": "verify.failed"},
        )),
        (4, ZfEvent(
            type="replan.contract_eval.completed",
            id="rp1",
            payload={
                "feature_id": "F-1",
                "eval_id": "eval-1",
                "decision": "adopt",
                "old_task_map_ref": "tm-v1",
                "new_task_map_ref": "tm-v2",
                "refs": {"artifact_ref": ".zf/artifacts/F-1/replan-eval.json"},
            },
        )),
        (5, ZfEvent(
            type="ship.completed",
            id="ship1",
            task_id="T1",
            payload={"feature_id": "F-1", "final_commit": "abc123"},
        )),
    ]

    trace = build_delivery_trace(
        feature_id="F-1",
        generated_at=_NOW,
        tasks=tasks,
        task_map=_task_map(),
        events=events,
        task_map_ref="tm-v2",
    )

    cycles = trace["cycles"]
    kinds = {cycle["kind"] for cycle in cycles}
    assert {"planned_phase", "fanout", "rework", "replan", "ship"} <= kinds
    fanout = next(cycle for cycle in cycles if cycle["kind"] == "fanout")
    assert fanout["cycle_id"] == "fanout:fanout-impl"
    assert fanout["status"] == "completed"
    assert fanout["task_ids"] == ["T1", "T2"]
    replan = next(cycle for cycle in cycles if cycle["kind"] == "replan")
    assert replan["status"] == "ready_to_adopt"
    assert replan["gate"] == "adopt"
    assert "rp1" in replan["evidence_refs"]
    ship = next(cycle for cycle in cycles if cycle["kind"] == "ship")
    assert ship["status"] == "shipped"
    assert "ship1" in ship["evidence_refs"]


def test_delivery_trace_projects_replan_cycle_from_insight_to_eval_request():
    tasks = {"T1": _task("T1", status="in_progress", wave=1)}
    events = [
        (1, ZfEvent(
            type="plan.insight.discovered",
            id="pins1",
            payload={
                "feature_id": "F-1",
                "insight_id": "pins-1",
                "task_map_ref": "tm-v2",
                "recommended_route": "research_probe",
            },
        )),
        (2, ZfEvent(
            type="research.probe.requested",
            id="probe1",
            payload={
                "feature_id": "F-1",
                "source_insight_ref": "projection:supervisor/plan-insights.json#pins-1",
                "candidate_task_map_ref": "tm-v2",
            },
        )),
        (3, ZfEvent(
            type="replan.proposal.created",
            id="proposal1",
            payload={
                "feature_id": "F-1",
                "proposal_ref": ".zf/autoresearch/replan.json",
                "candidate_task_map_ref": "tm-v2",
                "evidence_refs": ["probe1"],
            },
        )),
        (4, ZfEvent(
            type="replan.contract_eval.requested",
            id="eval-request1",
            payload={
                "feature_id": "F-1",
                "request_id": "replan-eval-1",
                "proposal_ref": ".zf/autoresearch/replan.json",
                "candidate_task_map_ref": "tm-v2",
            },
        )),
        (5, ZfEvent(
            type="replan.contract_eval.completed",
            id="eval1",
            payload={
                "feature_id": "F-1",
                "eval_id": "eval-1",
                "decision": "revise",
                "new_task_map_ref": "tm-v2",
            },
        )),
    ]

    trace = build_delivery_trace(
        feature_id="F-1",
        generated_at=_NOW,
        tasks=tasks,
        task_map=_task_map(),
        events=events,
        task_map_ref="tm-v2",
    )

    replan = next(cycle for cycle in trace["cycles"] if cycle["kind"] == "replan")
    assert replan["cycle_id"] == "replan:tm-v2"
    assert replan["status"] == "revise"
    assert replan["proposal_ref"] == ".zf/autoresearch/replan.json"
    assert {"plan.insight.discovered", "research.probe.requested", "replan.proposal.created", "replan.contract_eval.requested", "replan.contract_eval.completed"} <= {
        item["event_type"] for item in replan["events"]
    }


def test_delivery_trace_replan_owner_decision_overrides_gate_status():
    tasks = {"T1": _task("T1", status="in_progress", wave=1)}
    events = [
        (1, ZfEvent(
            type="replan.contract_eval.completed",
            id="eval1",
            payload={
                "feature_id": "F-1",
                "eval_id": "eval-1",
                "decision": "adopt",
                "new_task_map_ref": "tm-v2",
            },
        )),
        (2, ZfEvent(
            type="replan.owner_decision.rejected",
            id="owner1",
            payload={
                "feature_id": "F-1",
                "proposal_ref": ".zf/autoresearch/replan.json",
                "eval_ref": "eval-1",
                "candidate_task_map_ref": "tm-v2",
                "reason": "owner rejected after review",
            },
        )),
    ]

    trace = build_delivery_trace(
        feature_id="F-1",
        generated_at=_NOW,
        tasks=tasks,
        task_map=_task_map(),
        events=events,
        task_map_ref="tm-v2",
    )

    replan = next(cycle for cycle in trace["cycles"] if cycle["kind"] == "replan")
    assert replan["status"] == "owner_rejected"
    assert replan["events"][-1]["event_type"] == "replan.owner_decision.rejected"


def test_delivery_trace_projects_autoresearch_cycle_score_and_evidence():
    tasks = {"T1": _task("T1", status="in_progress", wave=1)}
    events = [
        (1, ZfEvent(
            type="autoresearch.invocation.requested",
            id="ar-req",
            task_id="T1",
            correlation_id="ar-run-1",
            payload={
                "trigger": "verify.failed",
                "policy": "proposal_only",
                "sandbox_required": True,
                "baseline_score": 61.4,
            },
        )),
        (2, ZfEvent(
            type="autoresearch.invocation.accepted",
            id="ar-accepted",
            task_id="T1",
            correlation_id="ar-run-1",
            payload={"feature_id": "F-1"},
        )),
        (3, ZfEvent(
            type="autoresearch.loop.started",
            id="ar-start",
            task_id="T1",
            correlation_id="ar-run-1",
            payload={"feature_id": "F-1"},
        )),
        (4, ZfEvent(
            type="autoresearch.loop.completed",
            id="ar-done",
            task_id="T1",
            correlation_id="ar-run-1",
            payload={
                "feature_id": "F-1",
                "candidate_score": 75.6,
                "score_delta": 14.2,
                "deposition": "proposal_only",
                "artifact_ref": ".zf/artifacts/F-1/autoresearch.json",
            },
        )),
    ]

    trace = build_delivery_trace(
        feature_id="F-1",
        generated_at=_NOW,
        tasks=tasks,
        task_map=_task_map(),
        events=events,
    )

    cycles = trace["autoresearch_cycles"]
    assert len(cycles) == 1
    cycle = cycles[0]
    assert cycle["cycle_id"] == "autoresearch:ar-run-1"
    assert cycle["status"] == "completed"
    assert cycle["trigger"] == "verify.failed"
    assert cycle["policy"] == "proposal_only"
    assert cycle["deposition"] == "proposal_only"
    assert cycle["sandbox"] == "required"
    assert cycle["baseline_score"] == 61.4
    assert cycle["candidate_score"] == 75.6
    assert cycle["score_delta"] == 14.2
    assert cycle["task_ids"] == ["T1"]
    assert {"ar-req", "ar-accepted", "ar-start", "ar-done"} <= set(cycle["evidence_refs"])
    assert ".zf/artifacts/F-1/autoresearch.json" in cycle["evidence_refs"]


def test_delivery_trace_projects_autoresearch_review_gate_cycle():
    tasks = {"T1": _task("T1", status="in_progress", wave=1)}
    events = [
        (1, ZfEvent(
            type="autoresearch.review_gate.requested",
            id="rg-req",
            task_id="T1",
            payload={
                "request_id": "rg-1",
                "feature_id": "F-1",
                "mode": "auto",
                "route": "fanout_gate",
                "severity": "high",
                "triggered": True,
                "attempt": 1,
                "attempt_cap": 2,
                "budget_cap": {"max_runs": 1, "max_minutes": 45},
                "artifact_refs": {
                    "summary": ".zf/autoresearch/runs/r1/review-gate/summary.json",
                },
            },
        )),
        (2, ZfEvent(
            type="autoresearch.review_gate.started",
            id="rg-start",
            task_id="T1",
            payload={"request_id": "rg-1", "feature_id": "F-1"},
        )),
        (3, ZfEvent(
            type="autoresearch.review_gate.completed",
            id="rg-done",
            task_id="T1",
            payload={
                "request_id": "rg-1",
                "feature_id": "F-1",
                "decision": "approve",
                "accepted": True,
                "artifact_refs": {
                    "summary": ".zf/autoresearch/runs/r1/review-gate/summary.json",
                    "closeout": ".zf/autoresearch/runs/r1/review-gate/closeout.json",
                },
            },
        )),
    ]

    trace = build_delivery_trace(
        feature_id="F-1",
        generated_at=_NOW,
        tasks=tasks,
        task_map=_task_map(),
        events=events,
    )

    cycle = trace["autoresearch_cycles"][0]
    assert cycle["cycle_id"] == "autoresearch:rg-1"
    assert cycle["kind"] == "autoresearch_review_gate"
    assert cycle["status"] == "completed"
    assert cycle["review_gate"]["route"] == "fanout_gate"
    assert cycle["review_gate"]["attempt_cap"] == 2
    assert cycle["review_gate"]["decision"] == "approve"
    assert ".zf/autoresearch/runs/r1/review-gate/closeout.json" in cycle["evidence_refs"]
