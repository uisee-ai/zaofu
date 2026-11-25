"""Accepted messages, retrievable read batches, explicit receipts and answers."""

from __future__ import annotations

from datetime import datetime, timezone

from zf.core.events import ZfEvent
from zf.runtime.artifact_access import require_artifact_access
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.call_result_envelope import canonical_json_sha256 as digest, write_immutable_json_sidecar
from zf.runtime.durable_mailbox_authority import (
    ACTOR,
    PREFIX,
    admission_offset,
    lease_expires,
    can_receive,
    enabled,
    guarded,
    record,
    resolve,
    roster,
    sender_address,
    member_address,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, SidecarRefError
from zf.runtime.durable_mailbox_projection import project_mailbox
from zf.runtime.durable_mailbox_reconcile import reconcile_mailbox

KINDS = {"notice", "question", "guidance", "evidence_notice"}


def _token(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ValueError("invalid mailbox " + name)
    return value


def _write(runtime, kind, actor, body, cause=None):
    expires = lease_expires.get()
    if expires and datetime.fromisoformat(expires) <= datetime.now(timezone.utc):
        raise ValueError("mailbox consumer lease expired before admission")
    event = ZfEvent(
        type=PREFIX + kind,
        actor=ACTOR,
        origin="kernel",
        task_id=actor.get("task_id") or None,
        correlation_id=actor["workflow_run_id"],
        causation_id=cause or actor.get("authority_event_id"),
        payload={
            "schema_version": "durable-mailbox." + kind + ".v1",
            "workflow_run_id": actor["workflow_run_id"],
            **body,
        },
    )
    offset = admission_offset.get()
    receipt = runtime.event_writer.append(
        event, expected_offset=offset, allow_intervening=frozenset({"worker.heartbeat", "agent.text"})
    )
    if receipt.type != event.type:
        raise ValueError("mailbox ledger event failed schema admission")
    if offset is not None:
        more, head = runtime.event_log.read_from_offset(offset)
        if any(row.id == receipt.id for row in more) and all(
            row.id == receipt.id or row.type in {"worker.heartbeat", "agent.text"} for row in more
        ):
            admission_offset.set(head)
    return receipt


def _body(runtime, data):
    ref = write_immutable_json_sidecar(
        runtime.state_dir,
        data,
        root="durable-mailbox",
        kind="durable_mailbox_body",
        schema_version="durable-mailbox.body.v1",
        created_by=ACTOR,
    )
    # Generic artifact readers must enforce this ref scope. Only mailbox
    # delivery/answer paths verify the per-recipient consumer binding.
    ref["access_scope"] = {"visibility": "restricted"}
    return ref


def _messages(events, run):
    return {
        e.payload["message_key"]: e
        for e in events
        if record(e, "accepted") and e.payload["workflow_run_id"] == run
    }


def _source_manifest(runtime, actor):
    if not actor.get("attempt_id"):
        raise ValueError("mailbox attachments require a current attempt input manifest")
    from zf.runtime.artifact_read_capability import (
        _binding_path,
        _read_json,
        _require_not_expired,
        canonical_source_manifest_digest,
    )

    manifest = load_attempt_source_manifest(runtime.state_dir, actor["attempt_id"])
    binding = _read_json(_binding_path(runtime.state_dir, actor["attempt_id"]))
    if (
        not binding
        or binding.get("manifest_digest") != canonical_source_manifest_digest(manifest)
        or binding.get("role_instance") != actor["actor"]
        or binding.get("operation_id") != actor["operation_id"]
        or binding.get("attempt_id") != actor["attempt_id"]
    ):
        raise ValueError("mailbox attachment lacks a current authorized source manifest")
    _require_not_expired(binding)
    if manifest.get("workflow_run_id") != actor["workflow_run_id"]:
        raise ValueError("attachment manifest belongs to a different Run")
    return manifest


def _role(runtime, actor):
    role = next((r for r in getattr(runtime.config, "roles", ()) if r.instance_id == actor), None)
    return role.name if role else actor


def _attachments(runtime, actor, requested, recipients):
    if not requested:
        return []
    if not isinstance(requested, (list, tuple)) or not all(isinstance(s, str) for s in requested):
        raise ValueError("mailbox attachments must be source IDs from the current attempt index")
    manifest = _source_manifest(runtime, actor)
    result = []
    for source_id in sorted(set(requested)):
        matches = [s for s in manifest.get("sources", []) if s.get("source_id") == source_id]
        if len(matches) != 1:
            raise ValueError("mailbox attachment is outside the authorized index")
        s = matches[0]
        if not s.get("ref") or not s.get("sha256"):
            raise ValueError("mailbox attachment lacks immutable source binding")
        for who in [actor["actor"], *(r.get("role") or r.get("actor") for r in recipients)]:
            require_artifact_access(
                s.get("access_scope"),
                actor=who,
                role=_role(runtime, who),
                purpose=manifest.get("read_purpose", ""),
            )
        result.append(
            {
                k: s[k]
                for k in (
                    "source_id",
                    "artifact_id",
                    "occurrence_id",
                    "ref",
                    "sha256",
                    "allowed_paths",
                    "access_scope",
                )
                if k in s
            }
        )
    return result


def post(
    runtime,
    identity,
    *,
    request_id,
    message_id,
    recipients,
    kind,
    text,
    attachments=(),
    question_id="",
    deadline="",
    visibility="restricted",
):
    _token(request_id, "request ID")
    _token(message_id, "message ID")
    if kind not in KINDS or visibility not in {"restricted", "operator_metadata"}:
        raise ValueError("unsupported mailbox kind or visibility")
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 16384:
        raise ValueError("invalid mailbox body")
    if not isinstance(recipients, (list, tuple)) or not recipients or len(recipients) > 100:
        raise ValueError("mailbox requires 1..100 recipients")
    if not isinstance(attachments, (list, tuple)) or not all(isinstance(s, str) and s for s in attachments):
        raise ValueError("mailbox attachments require source IDs")
    if kind == "question" and len(recipients) != 1:
        raise ValueError("a question requires one accountable recipient")
    if question_id:
        _token(question_id, "question ID")
    if deadline:
        if not isinstance(deadline, str) or datetime.fromisoformat(deadline).tzinfo is None:
            raise ValueError("mailbox deadline requires a timestamp with timezone")
    with guarded(runtime, identity) as (events, ops, actor):
        run = actor["workflow_run_id"]
        members = roster(runtime, events, ops, run)
        sender = sender_address(actor, members)
        # Caller keys are scoped to the logical sender; its execution fence is
        # checked independently on every call, including replay after a lost response.
        logical = (
            sender
            if sender["mode"] == "obligation"
            else {
                k: sender.get(k) for k in ("actor", "operation_id", "request_hash", "assistance_request_id")
            }
        )
        sender_key = digest([run, logical])
        key = "mail-" + digest([sender_key, message_id])
        requested = sorted(recipients, key=digest)

        addrs = [resolve(runtime, r, ops, run, members) for r in requested]
        refs = _attachments(runtime, actor, attachments, addrs)
        semantic = digest([message_id, requested, addrs, kind, text, refs, question_id, deadline, visibility])
        for e in _messages(events, run).values():
            p = e.payload
            if p["sender_key"] == sender_key and (
                p["request_id"] == request_id or p["message_id"] == message_id
            ):
                if (p["request_id"], p["message_id"], p["request_digest"]) != (
                    request_id,
                    message_id,
                    semantic,
                ):
                    raise ValueError("mailbox idempotency conflict")
                return _receipt(e)
        if not enabled(runtime.config):
            raise ValueError("durable mailbox is not accepting new messages")

        if len(set(map(digest, addrs))) != len(addrs):
            raise ValueError("duplicate resolved mailbox recipient")
        if question_id and kind == "question":
            request = next(
                (
                    e
                    for e in events
                    if e.type == "worker.assistance.requested"
                    and e.origin == "kernel"
                    and e.actor == actor["actor"]
                    and e.payload.get("request_id") == question_id
                    and e.payload.get("workflow_run_id") == run
                    and e.payload.get("operation_id") == actor.get("operation_id")
                    and e.payload.get("active_attempt_id") == actor.get("attempt_id")
                    and e.payload.get("durable_mailbox")
                ),
                None,
            )
            if not request or [a.get("role") for a in addrs] != [request.payload.get("owner_instance")]:
                raise ValueError("mailbox question does not bind the canonical assistance request")
        if actor.get("assistance_request_id"):
            raise ValueError("use the answer contract for an assistance response")
        ref = _body(runtime, {"text": text, "attachments": refs})
        ev = _write(
            runtime,
            "accepted",
            actor,
            {
                "message_key": key,
                "message_id": message_id,
                "request_id": request_id,
                "request_digest": semantic,
                "sender_key": sender_key,
                "sender": actor,
                "sender_address": sender,
                "recipients": addrs,
                "unbound_recipients": [
                    a["version"]
                    for a in addrs
                    if a["mode"] == "obligation" and not any(a == member and ids for member, ids in members)
                ],
                "kind": kind,
                "question_id": question_id,
                "deadline": deadline,
                "visibility": visibility,
                "body_ref": ref,
            },
        )
        return _receipt(ev)


def _receipt(event):
    return {
        "message_key": event.payload["message_key"],
        "message_id": event.payload["message_id"],
        "event_id": event.id,
        "status": "accepted",
        "question_id": event.payload.get("question_id", ""),
    }


def _select(messages, identifier):
    if identifier in messages:
        return messages[identifier]
    found = [e for e in messages.values() if e.payload["message_id"] == identifier]
    if len(found) != 1:
        raise ValueError("unknown or ambiguous mailbox message")
    return found[0]


def _recipient(message, actor, members, questions):
    latest = questions.get(message["message_key"], {})
    if latest.get("action") == "cancel":
        return None
    addresses = [latest["target"]] if latest.get("target") else message["recipients"]
    found = [
        a
        for a in addresses
        if digest(a) not in latest.get("revoked_recipients", ()) and can_receive(a, actor, members, message)
    ]
    if len(found) > 1:
        raise ValueError("mailbox delivery has ambiguous recipient for this consumer")
    return found[0] if found else None


def _deliverable(runtime, event, addr, actor, events):
    p = event.payload
    try:
        body = hydrate_sidecar_ref(runtime.state_dir, p["body_ref"]).payload
    except SidecarRefError as error:
        last = next(
            (
                e
                for e in reversed(events)
                if record(e, "disposition")
                and e.payload["message_key"] == p["message_key"]
                and e.payload.get("recipient_digest") == digest(addr)
            ),
            None,
        )
        if (
            not last
            or last.payload.get("status") != "evidence_unavailable"
            or last.payload.get("reason") != error.code
        ):
            _write(
                runtime,
                "disposition",
                actor,
                {
                    "message_key": p["message_key"],
                    "recipient": addr,
                    "recipient_digest": digest(addr),
                    "status": "evidence_unavailable",
                    "reason": error.code,
                    "recovery_owner": "run_manager",
                },
                event.id,
            )
        raise
    latest = next(
        (
            e
            for e in reversed(events)
            if record(e, "disposition")
            and e.payload["message_key"] == p["message_key"]
            and e.payload.get("recipient_digest") == digest(addr)
        ),
        None,
    )
    if latest and latest.payload.get("status") == "evidence_unavailable":
        _write(
            runtime,
            "disposition",
            actor,
            {
                "message_key": p["message_key"],
                "recipient": addr,
                "recipient_digest": digest(addr),
                "status": "evidence_restored",
                "reason": "body_digest_verified",
            },
            event.id,
        )
    attachments = []
    for descriptor in body.get("attachments", []):
        manifest = _source_manifest(runtime, actor)
        permitted = next(
            (
                s
                for s in manifest.get("sources", [])
                if s.get("ref") == descriptor["ref"]
                and all(
                    s.get(k) == descriptor.get(k)
                    for k in ("sha256", "allowed_paths", "artifact_id", "occurrence_id")
                )
            ),
            None,
        )
        if not permitted:
            raise ValueError("mailbox attachment lacks current receiver input authority")
        for source in (descriptor, permitted):
            require_artifact_access(
                source.get("access_scope"),
                actor=actor["actor"],
                role=_role(runtime, actor["actor"]),
                purpose=manifest.get("read_purpose", ""),
            )
        attachments.append({"source_id": permitted["source_id"], "sha256": permitted["sha256"]})
    return {
        "message_key": p["message_key"],
        "message_id": p["message_id"],
        "kind": p["kind"],
        "question_id": p["question_id"],
        "text": body["text"],
        "body_digest": p["body_ref"]["sha256"],
        "attachments": attachments,
        "recipient": addr,
    }


def _consumed(events, actor):
    return {
        (row["message_key"], digest(row["recipient"]))
        for e in events
        if record(e, "acknowledged") and e.payload.get("consumer") == actor
        for row in e.payload["items"]
    }


def pending_for_consumer(runtime, identity, *, limit=20):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("mailbox inbox limit must be 1..100")
    with guarded(runtime, identity) as (events, ops, actor):
        run = actor["workflow_run_id"]
        members = roster(runtime, events, ops, run)
        questions = _question_states(events)
        consumed = _consumed(events, actor)
        items = []
        count = 0
        for key, event in _messages(events, run).items():
            p = event.payload
            addr = _recipient(p, actor, members, questions)
            if (
                addr
                and (key, digest(addr)) not in consumed
                and questions.get(key, {}).get("action") != "answer"
                and not _expired(events, event, p.get("deadline"))
            ):
                count += 1
                if len(items) < limit:
                    items.append(
                        {
                            "message_key": key,
                            "message_id": p["message_id"],
                            "kind": p["kind"],
                            "question_id": p.get("question_id", ""),
                            "status": "pending",
                            "deadline": p["deadline"],
                            "recipient": addr,
                        }
                    )
        return {"items": items, "total": count}


def prepare(runtime, identity, *, request_id, message_ids=None, limit=20):
    _token(request_id, "read request ID")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("mailbox read limit must be 1..100")
    if message_ids is not None and (
        not isinstance(message_ids, (list, tuple))
        or len(message_ids) > 100
        or not all(isinstance(x, str) and x for x in message_ids)
    ):
        raise ValueError("invalid mailbox message selection")
    with guarded(runtime, identity) as (events, ops, actor):
        run = actor["workflow_run_id"]
        members = roster(runtime, events, ops, run)
        questions = _question_states(events)
        messages = _messages(events, run)
        delivery = "delivery-" + digest([actor, request_id])
        prior = next(
            (e for e in events if record(e, "prepared") and e.payload.get("delivery_id") == delivery), None
        )
        selection = digest([message_ids, limit])
        acked = _consumed(events, actor)
        if prior:
            if prior.payload["selection_digest"] != selection:
                raise ValueError("mailbox read idempotency conflict")
            selected = [
                (_select(messages, row["message_key"]), row["recipient"]) for row in prior.payload["items"]
            ]
        else:
            wanted = (
                [_select(messages, k) for k in dict.fromkeys(message_ids)]
                if message_ids is not None
                else list(messages.values())
            )
            selected = []
            for ev in wanted:
                addr = _recipient(ev.payload, actor, members, questions)
                if addr is None or _expired(events, ev, ev.payload.get("deadline")):
                    if message_ids is not None:
                        raise ValueError("mailbox access denied")
                    continue
                if (
                    questions.get(ev.payload["message_key"], {}).get("action") != "answer"
                    and (ev.payload["message_key"], digest(addr)) not in acked
                ):
                    selected.append((ev, addr))
                if len(selected) == limit:
                    break
        result = []
        rows = []
        for ev, addr in selected:
            if _recipient(ev.payload, actor, members, questions) != addr or _expired(
                events, ev, ev.payload.get("deadline")
            ):
                raise ValueError("mailbox recipient authority changed")
            item = _deliverable(runtime, ev, addr, actor, events)
            result.append(item)
            rows.append({k: item[k] for k in ("message_key", "recipient", "body_digest")})
        if not prior:
            prior = _write(
                runtime,
                "prepared",
                actor,
                {
                    "delivery_id": delivery,
                    "request_id": request_id,
                    "consumer": actor,
                    "selection_digest": selection,
                    "items": rows,
                },
            )
        ack = next(
            (e for e in events if record(e, "acknowledged") and e.payload.get("delivery_id") == delivery),
            None,
        )
        return {
            "delivery_id": delivery,
            "event_id": prior.id,
            "messages": result,
            "items": result,
            "status": "prepared",
            "acknowledgement": _ack_receipt(ack) if ack else None,
        }


def _ack_receipt(event):
    return {"delivery_id": event.payload["delivery_id"], "event_id": event.id, "status": "acknowledged"}


def acknowledge(runtime, identity, *, delivery_id):
    _token(delivery_id, "delivery ID")
    with guarded(runtime, identity) as (events, ops, actor):
        prior = next(
            (e for e in events if record(e, "prepared") and e.payload.get("delivery_id") == delivery_id), None
        )
        if not prior or prior.payload["consumer"] != actor:
            raise ValueError("mailbox delivery is not bound to this consumer")
        members = roster(runtime, events, ops, actor["workflow_run_id"])
        questions = _question_states(events)
        messages = _messages(events, actor["workflow_run_id"])
        for row in prior.payload["items"]:
            msg = _select(messages, row["message_key"])
            if _recipient(msg.payload, actor, members, questions) != row["recipient"] or _expired(
                events, msg, msg.payload.get("deadline")
            ):
                raise ValueError("mailbox recipient authority changed")
            _deliverable(runtime, msg, row["recipient"], actor, events)
        signed = next(
            (e for e in events if record(e, "acknowledged") and e.payload.get("delivery_id") == delivery_id),
            None,
        )
        if not signed:
            already = _consumed(events, actor)
            new_items = [
                r for r in prior.payload["items"] if (r["message_key"], digest(r["recipient"])) not in already
            ]
            signed = _write(
                runtime,
                "acknowledged",
                actor,
                {
                    "delivery_id": delivery_id,
                    "consumer": actor,
                    "items": prior.payload["items"],
                    "newly_consumed": new_items,
                },
                prior.id,
            )
        return _ack_receipt(signed)


def _question_states(events):
    latest = {}
    for e in events:
        if record(e, "question"):
            latest[e.payload["message_key"]] = dict(e.payload)
        elif record(e, "disposition") and e.payload.get("status") in {"cancelled", "timed_out"}:
            state = latest.setdefault(e.payload["message_key"], {})
            state.setdefault("revoked_recipients", set()).add(e.payload["recipient_digest"])
    return latest


def _valid_text(text):
    return isinstance(text, str) and 0 < len(text.strip()) and len(text.encode()) <= 16384


def _expired(events, event, deadline):
    from zf.runtime.pause_lifecycle import active_request_age_seconds

    if not deadline:
        return False
    start = datetime.fromisoformat(event.ts)
    end = datetime.fromisoformat(deadline)
    age = active_request_age_seconds(
        events, created_at=start, now=datetime.now(timezone.utc), run_id=event.payload["workflow_run_id"]
    )
    return age >= (end - start).total_seconds()


def _sender_authorized(message, actor, members):
    addr = message["sender_address"]
    if can_receive(addr, actor, members, message):
        return True
    # A redriven execution can retrieve the response to its own original request
    # iff WRC retained the same operation and immutable input fingerprint.
    return (
        addr["mode"] == "execution"
        and not actor.get("assistance_request_id")
        and all(
            addr.get(k) and addr[k] == actor.get(k)
            for k in ("workflow_run_id", "operation_id", "request_hash", "actor")
        )
    )


def question_action(
    runtime, identity, *, message_id, action, request_id, text="", delivery_id="", target=None, deadline=""
):
    from zf.runtime.assistance_mailbox import question_deadline_expired

    _token(request_id, "question action ID")
    if action not in {"claim", "answer", "wait", "handoff", "cancel"}:
        raise ValueError("unsupported mailbox question action")
    if text and not _valid_text(text):
        raise ValueError("invalid mailbox question response")
    if action == "answer" and not _valid_text(text):
        raise ValueError("an answer requires guidance")
    if (action == "handoff") != (target is not None):
        raise ValueError("handoff requires an explicit authorized successor")
    if deadline and (action != "wait" or datetime.fromisoformat(deadline).tzinfo is None):
        raise ValueError("wait deadline requires a timezone")
    with guarded(runtime, identity) as (events, ops, actor):
        run = actor["workflow_run_id"]
        msg = _select(_messages(events, run), message_id)
        p = msg.payload
        if p["kind"] != "question":
            raise ValueError("message is not a question")
        members = roster(runtime, events, ops, run)
        questions = _question_states(events)
        previous = questions.get(p["message_key"], {})
        addr = _recipient(p, actor, members, questions)
        sender = _sender_authorized(p, actor, members)
        if previous.get("revoked_recipients") and not (action == "handoff" and sender):
            raise ValueError("mailbox recipient authority revoked")
        if not addr and not (sender and action in {"cancel", "handoff"}):
            raise ValueError("mailbox question access denied")
        request_digest = digest([p["message_key"], action, delivery_id, text, target, deadline])
        # Every action replay checks access and current consumer before returning.
        replay = next(
            (
                e
                for e in events
                if record(e, "question")
                and e.payload["consumer"] == actor
                and e.payload["request_id"] == request_id
            ),
            None,
        )
        if replay:
            if replay.payload["request_digest"] != request_digest:
                raise ValueError("question action idempotency conflict")
            return _question_receipt(replay)
        if previous.get("action") in {"answer", "cancel"}:
            raise ValueError("mailbox question is already closed")
        if action != "cancel" and (
            _expired(events, msg, p.get("deadline"))
            or p.get("question_id")
            and question_deadline_expired(events, p["question_id"])
        ):
            raise ValueError("mailbox question deadline has elapsed")
        if not (sender and action in {"cancel", "handoff"}):
            receipt = next(
                (
                    e
                    for e in events
                    if record(e, "acknowledged")
                    and e.payload["delivery_id"] == delivery_id
                    and e.payload["consumer"] == actor
                    and any(
                        r["message_key"] == p["message_key"]
                        and r["recipient"] == addr
                        and r["body_digest"] == p["body_ref"]["sha256"]
                        for r in e.payload["items"]
                    )
                ),
                None,
            )
            if not receipt:
                raise ValueError("question action requires an acknowledged body delivery")
            _deliverable(runtime, msg, addr, actor, events)
        if action in {"answer", "wait"} and not (
            previous.get("action") in {"claim", "wait"} and previous.get("consumer") == actor
        ):
            raise ValueError("claim the current question before answering or waiting")
        if (
            action == "claim"
            and previous.get("action") in {"claim", "wait"}
            and previous["consumer"] == actor
        ):
            raise ValueError("this consumer already claimed the question")
        new_target = previous.get("target")
        if action == "handoff":
            if p.get("question_id"):
                raise ValueError("assistance ownership must be transferred through its recovery authority")
            new_target = resolve(runtime, target, ops, run, members)
            old_target = previous.get("target") or p["recipients"][0]
            if old_target.get("mode") == "obligation" and old_target not in [a for a, _ in members]:
                if (
                    "orchestrator" not in actor.get("coordination_owners", ())
                    or not _valid_text(text)
                    or not new_target.get("version")
                    or target.get("version") != new_target["version"]
                ):
                    raise ValueError(
                        "changed contract requires an explicit OA applicability decision on the new version"
                    )
            if not sender and new_target != p["sender_address"]:
                raise ValueError("only the sender can authorize a new recipient for this body")
        until = deadline or p.get("deadline", "")
        if action == "wait" and (
            not _valid_text(text)
            or not until
            or _expired(events, msg, until)
            or p.get("deadline")
            and datetime.fromisoformat(until) > datetime.fromisoformat(p["deadline"])
        ):
            raise ValueError("waiting requires a reason and an authorized future deadline")
        statuses = {
            "claim": "claimed",
            "answer": "answered",
            "wait": "waiting",
            "handoff": "handed_off",
            "cancel": "cancelled",
        }
        fact = {
            "message_key": p["message_key"],
            "question_id": p["question_id"] or p["message_key"],
            "request_id": request_id,
            "request_digest": request_digest,
            "consumer": actor,
            "recipient": addr,
            "action": action,
            "status": statuses[action],
            "delivery_id": delivery_id,
            "deadline": until,
        }
        if new_target:
            fact["target"] = new_target
        if text:
            fact["answer_ref" if action == "answer" else "note_ref"] = _body(runtime, {"text": text})
        return _question_receipt(_write(runtime, "question", actor, fact, msg.id))


def _question_receipt(event):
    return {
        "event_id": event.id,
        "message_key": event.payload["message_key"],
        "status": event.payload["status"],
        "new_writer_authorized": False,
    }


def read_answer(runtime, identity, *, message_id):
    with guarded(runtime, identity, mutation=False) as (events, ops, actor):
        run = actor["workflow_run_id"]
        msg = _select(_messages(events, run), message_id)
        if msg.payload["kind"] != "question" or not _sender_authorized(
            msg.payload, actor, roster(runtime, events, ops, run)
        ):
            raise ValueError("answer is outside the authorized sender scope")
        state = _question_states(events).get(msg.payload["message_key"], {})
        if state.get("action") != "answer":
            return {
                "message_key": msg.payload["message_key"],
                "status": state.get("status", "pending"),
                "new_writer_authorized": False,
            }
        answer = hydrate_sidecar_ref(runtime.state_dir, state["answer_ref"]).payload
        return {
            "message_key": msg.payload["message_key"],
            "text": answer["text"],
            "digest": state["answer_ref"]["sha256"],
            "status": "answered",
            "new_writer_authorized": False,
        }


__all__ = [
    "acknowledge",
    "enabled",
    "member_address",
    "post",
    "prepare",
    "pending_for_consumer",
    "project_mailbox",
    "question_action",
    "read_answer",
    "reconcile_mailbox",
]
