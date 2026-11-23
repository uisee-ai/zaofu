"""Collection failure preserves execution facts and gets a bounded diagnosis."""
from pathlib import Path
import subprocess

import pytest

from tests.test_host_verification import prepare
from tests.test_task_host_actions import case
from zf.runtime.host_verification import reconcile_host_verification
from zf.runtime.host_probe_diagnosis import pending_host_probe_diagnoses
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.mark.parametrize("kind", ["per_file_limit", "total_limit", "symlink", "non_regular"])
def test_collection_failure_retains_receipt_and_requests_diagnosis(case, monkeypatch, kind):
    case[0].writer.default_origin = "kernel"
    service, _ = prepare(case)
    policy = service.config.runtime.host_verification
    policy.max_artifact_bytes = 10
    policy.max_total_evidence_bytes = 15

    def run(argv, **kwargs):
        mount = next(v for v in argv if v.startswith("type=bind,src=") and "dst=/evidence" in v)
        output = Path(mount.split("src=")[1].split(",")[0])
        target = output / "trace.zip"
        if kind == "per_file_limit":
            target.write_bytes(b"x" * 11)
        elif kind == "total_limit":
            target.write_bytes(b"x" * 10)
            (output / "video.webm").write_bytes(b"x" * 10)
        elif kind == "symlink":
            target.symlink_to(output / "missing")
        else:
            import os
            os.mkfifo(target)
        return subprocess.CompletedProcess(argv, 0, "probe completed\n", "")

    monkeypatch.setattr("zf.runtime.host_verification._run_container", run)
    assert reconcile_host_verification(service) == 1
    events = service.writer.event_log.read_all()
    completed = [e for e in events if e.type == "runtime.action.attempt.completed"
                 and e.payload.get("kind") == "verify"]
    assert len(completed) == 1
    receipt = hydrate_sidecar_ref(service.state_dir, completed[0].payload["receipt_ref"]).payload
    assert receipt["exit_code"] == 0
    assert receipt["status"] == "failed"
    assert receipt["evidence_status"] == "collection_failed"
    assert receipt["collection_error"]["kind"] == kind
    assert receipt["collection_error"]["stage"] == "collect_container_evidence"
    assert receipt["collection_error"]["path"]
    assert not receipt.get("evidence_ref")
    output = hydrate_sidecar_ref(service.state_dir, receipt["output_ref"]).payload
    assert "probe completed" in str(output)
    actions = pending_host_probe_diagnoses(service.state_dir, events, service.config)
    assert len(actions) == 1 and actions[0]["source_event_id"] == completed[0].id
    assert actions[0]["failure_class"] == "unknown"
    assert reconcile_host_verification(service) == 0
    assert not any(e.type in {"test.passed", "task.done"} for e in events)


def _legacy_failure(case, monkeypatch):
    from tests.test_run_manager import _config

    case[0].writer.default_origin = "kernel"
    service, _ = prepare(case)
    def fail(*args):
        raise ValueError("collection could not retain evidence")
    monkeypatch.setattr("zf.runtime.host_verification.verify_in_container", fail)
    assert reconcile_host_verification(service) == 1
    assert reconcile_host_verification(service) == 0
    config = _config()
    config.runtime.host_verification = service.config.runtime.host_verification
    return service, config, service.writer.event_log.read_all()


def test_legacy_unknown_host_effect_gets_diagnosis_through_its_two_gates(case, monkeypatch):
    from zf.runtime.run_manager import build_run_manager_projection
    from zf.runtime.run_continuation import blocking_decisions_for_action

    service, config, events = _legacy_failure(case, monkeypatch)
    failure = next(e for e in events if e.type == "runtime.action.attempt.failed")
    projection = build_run_manager_projection(service.state_dir, events=events,
        config=config, project_root=service.project_root)
    action = next(a for a in projection["pending_actions"] if a.get("source_event_id") == failure.id)
    gates = projection["completion_profile"]["pending_human_decisions"]
    assert len(gates) == 2
    assert not blocking_decisions_for_action(gates, action, events=events)
    assert projection["continuation"]["next_operation"]["action"] == "diagnose-attention"
    assert not action.get("continuation_blockers")
    assert failure.payload["effect_status"] == "inspect_before_retry"
    assert "receipt_ref" not in failure.payload
    for mutation in ({"action": "candidate-rework-apply"}, {"workflow_run_id": "other"},
                     {"host_manifest_digest": "other"}, {"source_event_ids": ["missing"]}):
        assert blocking_decisions_for_action(gates, {**action, **mutation}, events=events) == gates
    assert service.writer.event_log.read_all() == events


