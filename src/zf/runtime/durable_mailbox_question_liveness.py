"""Recover the unanswered mailbox obligation after its exact claimant ends."""

from zf.runtime.durable_mailbox_authority import record, terminal
from zf.runtime.pause_lifecycle import is_run_dispatch_paused
from zf.runtime.provider_turn_liveness import _parse_event_ts, active_codex_turn_from_events
from zf.runtime.stream_turn_lifecycle import open_stream_turn

_DIAGNOSIS = "question_taker_stopped_without_answer"
_EXECUTION = ("operation_id", "attempt_id", "dispatch_id", "lease_id")
_NATIVE = ("session_id", "turn_id")
_CODEX_HOOKS = {"codex.hook.user_prompt_submit", "codex.hook.stop"}


def _execution_matches(payload, consumer):
    required = ("operation_id", "attempt_id")
    return all(consumer.get(k) and payload.get(k) == consumer[k] for k in required) and all(
        not consumer.get(k) or payload.get(k) == consumer[k] for k in ("dispatch_id", "lease_id")
    )


def _claim_events(events, consumer):
    """Scope native evidence to the exact worker attempt or this delivered advisory."""
    actor, run = consumer["actor"], consumer["workflow_run_id"]
    advisory = consumer.get("assistance_request_id")
    delivered = not advisory
    dispatches = {}
    for event in events:
        p = event.payload
        if not delivered:
            delivered = (
                record(event, "notification")
                and p.get("request_id") == advisory
                and p.get("workflow_run_id") == run
                and p.get("owner_instance") == actor
                and p.get("status") == "attempted"
            )
            continue
        scoped_run = p.get("workflow_run_id") or p.get("run_id") or event.correlation_id
        if scoped_run and scoped_run != run:
            continue
        scoped = scoped_run == run and _execution_matches(p, consumer)
        if (
            scoped
            and event.origin == "kernel"
            and event.actor == "orchestrator"
            and event.type in {"task.dispatched", "task.pipeline.stage.dispatched"}
            and all(consumer.get(k) for k in _EXECUTION)
            and p.get("role_instance") == actor
            and p.get("task_id") == consumer.get("task_id")
            and p.get("workdir")
        ):
            dispatches[event.id] = p["workdir"]
        if event.actor != actor:
            continue
        # hook-recv uses the governed dispatch as causation, and keeps Stop's historical empty origin.
        from_dispatch = (
            event.type in _CODEX_HOOKS
            and event.causation_id in dispatches
            and p.get("cwd") == dispatches[event.causation_id]
        )
        if not (advisory or scoped or from_dispatch):
            continue
        if event.origin == "kernel" or (
            not event.origin and event.type in _CODEX_HOOKS and (advisory or from_dispatch)
        ):
            yield event


def claim_turn(events, consumer):
    """Capture one native turn. A shared role name alone cannot identify the caller."""
    actor = consumer["actor"]
    related = list(_claim_events(events, consumer))
    backend = consumer.get("provider")
    stream = open_stream_turn(related, actor, require_unique=True) if backend != "codex" else None
    codex = (
        active_codex_turn_from_events(related, actor, require_unique=True)
        if backend != "claude-code"
        else None
    )
    if bool(stream) == bool(codex):
        return {}
    if stream:
        if (
            stream.payload.get("workflow_run_id") != consumer["workflow_run_id"]
            or stream.payload.get("transport") != "stream-json"
        ):
            return {}
        opening, backend = stream, "claude-code"
    else:
        opening = next(
            e
            for e in reversed(related)
            if e.type == "codex.hook.user_prompt_submit"
            and all(e.payload.get(k) == codex[k] for k in _NATIVE)
        )
        backend = "codex"
    native = {k: opening.payload.get(k) for k in _NATIVE}
    if not all(native.values()) or any(
        consumer.get("provider_" + k) not in {None, "", v} for k, v in native.items()
    ):
        return {}
    bound = {**native, "backend": backend, "opened_event_id": opening.id}
    if backend == "codex" and consumer.get("operation_id") and not _execution_matches(opening.payload, consumer):
        bound["dispatch_event_id"] = opening.causation_id
    return bound


