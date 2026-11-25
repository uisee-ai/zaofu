import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


def capture(path, body):
    path.write_text(json.dumps(body))
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def module():
    path = Path(__file__).parents[1] / "scripts/compare-stage-team-pilot.py"
    spec = importlib.util.spec_from_file_location("compare_pilot", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def pilot(tmp_path):
    parameters = {k: k + "-v1" for k in ("provider_profiles", "capacity", "permissions", "target_baseline",
                                        "fault_scenario", "acceptance_contract", "requirements")}
    controlled = capture(tmp_path / "inputs.json", {"controls": parameters,
                         "effective_actions": {"child-rebuild": ["fanout.child.completed"]}})
    pair = {"evidence_mode": "deterministic_replay", "authorized_at": "2026-11-21T10:00:00+00:00",
            "experiment_id": "test-paired-window", "authorization_ref": "fixture", "controls": controlled, "arms": []}
    for group in ("A", "B"):
        state = tmp_path / group
        state.mkdir()
        run = "run-" + group
        descriptor = write_immutable_json_sidecar(state, {
            "schema_version": "effective-config-snapshot.v1", "config": {
                "workflow": {"orchestration": {"normal_frontier_owner": "wrc", "oa_profile": "oa"}},
                "runtime": {"run_manager": {"resident_agent": {"enabled": group == "A"}, "reflect": {"enabled": group == "B"}},
                            "autoresearch_resident": {"enabled": False, "self_repair_consumer": False}}}
        }, root="workflow/proposals", kind="effective_config_snapshot",
            schema_version="effective-config-snapshot.v1", created_by="test")
        events = [("invoke", "workflow.invoke.requested", {"effective_config_ref": descriptor,
                   "workflow_proposal_ref": {"ref": "test-proposal"}}),
                  ("approved", "plan.approved", {}), ("fault", "fanout.child.failed", {"recovery_case_id": "case-1"}),
                  ("oa", "workflow.operation.started", {"operation_type": "orchestrator_agent_semantic"}),
                  ("diagnosis", "run.manager.action.effect.passed", {"action": "resident-agent-reprompt",
                   "status": "passed", "recovery_case_id": "case-1"}),
                  ("rebuilt", "fanout.child.completed", {}), ("effect", "run.manager.action.effect.passed", {
                       "action": "child-rebuild", "status": "passed", "recovery_case_id": "case-1",
                       "observed_event_type": "fanout.child.completed", "observed_event_id": "rebuilt"}),
                  ("covered", "task.done.accepted", {}), ("judge", "judge.passed", {}),
                  ("shipped", "ship.completed", {}), ("completed", "run.goal.completed", {})]
        log = EventLog(state / "events.jsonl")
        for minute, (id_, kind, body) in enumerate(events):
            log.append(ZfEvent(type=kind, id=id_, correlation_id=run, actor="zf-cli", origin="kernel",
                               causation_id="rebuilt" if id_ == "effect" else None,
                               ts=f"2026-11-21T10:{minute:02d}:00+00:00", payload={"workflow_run_id": run, **body}))
        audit = {"run_id": run, "evidence_mode": pair["evidence_mode"], "controls_sha256": controlled["sha256"],
                 "invoke": "invoke", "plan_approval": "approved", "window_end": "completed",
                 "fault": "fault", "recovery_case_id": "case-1", "interventions": [],
                 "model_handoff_event_ids": ["oa"], "unowned_waits": [],
                 "coverage": {"evaluation": "fixture-covered", "event_ids": ["covered"]}, **parameters}
        pair["arms"].append({"group": group, "run_id": run, "state_dir": group,
                             "audit": capture(tmp_path / f"audit-{group}.json", audit)})
    return tmp_path / "pair.json", pair


def test_pair_reports_actual_repair_effect_and_separate_judge_delivery(pilot):
    path, pair = pilot
    capture(path, pair)
    report = module().compare(path)
    b = report["arms"][1]
    assert b["first_effective_recovery"]["seconds"] == 240
    assert b["configured_consumers"] == ["on_demand_reflect"] and b["observed_consumer_receipts"]["on_demand_reflect"] == []
    assert b["model_handoffs"] == 1 and b["post_approval_interventions"] == 0
    assert b["judge_receipts"][0]["id"] == "judge"
    assert b["delivery_receipts"][0]["id"] == "shipped"
    assert report["evidence_mode"] == "deterministic_replay"
    assert report["decision"] == "evidence_for_owner_review"


@pytest.mark.parametrize("invalid", ["old-window", "tamper", "foreign-event", "renamed-replay", "mismatched-policy"])
def test_pair_fails_closed_on_unbound_comparisons(pilot, invalid):
    path, pair = pilot
    arm = pair["arms"][1]
    subpath = path.parent / arm["audit"]["path"]
    audit = json.loads(subpath.read_text())
    if invalid == "old-window":
        pair["authorized_at"] = "2026-11-22T00:00:00+00:00"
    elif invalid == "tamper":
        subpath.write_text("{}")
    elif invalid == "renamed-replay":
        pair["evidence_mode"] = "real_provider"
    else:
        if invalid == "foreign-event":
            audit["model_handoff_event_ids"] = ["absent"]
        else:
            audit["permissions"] = "different"
        arm["audit"] = capture(subpath, audit)
    capture(path, pair)
    with pytest.raises(ValueError):
        module().compare(path)


def test_partial_missing_judge_preserves_unknown_and_an_unreconciled_owner_action(pilot):
    path, pair = pilot
    arm = pair["arms"][1]
    audit_path = path.parent / arm["audit"]["path"]
    audit = json.loads(audit_path.read_text())
    audit.update(window_end="covered", interventions=None, unowned_waits=None)
    arm["audit"] = capture(audit_path, audit)
    capture(path, pair)
    b = module().compare(path)["arms"][1]
    assert b["judge_receipts"] == b["delivery_receipts"] == []
    assert b["unowned_wait_seconds"] is None and b["post_approval_interventions"] is None


def test_late_old_judge_and_owner_restart_are_counted_separately(pilot):
    path, pair = pilot
    arm = pair["arms"][1]
    audit_path = path.parent / arm["audit"]["path"]
    audit = json.loads(audit_path.read_text())
    log = EventLog(path.parent / arm["state_dir"] / "events.jsonl")
    for minute, (key, kind, actor, body) in enumerate([
        ("freeze2", "candidate.ready", "zf-cli", {"freeze_id": "gen-2", "schema_version": "candidate-freeze-receipt.v1"}),
        ("owner", "runtime.action.completed", "operator", {"action": "runtime-restart"}),
        ("late", "judge.passed", "zf-cli", {"freeze_id": "gen-1"})], start=11):
        log.append(ZfEvent(type=kind, id=key, actor=actor, origin="kernel", correlation_id=arm["run_id"],
                           ts=f"2026-11-21T10:{minute}:00+00:00", payload={"workflow_run_id": arm["run_id"], **body}))
    audit["window_end"] = "late"
    arm["audit"] = capture(audit_path, audit)
    capture(path, pair)
    b = module().compare(path)["arms"][1]
    assert b["owner_events_for_review"] == ["owner"] and b["post_approval_interventions"] is None
    assert b["judge_receipts"][-1]["binding"] == "older_or_different_target"
    audit["interventions"] = [{"event_id": "owner", "action": "runtime-restart", "evidence": "host capture",
                               "at": "2026-11-21T10:12:00+00:00"}]
    arm["audit"] = capture(audit_path, audit)
    capture(path, pair)
    b = module().compare(path)["arms"][1]
    assert b["post_approval_interventions"] == 1 and b["owner_events_for_review"] == []
