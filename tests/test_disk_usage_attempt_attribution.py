"""Native usage for a reworked Reader cannot renew an orphaned delivery."""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from tests.test_scheduler_task_attempt import _runtime
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.backend_session_reader import UsageReport
from zf.runtime.task_attempt_runtime import mark_task_attempt_sent, prepare_task_attempt
from zf.runtime.transport import DispatchContext


def test_rework_session_usage_is_bound_only_to_its_delivered_attempt(tmp_path):
    runtime, _, state = _runtime(tmp_path, mode="enforce")
    role = runtime.config.roles[0]
    role = replace(role, backend="codex", role_kind="reader", lifecycle=replace(role.lifecycle, mode="on_demand", reader_context_scope="dispatch"))
    session = str(uuid4())
    registry = runtime._role_lifecycle_registry()
    registry.bind_codex_session(role.instance_id, session)
    registry.update_instance_meta(role.instance_id, fanout_context_task_id="TASK-1",
                                  fanout_context_trace_id="RUN-1", fanout_context_dispatch_id="replacement")
    attempts = []
    for dispatch in ("interrupted", "replacement"):
        path = state / f"{dispatch}.md"
        path.write_text("# Review\n", encoding="utf-8")
        attempt = prepare_task_attempt(runtime, context=DispatchContext(
            task_id="TASK-1", role_name=role.name, instance_id=role.instance_id, dispatch_id=dispatch,
            run_id="RUN-1", operation_id=f"op-{dispatch}",
        ), briefing_path=path)
        assert attempt is not None
        attempts.append(attempt)
    orphan, replacement = attempts
    mark_task_attempt_sent(runtime, replacement)
    store = TaskAttemptStore(state / "task_attempts.json")
    before = store.get(orphan.context.attempt_id)["lease_expires_at"]

    def sample(sid):
        usage = UsageReport(30, 5, 1200, 0.03, datetime.now(timezone.utc).isoformat(),
                            model="test", usage_semantics="cumulative", provider_session_id=sid,
                            usage_series_id=f"codex:{sid}", raw={"input_tokens": 30, "output_tokens": 5})
        assert runtime._synthesize_agent_usage(role, usage)
        return runtime.event_log.query(type="agent.usage")[-1]

    active = sample(session)
    unknown = sample(str(uuid4()))
    assert unknown.task_id is None and "operation_id" not in unknown.payload
    assert unknown.payload["usage_binding_status"] == "unattributed"

    store.update(replacement.context.attempt_id, status="succeeded", updated_at=datetime.now(timezone.utc).isoformat())
    tail = sample(session)
    assert tail.task_id is None and "operation_id" not in tail.payload
    assert tail.payload["usage_binding_status"] == "unattributed"
    assert active.payload.get("attempt_id") == replacement.context.attempt_id
    assert active.payload["provider_session_id"] == session
    assert store.get(orphan.context.attempt_id)["lease_expires_at"] == before
    assert not any(e.payload.get("attempt_id") == orphan.context.attempt_id
                   for e in runtime.event_log.query(type="task.attempt.heartbeat"))


def test_native_cumulative_usage_keeps_session_when_task_ownership_is_unknown(tmp_path):
    from tests.test_provider_usage_reconciliation import _write_rollout, SESSION_ID
    from zf.runtime.backend_session_reader import CodexSessionReader

    runtime, _, _ = _runtime(tmp_path, mode="enforce")
    report = CodexSessionReader().read_latest_usage(_write_rollout(tmp_path))
    assert report.provider_session_id == SESSION_ID
    assert runtime._synthesize_agent_usage(runtime.config.roles[0], report)
    receipt = runtime.event_log.query(type="agent.usage")[-1]
    assert receipt.payload["provider_session_id"] == SESSION_ID
    assert receipt.payload["usage"]["output_tokens"] > 0 and receipt.task_id is None
    assert receipt.payload["usage_binding_status"] == "unattributed"
    assert not runtime.event_log.query(type="task.attempt.heartbeat")
    assert not runtime._synthesize_agent_usage(runtime.config.roles[0], report)


