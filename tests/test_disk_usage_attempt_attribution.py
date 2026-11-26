"""Native usage for a reworked Reader cannot renew an orphaned delivery."""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

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

    assert sample().payload.get("attempt_id") == attempt.context.attempt_id
    registry.bind_task_stage_session(
        workflow_run_id="RUN-1", task_id="TASK-1", stage="impl", rework_affinity_id="v1",
        role_instance=role.instance_id, role_config_digest="sha", workspace_generation=1, placement_epoch=2,
        backend="codex",
    )
    assert sample().payload.get("usage_binding_status") == "unattributed"
