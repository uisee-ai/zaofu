"""Stage roster shows pre-dispatch failures even when there is no operation."""
from dataclasses import replace

from zf.core.events.model import ZfEvent
from zf.runtime.stage_team_projection import project_stage_teams, team_summary


def test_stage_roster_binds_preregistration_receipts_to_the_current_child():
    run, fanout = "run", "scan"
    charter = {"obligation_id": "scan-source", "role": "tech", "recipient": "planner"}
    scope = {"request_id": "request", "revision": 1, "proposal_digest": "digest", "run_id": run}
    declared = {k: scope[k] for k in ("request_id", "revision", "proposal_digest")}
    timeline = [ZfEvent(type=t, origin="kernel", payload=declared)
                for t in ("workflow.request.proposed", "workflow.request.approved")]
    start = ZfEvent(type="fanout.started", origin="kernel", correlation_id=run, payload={
        "stage_id": "prd-scan", "fanout_id": fanout, "expected_children": [
            {"role_instance": "tech", "child_id": "reader-1", "payload": {"stage_obligation": charter}}]})
    timeline.append(start)
    identity = {"role_instance": "tech", "stage_id": "prd-scan", "child_id": "reader-1", "fanout_id": fanout}

    def member():
        nodes = {"workflow:stage:prd-scan": {"id": "workflow:stage:prd-scan", "data": {
            "stage_id": "prd-scan", "definition": {"metadata": {"team_obligations": [charter]}}}}}
        project_stage_teams(nodes, {"scope": scope, "compiled": {"stage_teams": {"plan": {"author": "planner"}}},
                                    "events": list(enumerate(timeline, 1))}, {})
        team = nodes["workflow:stage:prd-scan"]["data"]["team"]
        assert team["admitted_count"] == 0
        return team_summary(team)["members"][0]

    failed = ZfEvent(type="fanout.child.failed", origin="kernel", correlation_id=run, causation_id=start.id,
                     payload={**identity, "failure_scope": "operation_preregistration", "reason": "app/ is a directory"})
    timeline.extend([replace(failed, id="spoof", origin="worker"), replace(failed, id="foreign", correlation_id="other"),
                     replace(failed, id="wrong-child", payload={**failed.payload, "child_id": "different"}),
                     replace(failed, id="unrelated", causation_id="foreign")])
    assert member()["status"] == "ready"
    timeline.append(failed)
    current = member()
    assert (current["source_event_id"], current["status"], current["attempt_count"]) == (failed.id, "failed", 0)
    assert current["dispatch_failure_reason"] == failed.payload["reason"]
    assert not current["verdict"] and not current["instance"] and not current["owner"]

    timeline.pop()
    rejected = replace(failed, id="rejected", type="fanout.plan.source.rejected", payload={
        **identity, "obligation_id": "scan-source", "workflow_proposal_digest": "digest",
        "input_ref": "app/", "reason": "regular file required", "recovery_owner": "oa"})
    timeline.append(replace(rejected, id="old-proposal", payload={**rejected.payload, "workflow_proposal_digest": "old"}))
    assert member()["status"] == "ready"
    timeline.append(rejected)
    current = member()
    assert (current["source_event_id"], current["status"], current["owner"]) == (rejected.id, "blocked", "oa")
    assert current["wait"]["wake_event"] == "fanout.plan.source.rebound"
    terminal = replace(failed, id="handed-to-rm", causation_id="oa-failure", payload={
        **failed.payload, "source_event_id": rejected.id, "recovery_owner": "run_manager"})
    timeline.append(terminal)
    assert member()["owner"] == "run_manager" and member()["status"] == "failed"
    timeline.pop()
    rebound = replace(rejected, id="rebound", type="fanout.plan.source.rebound", causation_id="decision",
                      payload={**rejected.payload, "source_event_id": rejected.id, "replacement_ref": "app/entry.py"})
    timeline.append(replace(rebound, id="wrong-source", payload={**rebound.payload, "source_event_id": "unrelated"}))
    assert member()["source_event_id"] == rejected.id
    timeline.append(rebound)
    current = member()
    assert (current["source_event_id"], current["status"], current["owner"]) == (rebound.id, "ready", "kernel")
    assert current["attempt_count"] == 0
    # The selected file can change between the OA decision and target binding.
    drift = replace(failed, id="changed-after-rebind", causation_id=rebound.id,
                    payload={**failed.payload, "reason": "selected source changed"})
    timeline.append(drift)
    assert member()["source_event_id"] == drift.id and member()["status"] == "failed"
    timeline.pop()
    timeline[-1:] = [terminal, rebound]
    assert member()["source_event_id"] == terminal.id
    timeline.append(replace(start, id="new-start", payload={**start.payload, "fanout_id": "new-scan"}))
    assert member()["source_event_id"] == timeline[1].id