@pytest.mark.parametrize("invalid", ["worker", "missing_start", "wrong_run", "wrong_manifest"])
def test_unknown_effect_diagnosis_requires_exact_kernel_start(case, monkeypatch, invalid):
    from dataclasses import replace
    service, config, events = _legacy_failure(case, monkeypatch)
    failure = next(e for e in events if e.type == "runtime.action.attempt.failed")
    if invalid == "missing_start":
        events = [e for e in events if e.id != failure.causation_id]
    else:
        changed = (replace(failure, origin="worker") if invalid == "worker" else
                   replace(failure, payload={**failure.payload,
                       "workflow_run_id" if invalid == "wrong_run" else "manifest_digest": "other"}))
        events = [changed if e.id == failure.id else e for e in events]
    assert pending_host_probe_diagnoses(service.state_dir, events, config) == []


def test_projection_does_not_claim_ready_when_continuation_gate_blocks(tmp_path):
    from zf.runtime.run_continuation import bind_continuation_blockers, build_run_continuation_projection
    from zf.runtime.run_manager import build_run_status_explain_projection
    action = {"action": "candidate-rework-apply", "safe_resume_action": "candidate_replan",
              "preflight": {"status": "passed"}, "policy_decision": {"decision": "auto_decide", "executable": True}}
    gate = {"event_id": "gate", "blocking_scope": "verification"}
    completion = {"status": "incomplete", "pending_human_decisions": [gate]}
    actions = bind_continuation_blockers([action], [gate], [])
    continuation = build_run_continuation_projection([], goal={}, pending_actions=actions,
                                                     completion_profile=completion)
    assert continuation["next_operation"] is None
    assert actions[0]["policy_decision"]["executable"] is False
    explanation = build_run_status_explain_projection(tmp_path, events=[], goal={},
        completion_profile=completion, monitor={}, pending_actions=actions,
        no_progress={}, repair_merge_queue={}, wait_hints={}, continuation=continuation)
    assert explanation["blocking"] is True
    assert explanation["blocking_refs"] == [gate]
    assert explanation["next_auto_action"] == "wait_for_human_decision"
    assert explanation["pending_actions"][0]["readiness"] == "human_blocked"


def test_quiescent_standard_tick_reaches_rm_for_unknown_host_effect(case, monkeypatch):
    import time
    from unittest.mock import MagicMock
    from tests.test_tick_services import _FakeOrchestrator
    from zf.runtime.quiescent import quiescent_now
    from zf.runtime.tick_services import run_standard_tick_services, TickServiceState

    service, config, events = _legacy_failure(case, monkeypatch)
    config.goal.enabled = True
    config.goal.quiescent_after_escalate = True
    future = time.time() + 3600
    assert quiescent_now(events, config=config, now_epoch=future).quiescent
    monkeypatch.setattr(time, "time", lambda: future)
    manager = MagicMock(return_value=MagicMock(changed=True, repairs_dispatched=0))
    monkeypatch.setattr("zf.runtime.tick_services._run_run_manager", manager)
    orch = _FakeOrchestrator(service.state_dir, config)
    outcome = run_standard_tick_services(orch, state=TickServiceState(), now=future)
    assert manager.call_count == 1
    assert outcome.run_manager
    # Still no repeated container execution or implicit gate acknowledgement.
    after = orch.event_log.read_all()
    assert sum(e.type == "runtime.action.attempt.started" and e.payload.get("kind") == "verify"
               for e in after) == 1
    assert not any(e.type == "human.escalation.acknowledged" for e in after)