def test_disk_sample_for_resident_operation_requires_its_native_session(tmp_path):
    from zf.core.events import ZfEvent

    runtime, _, _ = _runtime(tmp_path, mode="enforce")
    role = runtime.config.roles[0]
    session = str(uuid4())
    fields = {"workflow_run_id": "RUN", "operation_id": "op", "operation_type": "workflow",
              "request_hash": "h", "task_id": "T", "active_attempt_id": "a", "role_instance": role.instance_id,
              "dispatch_id": "d", "provider_session_id": session}
    for kind in ("requested", "started"):
        runtime.event_writer.append(ZfEvent(type=f"workflow.operation.{kind}", origin="kernel", payload=fields))

    def sample(session_id):
        usage = UsageReport(4, 2, 100, 0.04, datetime.now(timezone.utc).isoformat(),
                            model="test", provider_session_id=session_id, raw={"input_tokens": 4})
        runtime._synthesize_agent_usage(role, usage)
        return runtime.event_log.query(type="agent.usage")[-1]

    assert sample(session).payload.get("operation_id") == "op"
    foreign = sample(str(uuid4()))
    assert foreign.payload["usage_binding_status"] == "unattributed"
    assert foreign.task_id is None and "operation_id" not in foreign.payload


def test_shared_task_stage_session_is_bound_to_its_current_placement(tmp_path):
    from zf.core.events import ZfEvent

    runtime, _, state = _runtime(tmp_path, mode="enforce")
    role = replace(runtime.config.roles[0], backend="codex")
    session = str(uuid4())
    registry = runtime._role_lifecycle_registry()
    placement = registry.bind_task_stage_session(
        workflow_run_id="RUN-1", task_id="TASK-1", stage="impl", rework_affinity_id="v1",
        role_instance=role.instance_id, role_config_digest="sha", workspace_generation=1, placement_epoch=1,
        backend="codex",
    )
    registry.activate_task_stage_session(binding_key=placement["binding_key"], role_instance=role.instance_id)
    assert registry.bind_codex_session(role.instance_id, session)
    path = state / "writer.md"
    path.write_text("# Writer\n", encoding="utf-8")
    attempt = prepare_task_attempt(runtime, context=DispatchContext(
        task_id="TASK-1", role_name=role.name, instance_id=role.instance_id, dispatch_id="dispatched",
        run_id="RUN-1", operation_id="op-impl", task_pipeline_stage="impl", placement_epoch=1,
    ), briefing_path=path)
    assert attempt is not None
    mark_task_attempt_sent(runtime, attempt)
    fields = {"workflow_run_id": "RUN-1", "operation_id": "op-impl", "parent_stage_id": "impl",
              "task_id": "TASK-1", "role_instance": role.instance_id, "operation_type": "task_pipeline",
              "request_hash": "h", "active_attempt_id": "WRC-1", "dispatch_id": "dispatched"}
    for kind in ("requested", "started"):
        runtime.event_writer.append(ZfEvent(type=f"workflow.operation.{kind}", origin="kernel", payload=fields))

    def sample():
        report = UsageReport(4, 2, 100, 0.04, datetime.now(timezone.utc).isoformat(),
                             model="test", provider_session_id=session, raw={"input_tokens": 4})
        runtime._synthesize_agent_usage(role, report)
        return runtime.event_log.query(type="agent.usage")[-1]

    # A reader lane can serve a task pipeline after an unrelated fanout.
    role = replace(role, role_kind="reader")
    runtime.event_writer.append(ZfEvent(type="fanout.child.dispatched", origin="kernel", payload={
        "role_instance":role.instance_id, "operation_id":"historical-reader", "dispatch_id":"old-send",
        "run_id":"old-send", "workflow_run_id":"old-run", "retry_of_run_id":"old-logical-input",
    }))
    assert sample().payload.get("attempt_id") == attempt.context.attempt_id
    registry.bind_task_stage_session(
        workflow_run_id="RUN-1", task_id="TASK-1", stage="impl", rework_affinity_id="v1",
        role_instance=role.instance_id, role_config_digest="sha", workspace_generation=1, placement_epoch=2,
        backend="codex",
    )
    assert sample().payload.get("usage_binding_status") == "unattributed"


