"""Real scheduler entry points for an unsent Candidate retry behind a provider gate."""

import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from tests.test_external_gate_dispatch import _blocked
from tests.test_reader_fanout_runtime import _durable_reader_state
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_attempt_runtime import reconcile_task_attempts, task_attempt_store
from zf.runtime.workflow_operation import reduce_workflow_operations


def cohort(tmp_path):
    run_once = Orchestrator.run_once

    def start(runtime, *args, **kwargs):
        cfg = runtime.config
        cfg.workflow.task_attempt.mode = "enforce"
        stage = cfg.workflow.stages[0]
        for name in ["verify-2", "verify-3"]:
            cfg.roles.append(replace(cfg.roles[0], name=name, instance_id=name))
            stage.children.append(replace(stage.children[0], role_instance=name))
        return run_once(runtime, *args, **kwargs)

    with patch.object(Orchestrator, "run_once", start):
        state, log, transport, runtime, config, _ = _durable_reader_state(tmp_path)
    runtime.event_writer.emit("run.started", payload={"workflow_run_id": "run-durable"})
    return state, log, transport, runtime, config


def quota_close(runtime, role):
    return runtime.event_writer.emit("provider.turn.closed", actor=role,
        payload={"backend": "codex", "turn_id": f"{role}-first-turn",
                 "provider_status": "failed", "provider_stop_reason": "quota_exhausted"})


def gate_transport(monkeypatch, transport, gated):
    original = transport.send_task

    def send(role, *args, **kwargs):
        if role in gated:
            raise _blocked()
        return original(role, *args, **kwargs)

    monkeypatch.setattr(transport, "send_task", send)
    monkeypatch.setattr(transport, "capture_log", lambda role, **kwargs:
                        "You have hit your usage limit" if role in gated else "")


def test_gated_retries_do_not_starve_other_readers_or_consume_worker_leases(tmp_path, monkeypatch):
    state, log, transport, runtime, cfg = cohort(tmp_path)
    gated = {"verify-1", "verify-2"}
    gate_transport(monkeypatch, transport, gated)
    stops = [quota_close(runtime, role) for role in sorted(gated)]
    runtime.run_once(events=stops)
    for _ in range(3):
        runtime.run_once(events=[])
    deferred = log.query(type="fanout.child.dispatch_deferred")
    assert sorted(e.payload["role_instance"] for e in deferred) == sorted(gated)
    assert len(transport.sent) == 3

    store = task_attempt_store(runtime)
    waiting = [row for row in store.current_rows() if row.get("unsent_external_gate")]
    assert {row["instance_id"] for row in waiting} == gated
    for row in waiting:
        store.renew_lease(row["attempt_id"], updated_at=row["updated_at"],
                          lease_expires_at="2000-01-01T00:00:00+00:00")
    reconcile_task_attempts(runtime)
    assert all(store.get(row["attempt_id"])["status"] == "prepared" for row in waiting)
    runtime = Orchestrator(state, cfg, transport)
    now = runtime._now()
    monkeypatch.setattr(runtime, "_now", lambda: now)
    runtime.run_once(events=[])
    gated.remove("verify-2")
    runtime.run_once(events=[])
    assert len(transport.sent) == 3
    now += cfg.orchestrator.rate_limit_cooldown_s + 1
    for _ in range(3):
        runtime.run_once(events=[])
    assert len(log.query(type="fanout.child.dispatch_deferred")) == 2
    assert [s[0] for s in transport.sent[3:]] == ["verify-2"]
    sent = next(row for row in store.current_rows() if row["instance_id"] == "verify-2")
    assert sent["status"] == "sent" and not sent.get("unsent_external_gate")
    assert sent["lease_expires_at"] > "2000-01-01T00:00:00+00:00"
    assert len(reduce_workflow_operations(log.read_all())) == 3
    assert not log.query(type="fanout.child.failed")


