"""Owner-declared recovery bounds are frozen before dispatch and rechecked at effects."""
from dataclasses import replace
import subprocess

import pytest
import yaml

from tests.test_candidate_host_consumption import evidence
from tests.test_host_verification import prepare
from tests.test_reader_settled_failure_rebuild import failed_reader
from tests.test_run_admission import _config, _invoke, _runtime
from tests.test_run_manager_reader_rebuild import consume
from tests.test_task_host_actions import case
from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import RuntimeRecoveryAuthorization
from zf.core.events import ZfEvent
from zf.runtime.host_verification import reconcile_host_verification
from zf.runtime.recovery_authorization import current_run_grant, run_grant_fields
from zf.runtime.run_admission import admit_workflow_invoke
from zf.runtime.reader_rebuild_admission import reader_rebuild_error


def enroll(runtime, run_id):
    runtime.config.runtime.run_manager.recovery_authorization = RuntimeRecoveryAuthorization(
        approval_ref="owner:recovery-policy", max_reader_rebuilds_per_run=1)
    decision = admit_workflow_invoke(runtime, ZfEvent(type="workflow.invoke.requested",
        payload={"workflow_run_id": run_id, "recovery_authorization": {"approval_ref":"untrusted"}}))
    assert decision.status == "admitted"


def test_admission_ignores_worker_payload_and_cannot_expand_existing_run(tmp_path):
    _, log, runtime = _runtime(tmp_path)
    _invoke(runtime, run_id="RUN-A", task_id="TASK-A", payload_updates={
        "recovery_authorization":{"approval_ref":"worker:forged"}})
    admitted = next(e for e in log.read_all() if e.type == "run.admission.admitted")
    assert "recovery_authorization" not in admitted.payload
    runtime.config.runtime.run_manager.recovery_authorization = RuntimeRecoveryAuthorization("owner:later", 1)
    assert current_run_grant(runtime.config, log.read_all(), "RUN-A")[1]
    forged = replace(admitted, actor="worker", payload={**admitted.payload, **run_grant_fields(runtime.config)})
    assert current_run_grant(runtime.config, [forged], "RUN-A")[1]


@pytest.mark.parametrize("field,value", [
    ("approval_ref",""), ("max_reader_rebuilds_per_run",True), ("max_checkpoint_restarts_per_run",-1),
    ("safe_resume_actions",["*"]), ("safe_resume_actions",[]), ("additional_permissions",True),
])
def test_config_requires_explicit_bounded_authority(tmp_path, field, value):
    config = {"approval_ref":"owner:gate", "max_checkpoint_restarts_per_run":1,
              "safe_resume_actions":["needs_stage_dispatch"]}
    data = {"version":"1.0", "project":{"name":"example"},
            "runtime":{"run_manager":{"recovery_authorization":config}}}
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump(data))
    assert load_config(path).runtime.run_manager.recovery_authorization.max_checkpoint_restarts_per_run == 1
    config[field] = value
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="recovery_authorization"):
        load_config(path)


@pytest.mark.parametrize("change", ["", "budget", "owner_gate", "revoked"])
def test_native_effect_rechecks_run_and_host_allowances(case, monkeypatch, change):
    service, _ = prepare(case)
    policy = service.config.runtime.host_verification
    policy.recollect_commands = list(policy.commands)
    config = _config()
    config.runtime.host_verification = policy
    config.runtime.run_manager.recovery_authorization = RuntimeRecoveryAuthorization("owner:bounded")
    service.config = config
    service.writer.default_origin = "kernel"
    # This operation fixture already contains the Run. Attach its trusted frozen policy as test input.
    service.writer.emit("run.admission.admitted", actor="orchestrator",
        payload={"workflow_run_id":"run-1", **run_grant_fields(config)})
    if change == "budget":
        policy.max_executions_per_run += 1
    elif change == "revoked":
        config.runtime.run_manager.recovery_authorization = None
    elif change == "owner_gate":
        service.writer.emit("human.escalate", payload={"workflow_run_id": "run-1", "blocking_scope": "run",
                            "checkpoint_id": "owner-review", "reason": "owner approval pending"})
    calls = []
    def execute(argv, **_):
        calls.append(argv)
        if len(calls) == 1:
            raise ValueError("collector output unavailable")
        return subprocess.CompletedProcess(argv, 0, "second observation", "")
    monkeypatch.setattr("zf.runtime.host_probe_recollection._container_absent", lambda _: True)
    monkeypatch.setattr("zf.runtime.host_verification._run_container", execute)
    for _ in range(4):
        reconcile_host_verification(service)
    events = service.writer.event_log.read_all()
    if change:
        assert not calls
        assert any(e.type == "human.escalate" and (change == "owner_gate" or
                   e.payload.get("reason") == "run_recovery_authorization_changed") for e in events)
    else:
        assert len(calls) == 2
        assert sum(bool(e.payload.get("recollection_of_start_id")) for e in events
                   if e.type == "runtime.action.attempt.started") == 1