@pytest.mark.parametrize(("scope", "fresh_synth"), [("workflow", False), ("dispatch", False), ("dispatch", True)])
def test_workflow_reader_redrive_binds_native_turn_to_one_physical_attempt(tmp_path, scope, fresh_synth):
    from zf.core.events import ZfEvent
    from zf.runtime.backend_session_reader import CodexSessionReader, read_role_usage
    from zf.runtime.disk_usage_attempt_binding import disk_usage_attempt_binding
    from datetime import timedelta
    import json

    runtime, _, state = _runtime(tmp_path, mode="enforce")
    role = replace(runtime.config.roles[0], backend="codex", role_kind="reader",
                   lifecycle=replace(runtime.config.roles[0].lifecycle, mode="on_demand", reader_context_scope=scope))
    session, old, new = (str(uuid4()) for _ in range(3))
    physical = "retry-delivery"
    op = "op-reader"
    attempt = prepare_task_attempt(runtime, context=DispatchContext(
        task_id="T", role_name=role.name, instance_id=role.instance_id,
        dispatch_id=physical, run_id="RUN", operation_id=op,
    ), briefing_path=state / "reader.md")
    assert attempt
    mark_task_attempt_sent(runtime, attempt, delivery_evidence={"role_instance":role.instance_id})
    identity = {"task_id": "T", "workflow_run_id": "RUN", "operation_id": op,
                "role_instance": role.instance_id, "dispatch_id": physical,
                "attempt_id": attempt.context.attempt_id, "lease_id": attempt.context.lease_id}
    for kind in ("requested", "started"):
        runtime.event_writer.append(ZfEvent(type=f"workflow.operation.{kind}", origin="kernel", payload={
            **identity, "operation_type":"fanout_reader_child", "request_hash":"pinned",
            "active_attempt_id":"logical-input-contract",
        }))
    if fresh_synth:
        carrier = {"run_id":physical, "workflow_run_id":"RUN", "role_instance":role.instance_id,
                   "operation_id":op, "attempt_id":"logical-input-contract"}
    else:
        carrier = {**identity, "retry_of_run_id":"logical-input-contract"}
    dispatch = runtime.event_writer.append(ZfEvent(
        type="fanout.synth.dispatched" if fresh_synth else "fanout.child.dispatched",
        origin="kernel", payload=carrier))
    start = datetime.now(timezone.utc)
    def ts(offset):
        return (start + timedelta(seconds=offset)).isoformat()
    runtime.event_writer.append(ZfEvent(type="codex.hook.user_prompt_submit", actor=role.instance_id,
        origin="kernel", causation_id=dispatch.id, ts=ts(2), payload={"session_id":session,"turn_id":new}))
    registry = runtime._role_lifecycle_registry()
    rollout = state / "workdirs" / role.instance_id / "codex-home/sessions/2026/11/26" / (
        f"rollout-2026-11-26T20-00-00-{session}.jsonl")
    rollout.parent.mkdir(parents=True)
    registry.bind_codex_session(role.instance_id, session, session_path=rollout)
    registry.update_instance_meta(role.instance_id, fanout_context_task_id="" if fresh_synth else "T",
                                 fanout_context_trace_id="RUN", fanout_context_dispatch_id=physical)

    def usage(turn, *, context=None, extra=()):
        rows = [("session_meta", {"id":session,"cwd":str(state)}, -100),
                ("event_msg", {"type":"task_started","turn_id":old}, -90),
                # Graceful process stop left the original native turn open.
                ("event_msg", {"type":"task_started","turn_id":turn}, 1),
                ("turn_context", {"turn_id":context or turn,"model":"test","cwd":str(state)}, 2),
                *extra,
                ("event_msg", {"type":"token_count","info":{"last_token_usage":{
                    "input_tokens":40,"output_tokens":5}}}, 4)]
        rollout.write_text("".join(json.dumps({"type":typ,"payload":body,"timestamp":ts(t)})+"\n" for typ,body,t in rows))
        return read_role_usage(CodexSessionReader(), rollout, state=state, role=role, session=session, cached=rollout)

    accepted = usage(new)
    assert disk_usage_attempt_binding(runtime, role, accepted)[0].get("attempt_id") == attempt.context.attempt_id
    assert runtime._synthesize_agent_usage(role, accepted)
    heartbeats = runtime.event_log.query(type="task.attempt.heartbeat")
    assert any(e.payload.get("attempt_id")==attempt.context.attempt_id for e in heartbeats)
    assert disk_usage_attempt_binding(runtime, role, usage(old))[0] == {}
    assert disk_usage_attempt_binding(runtime, role, usage(new, context=old))[0] == {}
    assert disk_usage_attempt_binding(runtime, role, usage(new, extra=[
        ("event_msg", {"type":"turn_aborted","turn_id":new},3)]))[0] == {}
    registry.update_instance_meta(role.instance_id, fanout_context_task_id="ANOTHER-TASK")
    assert disk_usage_attempt_binding(runtime, role, accepted)[0] == {}

    other = prepare_task_attempt(runtime, context=DispatchContext(
        task_id="ANOTHER-TASK", role_name=role.name, instance_id=role.instance_id,
        dispatch_id="other-retry", run_id="RUN", operation_id="another-op",
    ), briefing_path=state / "other.md")
    assert other
    mark_task_attempt_sent(runtime, other, delivery_evidence={"role_instance":role.instance_id})
    registry.update_instance_meta(role.instance_id, fanout_context_dispatch_id="other-retry")
    other_id = {**identity, "task_id":"ANOTHER-TASK", "dispatch_id":"other-retry",
                "operation_id":"another-op", "attempt_id":other.context.attempt_id,
                "lease_id":other.context.lease_id}
    for kind in ("requested", "started"):
        runtime.event_writer.append(ZfEvent(type=f"workflow.operation.{kind}", origin="kernel",
            payload={**other_id, "operation_type":"fanout_reader_child", "request_hash":"other",
                     "active_attempt_id":"other-logical-input"}))
    other_dispatch = runtime.event_writer.append(ZfEvent(type="fanout.child.dispatched", origin="kernel",
        payload={**other_id, "retry_of_run_id":"other-logical-input"}))
    next_turn = str(uuid4())
    prompt = {"session_id":session, "turn_id":next_turn}
    new_usage = usage(next_turn)
    runtime.event_writer.append(ZfEvent(type="codex.hook.user_prompt_submit", actor=role.instance_id,
        origin="worker", causation_id=other_dispatch.id, ts=ts(2), payload=prompt))
    assert disk_usage_attempt_binding(runtime, role, new_usage)[0] == {}
    runtime.event_writer.append(ZfEvent(type="codex.hook.user_prompt_submit", actor=role.instance_id,
        origin="kernel", causation_id=other_dispatch.id, ts=ts(2), payload=prompt))
    assert disk_usage_attempt_binding(runtime, role, accepted)[0] == {}
    bound, _ = disk_usage_attempt_binding(runtime, role, new_usage)
    assert bound["attempt_id"] == other.context.attempt_id
    assert bound["lease_id"] == other.context.lease_id


