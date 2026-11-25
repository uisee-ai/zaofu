"""A mixed diagnosis repairs a subset and carries all remaining acceptance duties."""
from copy import deepcopy

import pytest

from tests.test_candidate_task_repair_decision import incident, submit
from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding
from zf.runtime.candidate_finding_partition import retained_candidate_obligations
from zf.runtime.verification_result import validate_verification_acceptance, VerificationResultError


def partition(runtime, request):
    bound = aggregate_candidate_binding(runtime.state_dir, runtime.event_log.read_all(),
        failure_id=request.payload["failure_event_ids"][0], run_id=request.correlation_id, task_id="TASK-1")
    return [{"finding_id": row["finding_id"],
        "classification": "product_defect" if row["product_repair_eligible"] else "execution_unknown",
        "owner": "impl" if row["product_repair_eligible"] else "run_manager",
        "next_action": "repair" if row["product_repair_eligible"] else "diagnose bound missing execution",
        "rationale": "Read the bound evidence; preserve unknown until independent verification."}
        for row in bound["findings"]]


def test_mixed_repair_publishes_once_without_discarding_unknown(tmp_path, monkeypatch):
    runtime, request, repair = incident(tmp_path, monkeypatch, peer=True, canonical=True)
    repair["finding_dispositions"] = partition(runtime, request)
    event, result = submit(runtime, request, repair)
    assert result["applied"], result
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
    assert apply_orchestrator_agent_decision(runtime, event) == result
    events = runtime.event_log.read_all()
    ready = [e for e in events if e.type == "task_map.ready"]
    assert len(ready) == 1 and ready[0].payload["task_ids"] == ["TASK-2-FIX"]
    obligations = retained_candidate_obligations(events, run_id=request.correlation_id, task_id="TASK-1")
    assert len(obligations) == len(repair["finding_dispositions"])
    assert any(row["disposition"]["classification"] == "execution_unknown" for row in obligations)
    assert all(row["status"] == "pending" for row in obligations)
    assert not any(e.type in {"test.passed", "run.goal.completed", "prd.plan.requested"} for e in events)
    from tests.test_run_manager import _config
    from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick
    config = _config()
    diagnoses = [a for a in build_run_manager_projection(runtime.state_dir, events=events,
        config=config)['pending_actions'] if a.get('failure_class') == 'candidate_recovery_obligations']
    assert len(diagnoses) == 1 and diagnoses[0]['candidate_obligations']
    run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer, config=config,
                     spawn_repairs=False, action_filter={'diagnose-attention'})
    assert any(e.type == 'run.manager.autoresearch.requested' for e in runtime.event_log.read_all())
    assert not [a for a in build_run_manager_projection(runtime.state_dir,
        events=runtime.event_log.read_all(), config=config)['pending_actions']
        if a.get('failure_class') == 'candidate_recovery_obligations']
    with pytest.raises(VerificationResultError, match="retained recovery obligation"):
        validate_verification_acceptance({"execution_status": "completed", "verdict": "passed"},
            {"candidate_recovery_obligations": obligations})


@pytest.mark.parametrize("fault", ["missing", "unknown", "duplicate", "unknown_as_product", "owner", "next_action"])
def test_partition_rejects_dropped_or_unbound_obligations(tmp_path, monkeypatch, fault):
    runtime, request, repair = incident(tmp_path, monkeypatch, peer=True)
    rows = partition(runtime, request)
    if fault == "missing": rows.pop()
    elif fault == "unknown": rows[0]["finding_id"] = "unbound-finding"
    elif fault == "duplicate": rows.append(deepcopy(rows[0]))
    elif fault == "unknown_as_product":
        next(row for row in rows if row["classification"] == "execution_unknown")["classification"] = "product_defect"
    else: rows[0].pop(fault)
    repair["finding_dispositions"] = rows
    _, result = submit(runtime, request, repair)
    assert not result["applied"]
    assert not any(e.type == "task_map.amended" for e in runtime.event_log.read_all())


@pytest.mark.parametrize('explicit', [False, True])
def test_one_rejected_reader_still_requires_partition_of_its_blocked_item(tmp_path, monkeypatch, explicit):
    from tests import test_candidate_plan_recovery as fixture
    original = fixture.write_immutable_json_sidecar
    def mixed(state, body, **kwargs):
        if body.get('schema_version') == 'verification-result.v1':
            body = {**body, 'rework_items': [{'acceptance_id': 'TASK-2/AC-5',
                'status': 'blocked', 'allowed_scope': ['src/product.py'], 'summary': 'execution evidence missing'}]}
        return original(state, body, **kwargs)
    monkeypatch.setattr(fixture, 'write_immutable_json_sidecar', mixed)
    runtime, request, repair = incident(tmp_path, monkeypatch)
    if explicit: repair['finding_dispositions'] = partition(runtime, request)
    _, result = submit(runtime, request, repair)
    assert bool(result['applied']) is explicit, result
    if explicit:
        retained = retained_candidate_obligations(runtime.event_log.read_all(),
            run_id=request.correlation_id, task_id='TASK-1')
        assert len(retained) == 2
        assert any(r['disposition']['classification'] == 'execution_unknown' for r in retained)


def test_pre_upgrade_dispositions_still_apply_after_reader_observations(tmp_path, monkeypatch):
    import hashlib
    import json

    runtime, request, repair = incident(tmp_path, monkeypatch, peer=True, canonical=True)
    bound = aggregate_candidate_binding(runtime.state_dir, runtime.event_log.read_all(),
        failure_id=request.payload["failure_event_ids"][0], run_id=request.correlation_id, task_id="TASK-1")
    # Earlier OA checkpoints bound report findings to the admitted result and pointer,
    # before aggregate members also carried the fanout child event for shadow triage.
    dispositions = partition(runtime, request)
    for finding, disposition in zip(bound["findings"], dispositions, strict=True):
        if finding.get("control_result_ref"):
            old_source = {"operation_id": finding["operation_id"], "control_result_ref": finding["control_result_ref"],
                          "child_event_id": None, "pointer": finding["pointer"]}
            disposition["finding_id"] = "finding-" + hashlib.sha256(json.dumps(old_source, sort_keys=True).encode()).hexdigest()
    assert any(m["child_event_id"] and m["control_result_ref"] for m in bound["members"])
    repair["finding_dispositions"] = dispositions
    _, result = submit(runtime, request, repair)
    assert result["applied"], result
