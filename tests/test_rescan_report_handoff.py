"""Admitted verification keeps its authored gap handoff, never scratch edits."""
import json

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from tests.test_reader_replan_plan_binding import _dispatched_replan


@pytest.mark.parametrize("explicit", [False, True])
def test_admitted_rescan_keeps_gap_report_through_aggregate(tmp_path, explicit, monkeypatch):
    state, log, orch, child, _, _ = _dispatched_replan(tmp_path)
    # This report-handoff fixture uses a synthetic Candidate rather than a Git repo.
    monkeypatch.setattr('zf.runtime.candidate_continuation._git_commit', lambda *_: 'partial456')
    source = ZfEvent(type="flow.discovery.child.failed", actor="discovery", payload={
        **child, "status": "failed", "reason": "missing implementation",
        "report": {
            "status": "failed", "summary": "missing implementation", "recommendation": "reject",
            "findings": [{"severity": "high", "path": "app/missing.py", "message": "missing"}],
            "requirement_coverage_matrix": [{
                "acceptance_id": "GAP-CURRENT-AC1", "status": "failed",
                "findings": [{"severity": "high", "path": "app/missing.py", "message": "missing"}],
                "evidence_refs": ["git:partial456"],
            }],
            "gap_tasks": [{"task_id": "GAP-REPAIR", "title": "Implement missing slice",
                           "claim_paths": ["app/missing.py"], "acceptance": ["slice works"],
                           "verify_commands": ["pytest tests/test_missing.py"],
                           "source_refs": ["git:partial456"]}],
            "plan_reconciliation": {"preserve_completed": ["DONE-1"]},
        },
    })
    if explicit:
        from zf.runtime.call_result_adapters import _legacy_verification_result
        source.payload["verification_result"] = _legacy_verification_result(source)
    EventWriter(log).append(source)
    orch.run_once(events=[source])
    manifest = orch._fanout_manifest(child["fanout_id"])
    assert manifest["children"][0].get("admitted_call_result_ref")
    scratch = state / "fanouts" / child["fanout_id"] / "children" / child["child_id"] / "result.json"
    scratch.write_text(json.dumps({"payload": {"report": {"gap_tasks": [{"task_id": "POISON"}]}}}))
    EventWriter(log).append(ZfEvent(type="flow.discovery.child.failed", actor="discovery",
                                   payload={**child, "report": {"gap_tasks": [{"task_id": "LATER-UNADMITTED"}]}}))
    result = orch._fanout_child_payloads(manifest)[0]["report"]
    assert result["gap_tasks"] == source.payload["report"]["gap_tasks"]
    assert result["plan_reconciliation"] == {"preserve_completed": ["DONE-1"]}
    assert result["verdict"] == "rejected"
    aggregate = orch._generic_fanout_success_payload(manifest=manifest, success_event="flow.discovery.completed")
    assert aggregate["gap_tasks"] == source.payload["report"]["gap_tasks"]
    assert aggregate["gap_task_count"] == 1
    from zf.runtime.control_actions import ControlledActionService
    service = ControlledActionService(state, EventWriter(log), config=orch.config, project_root=tmp_path)
    source_aggregate = next(e for e in log.read_all() if e.type == "flow.discovery.failed")
    request = EventWriter(log).emit("control.action.requested", payload={"action": "fanout-aggregate-rebuild"})
    before_dispatch = sum(e.type == "fanout.child.dispatched" for e in log.read_all())
    rebuilt = service.execute(requested=request, action="fanout-aggregate-rebuild",
                              requested_action="fanout-aggregate-rebuild",
                              payload={"source_event_id": source_aggregate.id, "reason": "restore admitted handoff"})
    assert rebuilt["ok"], rebuilt
    rebuild = next(e for e in log.read_all() if e.type == "fanout.aggregate.rebuild.requested")
    orch.run_once(events=[rebuild])
    results = [e for e in log.read_all() if e.type == "flow.discovery.failed" and e.payload.get("rebuild_of") == source_aggregate.id]
    assert len(results) == 1
    assert results[0].payload["gap_tasks"] == source.payload["report"]["gap_tasks"]
    assert sum(e.type == "fanout.child.dispatched" for e in log.read_all()) == before_dispatch
    orch.run_once(events=[rebuild])
    assert len([e for e in log.read_all() if e.type == "flow.discovery.failed" and e.payload.get("rebuild_of") == source_aggregate.id]) == 1


@pytest.mark.parametrize("fault", ["wrong_failure", "pending"])
def test_failed_discovery_rebuild_rejects_ineligible_manifest(tmp_path, fault):
    from tests.test_control_actions_recovery import _service, _req
    service = _service(tmp_path)
    manifest = service.state_dir / "fanouts" / "failed-discovery" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "fanout_id": "failed-discovery",
        "aggregate_config": {"success_event": "flow.discovery.completed",
                             "failure_event": "other.failed" if fault == "wrong_failure" else "flow.discovery.failed"},
        "children": [{"child_id": "reader", "status": "dispatched" if fault == "pending" else "failed"}],
    }))
    source = service.writer.append(ZfEvent(type="flow.discovery.failed", payload={"fanout_id": "failed-discovery"}))
    result = service._fanout_aggregate_rebuild_action(requested=_req({}), action="fanout-aggregate-rebuild",
        requested_action="fanout-aggregate-rebuild", payload={"source_event_id": source.id})
    assert not result["ok"]
    assert not any(e.type == "fanout.aggregate.rebuild.requested" for e in service.writer.event_log.read_all())
