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


@pytest.mark.parametrize("change", ["stage_gate", "capacity", "runtime_recovery", "watchdog", "graph", "tool_permissions"])
def test_mismatched_frozen_common_control_prevents_pair_comparison(pilot, change):
    from copy import deepcopy
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    path, pair = pilot
    arm = pair["arms"][1]
    state = path.parent / arm["state_dir"]
    log = EventLog(state / "events.jsonl")
    events = log.read_all()
    invoke = next(e for e in events if e.id == "invoke")
    body = deepcopy(hydrate_sidecar_ref(state, invoke.payload["effective_config_ref"]).payload)
    config = body["config"]
    if change == "stage_gate":
        config["workflow"]["stages"] = [{"id": "verify", "success": "skip-independent-check"}]
    elif change == "capacity":
        config["workflow"]["flow_metadata"] = {"task_pipeline": {"pools": {"verify": {"capacity": 1}}}}
    elif change == "runtime_recovery":
        config["runtime"]["run_manager"]["source_repair"] = {"enabled": True}
    elif change == "watchdog":
        config["runtime"]["host_watchdog_seconds"] = 120
    elif change == "graph":
        body["compiled_graph"] = {"stage_teams": {"judge": {"author": "different-reader"}}}
    else:
        config["runtime"]["autoresearch_resident"]["self_repair_spawn"] = True
    invoke.payload["effective_config_ref"] = write_immutable_json_sidecar(state, body, root="workflow/proposals",
        kind="effective_config_snapshot", schema_version=body["schema_version"], created_by="test")
    (state / "events.jsonl").unlink()
    for event in events:
        log.append(event)
    capture(path, pair)
    with pytest.raises(ValueError, match="identical experimental controls"):
        module().compare(path)


def test_canonical_signed_judge_and_ship_are_counted_once_with_compatibility_projections(pilot):
    path, pair = pilot
    arm = pair["arms"][1]
    audit_path = path.parent / arm["audit"]["path"]
    audit = json.loads(audit_path.read_text())
    log = EventLog(path.parent / arm["state_dir"] / "events.jsonl")
    head = "1"*40

    def add(eid, kind, cause=None, **body):
        log.append(ZfEvent(id=eid, type=kind, origin="kernel", correlation_id=arm["run_id"], causation_id=cause,
            ts="2026-11-21T10:30:00+00:00", payload={"workflow_run_id": arm["run_id"], **body}))

    add("current-freeze", "candidate.ready", schema_version="candidate-freeze-receipt.v1", freeze_id="current",
        task_map_generation="G-2", candidate_head_commit=head)
    freeze = log.read_all()[-1]
    from tests.test_goal_closure_result import _result

    envelope = {"ref": "control/judge", "sha256": "c"*64}
    result = {**_result(), "workflow_run_id": arm["run_id"], "target_commit": head, "task_map_generation": "G-2"}
    add("admission", "workflow.call.result.admitted", admission_status="admitted", envelope_ref=envelope,
        control_result_schema="goal-closure-result.v1", semantic_verdict="passed")
    add("actual", "goal.closure.synthesized", admitted_call_result_ref=envelope,
        goal_closure_result=result, candidate_event_id=freeze.id)
    add("unadmitted", "goal.closure.synthesized", admitted_call_result_ref={**envelope, "sha256": "d"*64},
        goal_closure_result=result, candidate_event_id=freeze.id)
    add("unadmitted-compat", "judge.passed", "unadmitted", source_event_id="unadmitted")
    add("other-run-result", "goal.closure.synthesized", admitted_call_result_ref=envelope,
        goal_closure_result={**result, "workflow_run_id": "previous-run"}, candidate_event_id=freeze.id)
    add("compat", "judge.passed", "actual", source_event_id="actual", target_commit=head)
    add("synth-failed", "goal.closure.synthesis.failed", status="failed")
    add("unsigned", "goal.closure.synthesized", candidate_event_id=freeze.id)
    add("wrong", "judge.failed", candidate_snapshot_event_id="previous-freeze", freeze_id=freeze.payload["freeze_id"])
    add("stale", "judge.failed", candidate_event_id=freeze.id, target_commit="other-head")
    add("ambiguous-request", "run.delivery.requested", target_commit=head)
    add("ship-1", "ship.completed", "ambiguous-request", source_commit=head, final_commit="other")
    add("ship-2", "ship.completed", "ambiguous-request", source_commit=head, final_commit="other")
    add("ambiguous-settlement", "run.delivery.settled", "ambiguous-request", target_commit=head)
    add("ship-request", "run.delivery.requested", target_commit=head)
    add("ship-side-effect", "ship.completed", "ship-request", source_commit=head, final_commit="landed")
    add("settled", "run.delivery.settled", "ship-request", target_commit=head)
    audit["window_end"] = "settled"
    arm["audit"] = capture(audit_path, audit)
    capture(path, pair)
    output = module().compare(path)["arms"][1]
    judges = {r["id"]: r for r in output["judge_receipts"]}
    assert judges["actual"]["compatibility_event_ids"] == ["compat"] and judges["actual"]["status"] == "passed"
    assert judges["actual"]["binding"] == "current_freeze" and judges["actual"]["admission_evidence"] == "admission"
    assert output["unconfirmed_judge_event_ids"] == ["unadmitted", "unadmitted-compat", "other-run-result", "unsigned"]
    assert not {"unsigned", "compat", "synth-failed"}.intersection(judges)
    assert judges["wrong"]["binding"] == judges["stale"]["binding"] == "older_or_different_target"
    assert [r["id"] for r in output["judge_attempt_failures"]] == ["synth-failed"]
    delivered = {r["id"]: r for r in output["delivery_receipts"]}
    assert delivered["settled"]["ship_event_ids"] == ["ship-side-effect"] and "ship-side-effect" not in delivered
    assert delivered["settled"]["binding"] == "observed_unbound"
    assert {"ambiguous-settlement", "ship-1", "ship-2"} <= delivered.keys()


