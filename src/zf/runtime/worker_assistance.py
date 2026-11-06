"""Attempt-bound advisory requests; answers never grant a new writer lease."""

from datetime import datetime, timezone
import hashlib
import json
import shlex
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.core.state.locks import locked_path
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.store import TaskStore
from zf.runtime.pause_lifecycle import active_request_age_seconds, is_dispatch_paused
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.workflow_operation import load_workflow_operation


REQUESTED = "worker.assistance.requested"
CLOSED = "worker.assistance.closed"
ANSWER = "orchestrator.rework.triage.recorded"


def _current(state: Path, log, identity: dict[str, Any]) -> dict[str, Any]:
    operation = load_workflow_operation(log, str(identity.get("operation_id") or "")) or {}
    for field in ("workflow_run_id", "task_id", "active_attempt_id", "lease_id"):
        if not identity.get(field) or identity[field] != operation.get(field):
            raise ValueError(f"assistance identity mismatch: {field}")
    if operation.get("status") != "running":
        raise ValueError("assistance requires a running operation")
    task = TaskStore(state / "kanban.json").get(str(identity["task_id"]))
    if task is None or task.status in {"done", "cancelled"}:
        raise ValueError("assistance task is no longer active")
    attempt = TaskAttemptStore(state / "task_attempts.json").current_for_attempt(
        task_id=identity["task_id"], attempt_id=identity["active_attempt_id"],
    ) or {}
    if (attempt.get("attempt_id") != identity["active_attempt_id"]
            or attempt.get("status") not in {"prepared", "delivering", "sent"}
            or attempt.get("lease_id") != identity["lease_id"]):
        raise ValueError("assistance attempt is stale")
    if datetime.fromisoformat(attempt["lease_expires_at"]) <= datetime.now(timezone.utc):
        raise ValueError("assistance lease expired")
    return operation


def request_assistance(
    *, state_dir: Path, writer: EventWriter, config: Any, identity: dict[str, Any],
    actor: str, owner: str, question: str, evidence_refs: list[str], tried: list[str],
) -> dict[str, Any]:
    """Persist one question with the original execution identity and inputs."""
    if owner not in {"orchestrator", "run_manager"} or not question.strip() or not evidence_refs:
        raise ValueError("owner, question and evidence_refs are required")
    if is_dispatch_paused(state_dir, events=writer.event_log.read_all()):
        raise ValueError("dispatch_paused")
    operation = _current(state_dir, writer.event_log, identity)
    if actor != operation.get("role_instance"):
        raise ValueError("only the current worker may request assistance")
    if owner == "orchestrator":
        from zf.runtime.orchestrator_agent_role import configured_orchestrator_role_from_config
        role = configured_orchestrator_role_from_config(config)
    else:
        from zf.runtime.run_manager_resident import build_resident_run_manager_role
        role = build_resident_run_manager_role(config)
    if role is None:
        raise ValueError("assistance owner is not configured")
    digest = hashlib.sha256(json.dumps({**identity, "owner": owner, "question": question,
        "evidence_refs": evidence_refs, "tried": tried}, sort_keys=True).encode()).hexdigest()[:24]
    request_id = f"assistance-{digest}"
    with locked_path(state_dir / "locks" / request_id):
        for event in writer.event_log.read_all():
            if event.type == REQUESTED and event.payload.get("request_id") == request_id:
                return {"status": "requested", "request_id": request_id, "event_id": event.id}
        body = {**identity, "question": question, "evidence_refs": evidence_refs,
                "tried": tried, "owner": owner, "request_id": request_id,
                "request_ref": operation.get("request_ref"), "request_hash": operation.get("request_hash")}
        ref = write_sidecar_json(state_dir, f"diagnostics/assistance/{request_id}.json", body,
                                 kind="worker-assistance", schema_version="worker-assistance.v1",
                                 created_by=actor, required=True)
        event = writer.emit(REQUESTED, actor=actor, task_id=identity["task_id"],
            correlation_id=identity["workflow_run_id"], payload={
                **identity, "request_id": request_id, "owner": owner,
                "owner_instance": role.instance_id or role.name,
                "timeout_seconds": role.stuck_threshold_seconds,
                "request_kind": "worker_guidance", "recovery_context_ref": ref,
                "apply_policy": "proposal_only",
            })
    return {"status": "requested", "request_id": request_id, "event_id": event.id}