@pytest.mark.parametrize("fault", ["", "budget", "late_gate", "receipt", "refreeze", "exhausted", "role"])
def test_exact_reader_with_signed_receipts_rechecked_at_application(evidence, tmp_path, monkeypatch, fault):
    from zf.runtime import candidate_host_navigation as navigation
    from zf.runtime.diagnosis_reader_rebuild import diagnosis_reader_rebuild

    original = navigation.host_navigation_entries
    with monkeypatch.context() as previous:
        previous.setattr(navigation, "host_navigation_entries", lambda *a, **kw: [
            {**row, "media_sources":[]} for row in original(*a, **kw)])
        state, log, transport, runtime, scope, failure, _ = failed_reader(tmp_path, frozen_inputs=evidence[:2],
            pending_sibling=True, before_start=enroll, failure_class="verifier_execution_failure")
    config = runtime.config
    assert reader_rebuild_error(state, log.read_all(), scope, config=config) == ""
    diagnosis = diagnosis_reader_rebuild(state, log.read_all(), {"request_kind":"recovery_diagnosis",
        "workflow_run_id":scope["workflow_run_id"], "source_event_ids":[failure.id]})
    runtime.event_writer.emit("run.manager.agent.recommendation", actor="run-manager", payload=diagnosis["recommendation"])
    consume(runtime)
    requests = [e for e in log.read_all() if e.type == "repair.action.requested"]
    assert len(requests) == 1
    if fault == "budget":
        config.global_budget_usd = 999
    if fault == "late_gate":
        runtime.event_writer.emit("human.escalate", payload={
            "workflow_run_id":scope["workflow_run_id"], "blocking_scope":"run", "checkpoint_id":"owner-review"})
    if fault == "refreeze":
        candidate = next(e for e in log.read_all() if e.type == "candidate.ready")
        runtime.event_writer.emit("candidate.ready", payload={**candidate.payload, "freeze_id":"next-freeze"})
    if fault == "exhausted":
        runtime.event_writer.emit("runtime.action.attempt.started", actor="run-manager", payload={
            "workflow_run_id":scope["workflow_run_id"], "fanout_id":"other-fanout", "fanout_child_id":"verify-2",
            "action":"child-rebuild"})
    if fault == "role":
        config.roles[0].backend = "different-backend"
    if fault == "receipt":
        (state / evidence[4]["ref"]).write_text("{}")
    sent = len(transport.sent)
    runtime.run_once(events=requests)
    runtime.run_once(events=requests)
    assert len(transport.sent) == sent + (0 if fault else 1)
    if fault:
        assert any(e.type == "repair.action.rejected" for e in log.read_all())


def test_concurrent_reader_reservations_cannot_spend_last_permit_twice(tmp_path, evidence):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from zf.runtime.recovery_authorization import reserve_reader_rebuild

    state, log, _, runtime, scope, _, _ = failed_reader(tmp_path, frozen_inputs=evidence[:2],
        pending_sibling=True, before_start=enroll)
    requests = [runtime.event_writer.emit("repair.action.requested", actor="run-manager", payload={
        **scope, "action_id":f"child-rebuild-{index}", "recovery_action":"child-rebuild", "kind":"rerun_fanout_child"})
        for index in range(2)]
    start = Barrier(2)
    def reserve(request):
        start.wait(timeout=5)
        return reserve_reader_rebuild(runtime.config, state, runtime.event_writer, request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, requests))
    assert sum(bool(event) for event, _ in outcomes) == 1
    assert [err for _, err in outcomes if err] == ["reader_rebuild_run_allowance_exhausted"]
    first = next(event for event, _ in outcomes if event)
    original = next(request for request in requests if request.id == first.causation_id)
    # Model a crash before the provider returns. The charged attempt remains unknown.
    _, error = reserve_reader_rebuild(runtime.config, state, runtime.event_writer, original)
    assert error == "reader_recovery_reserved_inspect_original_effect"
    assert sum(e.type == "runtime.action.attempt.started" and e.payload.get("action")=="child-rebuild"
               for e in log.read_all()) == 1