@pytest.mark.parametrize("fault", ["", "ambiguous_claim", "reported_sent", "wrong_gate", "unknown_marker", "wrong_owner", "no_grant", "lower_limit", "changed_source", "paused"])
def test_legacy_expired_unsent_retry_uses_bounded_successor_only(tmp_path, monkeypatch, fault):
    state, log, transport, runtime, cfg = cohort(tmp_path)
    gated = {"verify-2"}
    gate_transport(monkeypatch, transport, gated)
    runtime.run_once(events=[quota_close(runtime, "verify-2")])
    store = task_attempt_store(runtime)
    row = next(r for r in store.current_rows() if r["instance_id"] == "verify-2")
    assert row["ordinal"] == 2 and row["status"] == "prepared"
    # Reproduce the old on-disk schema and actual scheduler expiry before an upgrade.
    old_store = json.loads(store.path.read_bytes())
    held = old_store["attempts"][row["attempt_id"]]
    held.pop("unsent_external_gate")
    held["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
    if fault == "ambiguous_claim":
        held["delivery_claimed_at"] = row["updated_at"]
    if fault == "reported_sent":
        held["sent_at"] = row["updated_at"]
    store.path.write_text(json.dumps(old_store))
    reconcile_task_attempts(runtime)
    assert store.get(row["attempt_id"])["status"] == "expired"
    original = next(e for e in log.query(type="fanout.child.dispatched")
                    if e.payload["role_instance"] == "verify-2")
    wait, = log.query(type="fanout.child.dispatch_deferred")
    # Old retry emitted child.failed when attempting to claim the expired lease.
    failure = runtime.event_writer.emit("fanout.child.failed", actor="zf-cli", task_id=original.task_id,
        causation_id=original.id, correlation_id=original.correlation_id, payload={
            **{k: wait.payload[k] for k in ("fanout_id", "trace_id", "stage_id", "child_id", "run_id",
                "role_instance", "task_id", "retry_of_run_id", "attempt")}, "reason": "expired attempt"})
    if fault in {"wrong_gate", "unknown_marker", "wrong_owner", "no_grant"}:
        target = wait.id if fault != "no_grant" else next(e.id for e in log.query(type="task.attempt.retry_scheduled")
                                                            if e.payload["attempt_id"] == row["attempt_id"])
        # A missing authentic receipt cannot be replaced by an agent's assertion.
        path = log.path
        lines = [json.loads(line) for line in path.read_bytes().splitlines()]
        for item in lines:
            if item["id"] == target:
                if fault == "unknown_marker":
                    item["payload"]["reason"] = "provider_interactive:unconfirmed"
                elif fault == "wrong_owner":
                    item["payload"]["role_instance"] = "verify-1"
                else:
                    item["origin"] = "agent"
        path.write_text("".join(json.dumps(item) + "\n" for item in lines))
    if fault == "changed_source":
        (state / original.payload["attempt_source_manifest_ref"]).write_text("{}")
    if fault == "lower_limit":
        cfg.workflow.task_attempt.max_attempts = 2
    if fault == "paused":
        runtime.event_writer.emit("run.paused", payload={"workflow_run_id": "run-durable"})
    gated.clear()
    runtime = Orchestrator(state, cfg, transport)
    runtime.run_once(events=[failure])
    runtime.run_once(events=[])
    new_dispatches = [e for e in log.query(type="fanout.child.dispatched") if e.payload.get("retry_of_run_id")]
    assert len(new_dispatches) == (0 if fault else 1)
    if not fault:
        replacement, = new_dispatches
        assert replacement.payload["role_instance"] == "verify-2"
        assert replacement.payload["run_id"].endswith("-retry-2")
        assert replacement.payload["recovery_decision_event_id"] == failure.id
        assert replacement.payload["operation_id"] == original.payload["operation_id"]
        assert replacement.payload["payload"]["request_hash"] == original.payload["payload"]["request_hash"]
        assert store.get(row["attempt_id"])["superseded_from_status"] == "expired"
        assert failure in log.query(type="fanout.child.failed")
