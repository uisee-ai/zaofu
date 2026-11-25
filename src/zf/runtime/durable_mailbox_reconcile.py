"""Reconcile pending mailbox responsibility against current Run and contract authority."""

from zf.runtime.durable_mailbox_authority import admission_offset, can_receive, record, roster
from zf.runtime.call_result_envelope import canonical_json_sha256 as digest
from zf.runtime.durable_mailbox_index import MailboxIndex


def reconcile_mailbox(runtime, events=None):
    """Cancel pending deliveries only when positive authority evidence proves revocation."""
    from zf.runtime.durable_mailbox import _expired, _question_states, _write
    from zf.core.state.locks import locked_path
    from zf.runtime.durable_mailbox_authority import operation_binding, terminal, owner_role
    from zf.runtime.workflow_operation import reduce_workflow_operations
    from zf.runtime.durable_mailbox_question_liveness import peer_loss
    from zf.runtime.pause_lifecycle import is_run_dispatch_paused

    # An unconfigured project should not allocate a mailbox index. During drain,
    # losing the cache must still discover previously accepted ledger entries.
    if events is None:
        from zf.runtime.durable_mailbox_authority import enabled

        if not enabled(runtime.config) and not (runtime.state_dir / "private/durable-mailbox-index").exists():
            events = runtime.event_log.read_all()
    if events is not None and not any(record(e, "accepted") for e in events):
        return 0
    with locked_path(runtime.state_dir / "locks/durable-mailbox"):
        index = MailboxIndex(runtime)
        indexed = index.read(open_only=True)
        canonical = [e for e in indexed if e.origin == "kernel"]
        expected = index.offset
        by_run = {}
        operations = reduce_workflow_operations(canonical)
        acknowledged = {
            (r["message_key"], digest(r["recipient"]))
            for e in canonical
            if record(e, "acknowledged")
            for r in e.payload["items"]
        }
        disposed = {
            (e.payload["message_key"], e.payload["recipient_digest"])
            for e in canonical
            if record(e, "disposition") and e.payload["status"] in {"cancelled", "timed_out"}
        }
        states = _question_states(canonical)
        waits = {e.payload["message_key"]: e for e in canonical if record(e, "question") and e.payload.get("action") == "wait"}
        claims = {e.payload["message_key"]: e for e in canonical
                  if record(e, "question") and e.payload.get("action") == "claim"}
        diagnosed = {e.causation_id for e in canonical if record(e, "disposition")
                     and e.payload.get("status") == "diagnosis_required"}
        bindings = {
            (e.payload["message_key"], e.payload["recipient_digest"]): e.payload["consumer"]
            for e in canonical
            if record(e, "binding")
        }
        written = 0
        for e in canonical:
            if not record(e, "accepted"):
                continue
            p = e.payload
            run = p["workflow_run_id"]
            key = p["message_key"]
            last = states.get(key, {})
            if last.get("action") in {"answer", "cancel"}:
                continue
            if run not in by_run:
                by_run[run] = (terminal(canonical, run), None)
            ended, members = by_run[run]
            for addr in [last["target"]] if last.get("target") else p["recipients"]:
                pair = (key, digest(addr))
                if pair in disposed or (p["kind"] != "question" and pair in acknowledged):
                    continue
                reason = ""
                binding = None
                if ended:
                    reason = "run_terminal"
                elif _expired(canonical, e, p.get("deadline")):
                    reason = "message_deadline_elapsed"
                elif addr["mode"] == "execution":
                    op = operations.get(addr["operation_id"])
                    if op:
                        try:
                            if not can_receive(addr, operation_binding(op), [], p):
                                reason = "execution_replaced"
                        except ValueError:
                            reason = "execution_ended"
                elif addr["mode"] == "coordination":
                    try:
                        current = owner_role(runtime.config, addr["owner"])
                    except ValueError:
                        current = ""
                    if current != addr["role"]:
                        reason = "coordination_authority_changed"
                else:
                    if members is None:
                        members = roster(runtime, canonical, operations, run)
                        by_run[run] = (ended, members)
                    scope = ("stage", "obligation_id", "work_unit_id", "pipeline_stage", "task_id")
                    current = next((ids for member, ids in members if member == addr), [])
                    if len(current) == 1:
                        binding = operation_binding(operations[current[0]])
                    if any(
                        all(member.get(k) == addr.get(k) for k in scope)
                        and member["version"] != addr["version"]
                        for member, ids in members
                    ):
                        reason = "obligation_contract_changed"
                facts = {
                    "question_id": p.get("question_id", ""),
                    "message_key": key,
                    "recipient_digest": pair[1],
                    "recipient": addr,
                }
                claim = (
                    claims.get(key)
                    if last.get("action") in {"claim", "wait"}
                    and addr["mode"] != "coordination"
                    else None
                )
                cause = e.id
                if claim and reason in {"", "execution_ended"}:
                    if is_run_dispatch_paused(canonical, run):
                        continue
                    if last["action"] == "wait" and not _expired(
                        canonical, waits[key], last["deadline"]
                    ):
                        continue
                    consumer = claim.payload["consumer"]
                    if claim.id not in diagnosed and (
                        loss := peer_loss(
                            indexed,
                            claim,
                            operations.get(consumer["operation_id"]),
                            binding,
                        )
                    ):
                        facts.update(
                            status="diagnosis_required",
                            reason="question_taker_stopped_without_answer",
                            previous_status=last["status"],
                            consumer=consumer,
                            recovery_owner="run_manager",
                            source_event_ids=[e.id, claim.id, loss.id],
                        )
                        cause = claim.id
                    elif reason == "execution_ended":
                        # Retain responsibility when the exact claimant's end is unproven or already routed.
                        continue
                if cause != e.id:
                    kind = "disposition"
                elif reason:
                    kind = "disposition"
                    facts.update(
                        status="timed_out" if reason == "message_deadline_elapsed" else "cancelled",
                        reason=reason,
                        previous_status=last.get("status", "pending"),
                    )
                elif binding and bindings.get(pair) != binding:
                    kind = "binding"
                    facts["consumer"] = binding
                else:
                    continue
                token = admission_offset.set(expected)
                try:
                    receipt = _write(runtime, kind, {"workflow_run_id": run}, facts, cause)
                finally:
                    admission_offset.reset(token)
                written += 1
                # One sweep may append several facts. A concurrent WRC edit in
                # between requires a fresh authority fold on the next sweep.
                tail, expected = runtime.event_log.read_from_offset(expected)
                if len(tail) != 1 or tail[0].id != receipt.id:
                    return written
        return written