def read_assistance(*, state_dir: Path, writer: EventWriter, request_id: str) -> dict[str, Any]:
    events = writer.event_log.read_all()
    request = next((e for e in events if e.type == REQUESTED and e.payload.get("request_id") == request_id), None)
    if request is None:
        raise ValueError("assistance request not found")
    payload = request.payload
    _current(state_dir, writer.event_log, payload)
    if is_dispatch_paused(state_dir, events=events):
        raise ValueError("dispatch_paused")
    if any(e.type == CLOSED and e.payload.get("request_id") == request_id
           and e.payload.get("status") != "answered" for e in events):
        raise ValueError("assistance request is closed without an applicable answer")
    # The immutable request is the version binding; do not substitute the
    # current parent briefing for the one on which the advisor reasoned.
    body = hydrate_sidecar_ref(state_dir, payload["recovery_context_ref"]).payload
    operation = load_workflow_operation(writer.event_log, payload["operation_id"]) or {}
    if body.get("request_hash") != operation.get("request_hash"):
        raise ValueError("assistance input changed")
    answer = next((e for e in reversed(events) if e.type == ANSWER
                   and e.payload.get("request_id") == request_id
                   and e.actor == payload["owner_instance"]
                   and str(e.task_id or e.payload.get("task_id") or "") == payload["task_id"]), None)
    observed_at = datetime.fromisoformat(answer.ts) if answer else datetime.now(timezone.utc)
    age = active_request_age_seconds(events, created_at=datetime.fromisoformat(request.ts), now=observed_at)
    if payload.get("timeout_seconds", 0) > 0 and age >= payload["timeout_seconds"]:
        raise ValueError("assistance response deadline expired")
    if answer is None:
        return {"status": "pending", "request_id": request_id, "owner": payload["owner"]}
    if not answer.payload.get("guidance") or answer.payload.get("apply_policy") != "proposal_only":
        raise ValueError("assistance answer is incomplete or attempts to grant authority")
    recommendation = answer.payload.get("recommended_action")
    return {"status": "guidance" if recommendation in {"continue_rework", "precise_rework"}
            else "controlled_handoff_required", "request_id": request_id,
            "answer_event_id": answer.id, "guidance": answer.payload["guidance"],
            "recommended_action": recommendation, "new_writer_authorized": False}


def reconcile_assistance(*, state_dir: Path, writer: EventWriter, config: Any) -> int:
    """One reconciliation owner across reactive and periodic consumers."""
    with locked_path(state_dir / "locks" / "assistance-reconciliation"):
        return _reconcile_assistance(state_dir=state_dir, writer=writer, config=config)


def _reconcile_assistance(*, state_dir: Path, writer: EventWriter, config: Any) -> int:
    """Durable replay/timeout processing; no model call runs under this lock."""
    events = writer.event_log.read_all()
    closed = {e.payload.get("request_id") for e in events if e.type == CLOSED}
    count = 0
    for request in events:
        if request.type != REQUESTED or request.payload.get("request_id") in closed:
            continue
        p = request.payload
        status, reason = "", ""
        from zf.runtime.orchestrator_agent_role import configured_orchestrator_role_from_config
        from zf.runtime.run_manager_resident import build_resident_run_manager_role
        owner = (configured_orchestrator_role_from_config(config) if p["owner"] == "orchestrator"
                 else build_resident_run_manager_role(config))
        try:
            if owner is None or (owner.instance_id or owner.name) != p["owner_instance"]:
                raise ValueError("assistance owner unavailable")
            result = read_assistance(state_dir=state_dir, writer=writer, request_id=p["request_id"])
            if result["status"] != "pending":
                status = "answered"
        except ValueError as exc:
            if str(exc) == "dispatch_paused":
                continue
            reason = str(exc)
            status = "waiting" if reason in {"assistance response deadline expired", "assistance owner unavailable"} else "stale"
        if not status:
            age = active_request_age_seconds(events,
                created_at=datetime.fromisoformat(request.ts), now=datetime.now(timezone.utc))
            if p.get("timeout_seconds", 0) > 0 and age >= p["timeout_seconds"]:
                status, reason = "waiting", "assistance owner did not deliver the current answer"
        if status:
            writer.emit(CLOSED, actor="run-manager", task_id=p["task_id"],
                correlation_id=p["workflow_run_id"], causation_id=request.id,
                payload={**p, "status": status, "reason": reason})
            if status == "waiting":
                writer.emit("human.escalate", actor="run-manager", task_id=p["task_id"],
                    correlation_id=p["workflow_run_id"], causation_id=request.id,
                    payload={"request_id": p["request_id"], "reason": reason,
                             "owner_route": p["owner"], "blocking_scope": "task",
                             "task_id": p["task_id"], "resume_condition": "current advisor response or authorized handoff"})
            count += 1
        elif p["owner"] == "orchestrator" and not any(
            e.type == "orchestrator.rework.triage.requested" and e.payload.get("request_id") == p["request_id"]
            for e in events
        ):
            writer.emit("orchestrator.rework.triage.requested", actor="run-manager",
                task_id=p["task_id"], correlation_id=p["workflow_run_id"],
                causation_id=request.id, payload={**p, "failure_event_ids": [request.id]})
            count += 1
    return count