def test_real_completion_gate_proves_actual_judge_and_delivery_even_without_direct_freeze_id():
    from tests.test_goal_completion_receipt import completion_events, RUN_ID
    from dataclasses import replace

    events = [replace(e, origin="kernel") for e in completion_events()]
    freeze = next(e for e in events if e.type == "candidate.ready")
    end = next(e for e in events if e.type == "run.goal.completed")
    delivery = ZfEvent(type="run.delivery.settled", id="delivery-real", origin="kernel", correlation_id=RUN_ID,
        payload={"workflow_run_id": RUN_ID, "claim_id": end.payload["claim_id"], "target_commit": end.payload["target_commit"]})
    end.payload.update(delivery_policy="ship", delivery_status="settled", delivery_event_id=delivery.id)
    events.insert(events.index(end), delivery)
    assert module().terminal_bound_receipts(events, RUN_ID, end, freeze) == {"evt-closure", delivery.id}
    assert not module().terminal_bound_receipts(events, RUN_ID, end, replace(freeze, id="later-same-head"))
    delivery.payload["claim_id"] = "unrelated"
    assert not module().terminal_bound_receipts(events, RUN_ID, end, freeze)


def test_web_mutations_without_top_level_run_binding_remain_reviewable(pilot):
    path, pair = pilot
    arm = pair["arms"][0]
    state = path.parent / arm["state_dir"]
    log = EventLog(state / "events.jsonl")
    for eid, target in (("our-web", arm["run_id"]), ("other-web", "another-run"), ("unbound-web", "")):
        log.append(ZfEvent(type="web.action.requested", id=eid, actor="web",
                           payload={"action": "runtime-restart", "request": {"run_id": target}}))
    log.append(ZfEvent(type="human.escalate", id="end", correlation_id=arm["run_id"], actor="supervisor"))
    audit = json.loads((path.parent / arm["audit"]["path"]).read_text())
    audit["window_end"] = "end"
    arm["audit"] = capture(path.parent / arm["audit"]["path"], audit)
    capture(path, pair)
    result = module().compare(path)["arms"][0]
    assert result["owner_events_for_review"] == ["our-web", "unbound-web"]
    assert result["post_approval_interventions"] is None


def test_task_completion_requires_current_digest_and_same_run_integration(tmp_path):
    common = {"workflow_run_id": "R", "task_map_generation": "G", "candidate_generation": "C"}
    ref = write_immutable_json_sidecar(tmp_path, {"task_id": "T", **common, "new_candidate_head": "H"}, root="receipts",
                                       kind="task_integration_receipt", schema_version="task-integration-receipt.v1",
                                       created_by="kernel")
    cause = ZfEvent(type="integration.queue.integrated", id="I", origin="kernel",
                    payload={**common, "task_id": "T", "candidate_head": "H", "receipt_ref": ref})
    done = ZfEvent(type="task.done", id="D", origin="kernel", task_id="T", causation_id=cause.id,
                   payload={**common, "candidate_head": "H", "authority": "task_integration_receipt",
                            "integration_event_id": cause.id, "integration_receipt_ref": ref,
                            "integration_receipt_digest": ref["sha256"]})
    alias = ZfEvent(type="task.done.accepted", id="alias", task_id="T", origin="kernel")
    by_id = {e.id: e for e in (cause, done, alias)}
    rows = [{"id": e.id, "task_id": "T"} for e in (done, alias)]
    assert module().task_receipts(rows, by_id, tmp_path, "R") == ([{**rows[0], "corroborating_event_ids": ["alias"]}], [])
    (tmp_path / ref["ref"]).unlink()
    assert module().task_receipts(rows[:1], by_id, tmp_path, "R") == ([], ["D"])


def test_frozen_machine_local_identities_preserve_comparable_policy():
    fn = module().common_policy_digest
    a = {"config":{"runtime":{"run_manager":{"resident_agent":{}}, "host_verification":{
         "approval_ref":"owner:A", "commands":["npm test"]}}, "session":{"tmux_session":"A"}}}
    b = json.loads(json.dumps(a))
    b["config"]["session"]["tmux_session"] = "B"
    b["config"]["runtime"]["host_verification"]["approval_ref"] = "owner:B"
    def bindings(group):
        return {"session.tmux_session":group,"runtime.host_verification.approval_ref":"owner:"+group}
    assert fn(a, bindings("A")) == fn(b, bindings("B"))
    with pytest.raises(ValueError, match="differs from preapproved"):
        fn(b, bindings("A"))
    with pytest.raises(ValueError, match="unsupported"):
        fn(b, {"runtime.host_verification.commands":"whatever"})
    b["config"]["runtime"]["host_verification"]["commands"].append("npm run deploy")
    assert fn(a, bindings("A")) != fn(b, bindings("B"))