def test_registered_migrated_reader_requires_exclusive_fresh_session(tmp_path):
    import json
    from datetime import timedelta

    from zf.core.events import ZfEvent
    from zf.runtime.backend_session_reader import CodexSessionReader, read_role_usage
    from zf.runtime.disk_usage_attempt_binding import disk_usage_attempt_binding

    runtime, _, state = _runtime(tmp_path, mode="enforce")
    spec = runtime.config.roles[0]
    role = replace(spec, backend="codex", role_kind="reader",
                   lifecycle=replace(spec.lifecycle, mode="on_demand", reader_context_scope="dispatch"))
    session, turn = str(uuid4()), str(uuid4())
    dispatch_id = "first-delivery"
    attempt = prepare_task_attempt(runtime, context=DispatchContext(
        task_id="T", role_name=role.name, instance_id=role.instance_id, dispatch_id=dispatch_id,
        run_id="RUN", operation_id="op",
    ), briefing_path=state / "input.md")
    assert attempt
    mark_task_attempt_sent(runtime, attempt, delivery_evidence={"role_instance": role.instance_id})
    fields = {"task_id": "T", "workflow_run_id": "RUN", "operation_id": "op",
              "role_instance": role.instance_id, "dispatch_id": dispatch_id,
              "operation_type": "fanout_reader_child", "request_hash": "H",
              "active_attempt_id": "signed-input", "provider_session_id": session}
    for kind in ("requested", "started"):
        runtime.event_writer.append(ZfEvent(type=f"workflow.operation.{kind}", origin="kernel", payload=fields))
    dispatched = runtime.event_writer.append(ZfEvent(type="fanout.child.dispatched", origin="kernel", payload={
        **fields, "attempt_id": attempt.context.attempt_id, "lease_id": attempt.context.lease_id,
    }))
    start = datetime.now(timezone.utc)
    def ts(seconds):
        return (start + timedelta(seconds=seconds)).isoformat()

    path = tmp_path / "previous-provider-home" / f"rollout-2026-11-26T20-00-00-{session}.jsonl"
    path.parent.mkdir()
    registry = runtime._role_lifecycle_registry()
    assert registry.bind_codex_session(role.instance_id, session, session_path=path)
    registry.update_instance_meta(role.instance_id, fanout_context_task_id="T",
                                 fanout_context_trace_id="RUN", fanout_context_dispatch_id=dispatch_id)
    reader = CodexSessionReader()
    def sample(*, previous_turn=False, start_offset=1, cached=path):
        body = [("session_meta", {"id": session, "cwd": str(state)}, -4)]
        if previous_turn:
            body.append(("event_msg", {"type": "task_started", "turn_id": str(uuid4())}, -2))
        body.extend([("event_msg", {"type": "task_started", "turn_id": turn}, start_offset),
                     ("turn_context", {"turn_id": turn, "cwd": str(state), "model": "test"}, 2),
                     ("event_msg", {"type": "token_count", "info": {
                         "last_token_usage": {"input_tokens": 17, "output_tokens": 3}}}, 4)])
        path.write_text("".join(json.dumps({"type": k, "payload": v, "timestamp": ts(t)})+"\n" for k, v, t in body))
        resolved = reader.session_path(str(state.parent), session, cached_path=registry.get_path(role.instance_id))
        usage = read_role_usage(reader, resolved, state=state, role=role, session=session, cached=cached)
        assert usage.provider_session_id == session and not usage.native_turn_sample and not usage.provider_usage_turn_id
        return disk_usage_attempt_binding(runtime, role, usage)[0].get("attempt_id")

    def hook(turn_id, cause=dispatched.id, sid=session):
        runtime.event_writer.append(ZfEvent(type="codex.hook.user_prompt_submit", actor=role.instance_id,
            origin="kernel", causation_id=cause, ts=ts(2), payload={"session_id": sid, "turn_id": turn_id}))

    assert sample() == attempt.context.attempt_id
    assert sample(previous_turn=True) is None
    assert sample(start_offset=-1) is None
    assert sample(cached=None) is None
    # An alias cannot supply native identity, even when ordinary accounting can read it.
    target = path.with_name("restored-record.jsonl")
    path.rename(target)
    path.symlink_to(target)
    assert sample() is None
    path.unlink()
    target.rename(path)
    hook(turn)
    assert sample() == attempt.context.attempt_id
    hook(str(uuid4()))
    assert sample() is None
    hook(turn, cause="previous-dispatch")
    assert sample() is None
    hook(turn, sid=str(uuid4()))
    assert sample() is None
    hook(turn)
    retry = runtime.event_writer.append(ZfEvent(type="fanout.child.dispatched", origin="kernel", payload={
        **dispatched.payload, "retry_of_run_id": "old-physical-delivery",
    }))
    hook(turn, cause=retry.id)
    assert sample() is None
    dispatched = runtime.event_writer.append(ZfEvent(type="fanout.child.dispatched", origin="kernel",
                                                    payload=dispatched.payload))
    hook(turn, cause=dispatched.id)
    assert sample() == attempt.context.attempt_id
    for kind in ("requested", "started"):
        runtime.event_writer.append(ZfEvent(type=f"workflow.operation.{kind}", origin="kernel",
            payload={**fields, "task_id": "OTHER-TASK", "operation_id": "other-op", "dispatch_id": "other-send"}))
    assert sample() is None