def pending_assistance_actions(events) -> list[dict[str, Any]]:
    """RM guidance uses the already configured resident, not an Impl slot."""
    closed = {e.payload.get("request_id") for e in events if e.type == CLOSED}
    prompted = {e.payload.get("request_id") for e in events if e.type == "run.manager.resident.prompted"}
    return [{
        **e.payload, "action": "diagnose-attention", "safe_resume_action": "diagnose_attention",
        "checkpoint_id": e.payload["request_id"], "fingerprint": e.payload["request_id"],
        "source_event_id": e.id, "source_event_ids": [e.id],
        "owner_route": "run_manager", "action_policy": "needs_diagnosis",
        "suggested_route": "run_manager_resident_agent", "failure_class": "worker_assistance",
        "expected_output": [ANSWER], "effect_expected_events": [CLOSED],
        "effect_bindings": {"request_id": e.payload["request_id"], "status": "answered"},
        "summary": "当前 Worker 请求只读指导；读取 recovery_context_ref，回答绑定 request_id。",
        "source_ref": e.payload["recovery_context_ref"]["ref"],
    } for e in events if e.type == REQUESTED and e.payload.get("owner") == "run_manager"
       and e.payload.get("request_id") not in closed | prompted]


def assistance_briefing(payload: dict[str, Any], *, actor: str) -> str:
    from zf.runtime.cli_command import zf_cli_cmd

    answer = {"request_id": payload["request_id"], "task_id": payload["task_id"],
              "recommended_action": "precise_rework", "guidance": "REPLACE_WITH_EVIDENCED_GUIDANCE",
              "apply_policy": "proposal_only"}
    ref = payload.get("recovery_context_ref") or {}
    return "\n".join([
        "这是运行中 Worker 的只读求助，原 attempt 仍是唯一写入者。",
        f"先读取固定问题、已尝试方法与证据：{ref.get('ref', '')}。",
        "给出合同内指导用 precise_rework；需要更改合同/执行身份则提出 replan/diagnose/human，不能直接执行重派或扩权。",
        "不得编辑业务代码、改 canonical 状态或宣称独立验证通过。回答后结束 turn。",
        f"{zf_cli_cmd()} emit {ANSWER} --task {shlex.quote(str(payload['task_id']))} "
        f"--actor {shlex.quote(actor)} --payload {shlex.quote(json.dumps(answer, ensure_ascii=False))}",
    ])


def claim_assistance_dispatch(runtime, request) -> bool:
    """Reserve a read-only delivery before transport; uncertain sends are not replayed."""
    if request.payload.get("request_kind") != "worker_guidance":
        return True
    request_id = request.payload["request_id"]
    with locked_path(Path(runtime.state_dir) / "locks" / request_id):
        events = runtime.event_log.read_all()
        if any(e.type in {CLOSED, "worker.assistance.dispatching"}
               and e.payload.get("request_id") == request_id for e in events):
            return False
        try:
            if read_assistance(state_dir=runtime.state_dir, writer=runtime.event_writer,
                               request_id=request_id)["status"] != "pending":
                return False
        except ValueError:
            return False
        runtime.event_writer.emit("worker.assistance.dispatching", actor="zf-runtime",
            task_id=request.task_id, causation_id=request.id,
            payload={"request_id": request_id, "workflow_run_id": request.payload["workflow_run_id"],
                     "owner_instance": request.payload["owner_instance"]})
        return True


def dispatch_pending_assistance(runtime) -> None:
    """A fresh reactor recovers requests whose wake event was missed."""
    from zf.runtime.orchestrator_agent_role import configured_orchestrator_role_from_config
    role = configured_orchestrator_role_from_config(runtime.config)
    if role is None:
        return
    events = runtime.event_log.read_all()
    closed = {e.payload.get("request_id") for e in events if e.type == CLOSED}
    if any(e.type == REQUESTED and e.payload.get("request_id") not in closed for e in events):
        reconcile_assistance(state_dir=runtime.state_dir, writer=runtime.event_writer, config=runtime.config)
        events = runtime.event_log.read_all()
    handled = {e.payload.get("request_id") for e in events
               if e.type in {CLOSED, "worker.assistance.dispatching"}}
    for event in events:
        if (event.type == "orchestrator.rework.triage.requested"
                and event.payload.get("request_kind") == "worker_guidance"
                and event.payload.get("request_id") not in handled):
            runtime._fire_orchestrator_turn(role, [event])
