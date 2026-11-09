import pytest
from dataclasses import replace
from types import SimpleNamespace
from zf.core.task.store import TaskStore
from zf.core.events.writer import EventWriter
from zf.core.events.model import ZfEvent
from zf.runtime.task_contract_snapshot import hydrate_task_contract_snapshot
from tests.test_reader_replan_plan_binding import _dispatched_replan


@pytest.mark.parametrize(
    "change",
    ["none", "source_contract", "source_cancelled", "plan", "target", "domain"],
)
def test_rescan_dispatch_template_and_result_admission_share_current_authority(
    tmp_path, change
):
    state, log, orch, child, original, transport = _dispatched_replan(tmp_path)
    snapshot = hydrate_task_contract_snapshot(
        state,
        {
            "ref": child["contract_snapshot_ref"],
            "sha256": child["contract_snapshot_digest"],
        },
    )
    assert snapshot["task_map_generation"] == "generation-2"
    assert snapshot["authority_scope"] == "global-rescan"
    assert snapshot["source_contract_snapshot_ref"] == original["ref"]
    assert child["attempt_domain"] == "gap"
    briefing = transport.sent[0][1].read_text()
    assert '"task_map_generation": "generation-1"' not in briefing
    source = ZfEvent(
        type="flow.discovery.child.failed",
        actor="discovery",
        payload={
            **child,
            "status": "failed",
            "reason": "missing bounded slice",
            "report": {
                "workflow_run_id": "workflow-1",
                "summary": "Missing bounded slice",
                "recommendation": "reject",
                "findings": [
                    {
                        "severity": "high",
                        "path": "app/missing.py",
                        "message": "implementation missing",
                    }
                ],
                "requirement_coverage_matrix": [
                    {
                        "acceptance_id": "GAP-CURRENT-AC1",
                        "status": "failed",
                        "findings": [
                            {
                                "severity": "high",
                                "path": "app/missing.py",
                                "message": "implementation missing",
                            }
                        ],
                        "evidence_refs": ["git:partial456"],
                    }
                ],
            },
        },
    )
    original_bytes = (state / original["ref"]).read_bytes()
    replay = SimpleNamespace(payload=dict(child), target_ref=child["target_ref"])
    orch._prepare_reader_contract_target(replay)
    assert replay.payload["contract_snapshot_ref"] == child["contract_snapshot_ref"]
    assert replay.payload["target_snapshot_ref"] == child["target_snapshot_ref"]
    assert (state / original["ref"]).read_bytes() == original_bytes
    if change == "source_contract":
        store = TaskStore(state / "kanban.json")
        task = store.get("GAP-CURRENT")
        store.update(task.id, contract=replace(task.contract, behavior="new behavior"))
    elif change == "source_cancelled":
        TaskStore(state / "kanban.json").update("GAP-CURRENT", status="cancelled")
    elif change == "plan":
        EventWriter(log).emit(
            "plan.artifact_package.admitted",
            payload={
                "workflow_run_id": "workflow-1",
                "package_slot": "execution_plan",
                "status": "admitted",
                "task_map_generation": "generation-3",
                "plan_revision": "generation-3",
                "package_id": "planpkg-newer",
                "package_ref": "artifacts/new.json",
                "package_digest": "n" * 64,
            },
        )
    elif change == "target":
        source.payload["target_commit"] = "foreign-target"
    elif change == "domain":
        source.payload["attempt_domain"] = "task"
    orch.run_once(events=[source])
    invalid = [
        e.payload.get("issues")
        for e in log.read_all()
        if e.type == "workflow.call.result.invalid"
    ]
    if change != "none":
        assert invalid, change
        assert not any(
            e.type == "workflow.call.result.admitted"
            and e.payload.get("source_event_id") == source.id
            for e in log.read_all()
        )
        return
    assert not invalid, invalid
    reported = [
        e.payload.get("issues")
        for e in log.read_all()
        if e.type == "workflow.call.result.reported"
    ]
    assert not any(reported), reported
    assert any(
        e.type == "workflow.call.result.admitted"
        and e.payload.get("source_event_id") == source.id
        for e in log.read_all()
    )
    assert any(
        e.type == "workflow.operation.settled"
        and e.payload.get("operation_id") == child["operation_id"]
        for e in log.read_all()
    )
