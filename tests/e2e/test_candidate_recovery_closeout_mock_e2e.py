"""One ledger: Reader infrastructure failure -> diagnosis -> authorized repair -> Goal.

Provider results and the Owner's repair approval are scripted test actors.
Git probes, result admission, controlled application and final closure are real;
this does not claim autonomous diagnosis quality or real-provider qualification.
"""
import json

import pytest

from tests.e2e.test_agent_owned_verification_mock_e2e import (
    _Transport, _dispatch_payload, _record_required_reads, test_light_profile_mock_e2e_exact_verify as _scenario,
)
from zf.core.events import ZfEvent
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_manager import build_run_manager_projection

pytestmark = pytest.mark.mock_e2e


def test_failed_candidate_reader_recovers_through_controlled_action_and_finishes_goal(tmp_path, monkeypatch):
    def recover(runtime, manifest, submitter, tokens):
        log = runtime.event_log
        before = log.read_all()
        done_before = [e.id for e in before if e.type == "task.done"]
        for child in manifest["children"]:
            payload = _dispatch_payload(child)
            _record_required_reads(runtime.state_dir, payload)
            failed = submitter.submit(operation_id=payload["operation_id"], semantic_result={
                "schema_version": "workflow-read-result.v1", "status": "failed", "recommendation": "abstain",
                "summary": "Mock provider cannot read the mounted verification input.",
                "failure_class": "reader_execution_failure", "requirement_results": [],
                "evidence_refs": [], "rework_items": [],
            }, role_instance=child["role_instance"], credential=tokens[child["role_instance"]])
            result = next(e for e in log.read_all() if e.id == failed.canonical_event_id)
            runtime.run_once(events=[result])
        failure = next(e for e in reversed(log.read_all()) if e.type == "flow.discovery.failed")
        runtime.run_once(events=[failure])
        projection = build_run_manager_projection(runtime.state_dir, config=runtime.config,
            events=log.read_all(), project_root=tmp_path)
        assert any(a["action"] == "diagnose-attention" and failure.id in a.get("source_event_ids", [])
                   for a in projection["pending_actions"]), projection["pending_actions"]
        assert not any(e.type == "orchestrator.replan_requested" for e in log.read_all())
        # The isolated test fixes its mock input provider and supplies the
        # explicit Owner repair approval required by the production action.
        payload = {"repaired_failure_event_id": failure.id, "approval_ref": "test-owner-repaired-input",
                   "reason": "Mock input transport repaired; unchanged frozen Candidate must be reverified."}
        requested = runtime.event_writer.append(ZfEvent(type="control.action.requested", origin="operator",
            actor="mock-operator", payload=payload, correlation_id=failure.correlation_id))
        service = ControlledActionService(runtime.state_dir, runtime.event_writer, config=runtime.config,
            project_root=tmp_path, actor="mock-operator", surface="test")
        outcome = service.execute(action="stage-replan-new-generation", requested_action="stage-replan-new-generation",
            payload=payload, requested=requested)
        assert outcome["ok"], outcome
        trigger = next(e for e in reversed(log.read_all()) if e.type == "flow.discovery.requested")
        assert trigger.payload["repaired_failure_event_id"] == failure.id
        # Lose the notification and controller object. Resume from persisted
        # facts, then deliver the same wake twice to check dispatch idempotency.
        fresh = Orchestrator(runtime.state_dir, runtime.config, _Transport(), project_root=tmp_path)
        fresh.run_once(events=[trigger])
        fresh.run_once(events=[trigger])
        started = [e for e in log.read_all() if e.type == "fanout.started" and e.payload.get("trigger_event_id") == trigger.id]
        assert len(started) == 1
        assert [e.id for e in log.read_all() if e.type == "task.done"] == done_before
        resumed = json.loads((runtime.state_dir / "fanouts" / started[0].payload["fanout_id"] / "manifest.json").read_text())
        return fresh, resumed

    _scenario(tmp_path, monkeypatch, discovery_closeout=True, reader_recovery_hook=recover)