def stopped_turn(events, claim):
    """A close must follow the claim and identify the same provider invocation."""
    pos = next(i for i, e in enumerate(events) if e.id == claim.id)
    prior = events[:pos]
    consumer = claim.payload["consumer"]
    turn = claim.payload["provider_turn"] if "provider_turn" in claim.payload else claim_turn(prior, consumer)
    if not turn:
        return None
    opening = next((e for e in prior if e.id == turn["opened_event_id"]), None)
    if (
        not opening
        or opening.actor != consumer["actor"]
        or any(opening.payload.get(k) != turn[k] for k in _NATIVE)
    ):
        return None
    started, claimed = _parse_event_ts(opening.ts), _parse_event_ts(claim.ts)
    if not started or not claimed or started > claimed:
        return None
    for close in events[pos + 1 :]:
        p = close.payload
        if (
            close.actor != opening.actor
            or (p.get("workflow_run_id") or p.get("run_id") or close.correlation_id) not in {consumer["workflow_run_id"], None, ""}
            or any(p.get(k) != turn[k] for k in _NATIVE)
            or not (ended := _parse_event_ts(close.ts))
            or ended < claimed
        ):
            continue
        if turn["backend"] == "claude-code":
            scope = ("workflow_run_id", "instance_id", *_EXECUTION)
            if (
                close.origin == "kernel"
                and close.type == "provider.turn.closed"
                and p.get("backend") == "claude-code"
                and p.get("transport") == "stream-json"
                and p.get("provider_status") in {"completed", "failed", "cancelled"}
                and not p.get("tool_identity_unknown")
                and not p.get("pending_tool_ids")
                and all(p.get(k) == opening.payload.get(k) for k in scope)
            ):
                return close
            continue
        if close.type == "provider.turn.closed" and p.get("backend") != "codex":
            continue
        if close.type not in {"codex.hook.stop", "provider.turn.closed"}:
            continue
        advisory = bool(consumer.get("assistance_request_id"))
        linked = (
            bool(turn.get("dispatch_event_id"))
            and turn["dispatch_event_id"] == opening.causation_id == close.causation_id
            and bool(p.get("cwd"))
            and p["cwd"] == opening.payload.get("cwd")
            and close.correlation_id in {None, "", consumer["workflow_run_id"]}
        )
        scoped = (
            close.origin == "kernel"
            and _execution_matches(p, consumer)
            and (p.get("workflow_run_id") or p.get("run_id") or close.correlation_id)
            == consumer["workflow_run_id"]
        )
        if close.origin == "kernel" or (close.type == "codex.hook.stop" and not close.origin):
            if scoped or linked or advisory:
                return close
    return None


def peer_loss(events, claim, operation, binding):
    actor = claim.payload["consumer"]
    if binding and (binding["operation_id"], binding["attempt_id"]) != (
        actor["operation_id"],
        actor["attempt_id"],
    ):
        return None
    if (
        operation
        and operation.get("status") in {"settled", "failed", "superseded", "cancelled"}
        and operation.get("active_attempt_id") == actor["attempt_id"]
        and operation.get("request_hash") == actor["request_hash"]
    ):
        pos = next(i for i, e in enumerate(events) if e.id == claim.id)
        return next(
            (
                e
                for e in reversed(events[pos + 1 :])
                if e.id == operation["last_event_id"]
                and e.type == "workflow.operation." + operation["status"]
                and e.origin == "kernel"
                and e.payload.get("workflow_run_id") == actor["workflow_run_id"]
                and e.payload.get("operation_id") == actor["operation_id"]
            ),
            None,
        )
    return stopped_turn(events, claim)


def pending_peer_diagnoses(events):
    """Give a still-open diagnostic receipt to the existing Run Manager owner."""
    pending = {}
    for e in events:
        if record(e, "disposition") and e.payload.get("reason") == _DIAGNOSIS:
            pending[e.payload["message_key"]] = e
        elif (
            record(e, "question")
            or record(e, "disposition")
            and e.payload.get("status") in {"cancelled", "timed_out"}
        ):
            pending.pop(e.payload["message_key"], None)
        elif record(e, "binding"):
            prior = pending.get(e.payload["message_key"])
            if prior and any(
                e.payload["consumer"].get(k) != prior.payload["consumer"].get(k)
                for k in ("attempt_id", "operation_id")
            ):
                pending.pop(e.payload["message_key"])
    actions = []
    for e in pending.values():
        p = e.payload
        run = p["workflow_run_id"]
        if is_run_dispatch_paused(events, run) or terminal(events, run):
            continue
        actions.append(
            {
                "schema_version": "run-manager.pending-action.v1",
                "action": "diagnose-attention",
                "safe_resume_action": "diagnose_attention",
                "checkpoint_id": e.id,
                "fingerprint": e.id,
                "workflow_run_id": run,
                "task_id": p["consumer"].get("task_id", ""),
                "source_event_id": e.id,
                "source_event_ids": [*p["source_event_ids"], e.id],
                "owner_route": "run_manager",
                "failure_class": "unanswered_peer_question",
                "intervention_class": "diagnose",
                "action_policy": "needs_diagnosis",
                "expected_output": ["run.manager.agent.recommendation"],
                "summary": "受信接手者已结束，同一问题尚无答复或获准继任者。请核实已有责任与证据，提出后续处置。",
            }
        )
    return actions