def test_multiple_unknown_manifests_have_distinct_diagnoses_without_cross_blocking(case, monkeypatch):
    from dataclasses import replace
    from zf.runtime.run_continuation import blocking_decisions_for_action
    from zf.runtime.run_manager import build_run_manager_projection

    service, config, events = _legacy_failure(case, monkeypatch)
    failed = next(e for e in events if e.type == 'runtime.action.attempt.failed')
    start = next(e for e in events if e.id == failed.causation_id)
    other_start = replace(start, id=start.id + '-other', payload={**start.payload, 'manifest_digest': 'other-command'})
    service.writer.append(other_start)
    other_failed = replace(failed, id=failed.id + '-other', causation_id=other_start.id,
                           payload={**failed.payload, 'manifest_digest': 'other-command'})
    service.writer.append(other_failed)
    gate = next(e for e in events if e.type == 'human.escalate')
    service.writer.append(replace(gate, id=gate.id + '-other',
                                 payload={**gate.payload, 'host_manifest_digest': 'other-command'}))
    events = service.writer.event_log.read_all()
    projection = build_run_manager_projection(service.state_dir, events=events, config=config,
                                              project_root=service.project_root)
    action = next(a for a in projection['pending_actions'] if a.get('source_event_id') == failed.id)
    second = next(a for a in projection['pending_actions'] if a.get('source_event_id') == other_failed.id)
    assert action['checkpoint_id'] != second['checkpoint_id']
    gates = projection['completion_profile']['pending_human_decisions']
    assert len(gates) == 3
    assert not blocking_decisions_for_action(gates, action, events=events)
    assert projection['continuation']['next_operation']['action'] == 'diagnose-attention'
    # The other gate cannot be exempted without its own authenticated failure.
    assert blocking_decisions_for_action(gates, action, events=[e for e in events if e.id != other_failed.id])


def test_unknown_host_diagnosis_deadline_reprompts_through_full_tick(case, monkeypatch):
    import time
    from datetime import datetime, timedelta, timezone
    from tests.test_run_manager_resident import _resident_config
    from tests.test_tick_services import _FakeOrchestrator
    from zf.runtime.run_manager import run_manager_tick
    from zf.runtime.tick_services import run_standard_tick_services, TickServiceState
    from zf.runtime.quiescent_reader_recovery import _host_diagnosis_ready

    service, config, _ = _legacy_failure(case, monkeypatch)
    config.runtime.run_manager = _resident_config(session_mode='dedicated').runtime.run_manager
    config.runtime.run_manager.resident_agent.instance_id = 'recovery-manager'
    config.goal.enabled = True
    config.goal.quiescent_after_escalate = True
    briefing = service.state_dir / 'briefings/resident.md'
    briefing.parent.mkdir(exist_ok=True); briefing.write_text('diagnose only')
    service.writer.emit('run.manager.resident.spawned', payload={'ready': True,
        'instance_id': 'recovery-manager', 'session_mode': 'dedicated', 'tmux_session': 'zf-rm'})
    service.writer.emit('run.manager.resident.prompted', payload={'prompted': True,
        'instance_id': 'recovery-manager', 'briefing_path': str(briefing)})
    service.writer.emit('run.manager.agent.observation', actor='recovery-manager', payload={'status': 'watching'})
    def fake_run(args, **kwargs):
        output = f'%9\tclaude\t{service.project_root}\t0\n' if args[:3] == ['tmux', 'display-message', '-p'] else ''
        return subprocess.CompletedProcess(args, 0, output, '')
    monkeypatch.setattr('zf.runtime.run_manager.subprocess.run', fake_run)
    monkeypatch.setattr('zf.runtime.run_manager.time.sleep', lambda _: None)
    run_manager_tick(state_dir=service.state_dir, writer=service.writer, config=config,
        project_root=service.project_root, event_log=service.writer.event_log, spawn_repairs=False)
    first = next(e for e in service.writer.event_log.read_all()
        if e.type == 'run.manager.resident.prompted' and e.payload.get('request_id'))
    later = datetime.now(timezone.utc) + timedelta(seconds=3600)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return later
    monkeypatch.setattr('zf.runtime.run_manager.datetime', Clock)
    monkeypatch.setattr(time, 'time', lambda: later.timestamp())
    orch = _FakeOrchestrator(service.state_dir, config)
    orch.event_writer.default_origin = 'kernel'
    run_standard_tick_services(orch, state=TickServiceState(), now=later.timestamp())
    after = service.writer.event_log.read_all()
    prompts = [e for e in after if e.type == 'run.manager.resident.prompted' and e.payload.get('request_id')]
    assert len(prompts) == 2
    assert prompts[1].payload['request_id'] == first.payload['request_id']
    assert prompts[1].payload['diagnosis_context']['host_manifest_digest']
    # The response is admitted by its configured instance, not a hard-coded role label.
    service.writer.emit('run.manager.agent.recommendation', actor='recovery-manager',
                        payload={'request_id': first.payload['request_id']})
    after = service.writer.event_log.read_all()
    gate = next(e for e in after if e.type == 'human.escalate')
    assert _host_diagnosis_ready(service.state_dir, config, service.project_root, after, gate)
    assert sum(e.type == 'runtime.action.attempt.started' and e.payload.get('kind') == 'verify' for e in after) == 1
    assert not any(e.type == 'human.escalation.acknowledged' for e in after)
