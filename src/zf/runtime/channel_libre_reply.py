"""Structured optional questions and host intents in free discussion replies."""
from __future__ import annotations

from zf.runtime.channel_libre import advance_libre, round_for_request
from zf.runtime.channel_contracts import MAX_CHANNEL_PARALLEL_REPLIES
from zf.runtime.channel_contract_artifacts import persist_channel_contract
from zf.runtime.channel_deliberation_contract import reply_question_records
from zf.runtime.channel_member_proposals import prepare_member_proposals, emit_member_proposals
from zf.runtime.channel_question_provenance import question_source_for_request
from zf.runtime.channel_reply_parsing import structured_reply_payload


def emit_libre_reply(*, state_dir, writer, channel, request, reply, reply_event_id, actor, source):
    cid = channel["channel_id"]
    tid = str(request.get("thread_id") or "main")
    root = round_for_request(channel, request)
    rid = str(request.get("request_id") or "")
    member = str(request.get("target_member_id") or "")
    processed = channel.get("libre_rounds", {}).get(root, {}).get("processed_request_ids", [])
    if rid in processed:
        return
    body = structured_reply_payload(reply, "channel_contribution")
    if not body:
        body = structured_reply_payload(reply, "ask_user_question")
    questions, error = reply_question_records(body, channel=channel, channel_id=f"{cid}:{rid}", thread_id=tid,
                                              member_id=member, source_kind="contribution", reuse_by_text=False)
    proposals, proposal_error = prepare_member_proposals(channel, request, body, state_dir=state_dir)
    proposals = [p for p in proposals if not any(
        prior.get("status") == "pending" and prior.get("member_id") == p["member_id"]
        and prior.get("profile_id") == p["profile_id"] for prior in channel.get("member_invitation_proposals", []))]
    if body and not error:
        descriptor = persist_channel_contract(state_dir, channel_id=cid, thread_id=tid, identity=rid,
                                              kind="contribution", body=body, created_by=member,
                                              source_event_id=reply_event_id,
                                              provenance={"source_refs": [f"event:{reply_event_id}"]})
        writer.emit("channel.finding.recorded", actor=member, causation_id=reply_event_id, correlation_id=cid,
                    payload={"channel_id": cid, "thread_id": tid, "member_id": member,
                             "summary": str(body.get("summary") or ""), "request_id": rid,
                             "message_id": request.get("message_id", ""), "source_reply_event_id": reply_event_id,
                             "artifact_ref": descriptor["ref"], "artifact_digest": descriptor["sha256"],
                             "findings": body.get("findings") or [], "contract_status": "structured", "source": source})
        if not proposal_error:
            emit_member_proposals(writer, channel=channel, request=request, proposals=proposals,
                                  descriptor=descriptor, reply_event_id=reply_event_id)
    for q in questions:
        writer.emit("channel.question.opened", actor=member, causation_id=reply_event_id, correlation_id=cid,
                    payload={"channel_id": cid, "thread_id": tid, **q, "libre_round_id": root,
                             "presentation_pending": True, **question_source_for_request(cid, request), "source": source})
    fact_error = apply_fact_resolutions(writer, channel, request, body, reply_event_id, source)
    error = error or fact_error
    relay = body.get("libre_relay") or {}
    row = channel.get("libre_rounds", {}).get(root, {})
    if (member == channel.get("leader_member_id") and row.get("allow_relay") and not row.get("relay")
            and request.get("message_id") == root and isinstance(relay, dict)):
        targets = relay.get("target_member_ids") or []
        prompt = str(relay.get("prompt") or "").strip()
        reason = str(relay.get("reason") or "").strip()
        valid = {m.get("member_id") for m in channel.get("members", [])
                 if m.get("status", "active") not in {"removed", "failed", "rejected", "suspended"}
                 and m.get("member_type") != "observer" and "message" in m.get("permissions", [])}
        if (isinstance(targets, list) and targets and len(targets) <= MAX_CHANNEL_PARALLEL_REPLIES and set(targets) <= set(row.get("targets") or []) and len(set(targets)) == len(targets)
                and set(targets) <= valid and prompt and reason):
            writer.emit("channel.libre.relay.proposed", actor=member, causation_id=reply_event_id, correlation_id=cid,
                        payload={"channel_id": cid, "thread_id": tid, "round_id": root, "targets": targets,
                                 "prompt": prompt, "reason": reason, "request_id": f"relay-{root}", "source": source})
    writer.emit("channel.libre.reply.processed", actor=member, causation_id=reply_event_id, correlation_id=cid,
                payload={"channel_id": cid, "thread_id": tid, "round_id": root, "request_id": rid,
                         "question_error": error, "invitation_error": proposal_error, "source": source})
    advance_libre(state_dir, writer, channel_id=cid, thread_id=tid)


def apply_fact_resolutions(writer, channel, request, body, reply_event_id, source):
    """Only the assigned member may settle a fact it read, with explicit evidence."""
    from zf.runtime.channel_semantic_sources import validate_semantic_source_coverage
    rows = body.get("fact_resolutions") or []
    if not rows:
        return ""
    if not isinstance(rows, list) or len(rows) > 32:
        return "fact_resolutions_must_be_a_bounded_list"
    _, error = validate_semantic_source_coverage(channel, request, body.get("consumed_message_digests"))
    if error:
        return error
    member = request.get("target_member_id")
    thread = request.get("thread_id") or "main"
    pack = next((p for p in channel.get("context_packs", []) if p.get("context_pack_id") == request.get("context_pack_id")), {})
    frozen = {q.get("question_id"): q for q in pack.get("question_ledger", [])}
    current = {q["question_id"]: q for q in channel.get("open_questions", [])}
    accepted, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or row.get("outcome") not in {"resolved", "unresolved", "needs_owner"}:
            return "invalid_fact_resolution_outcome"
        qid = row.get("question_id")
        question = current.get(qid, {})
        if question.get("status") == "resolved":
            continue
        if (qid in seen or question.get("kind") != "fact" or question.get("target_member_id") != member
                or question.get("thread_id") != thread or question.get("status") != "open"):
            return "fact_resolution_scope_or_identity_mismatch"
        seen.add(qid)
        if (not frozen.get(qid) or frozen[qid].get("question_version_ref") != str(question.get("updated_event_id") or question.get("opened_event_id") or "")
                or any(question.get(k) != frozen[qid].get(k) for k in ("kind", "target_member_id", "depends_on", "options"))):
            return "fact_resolution_source_changed_or_not_read"
        if row["outcome"] != "resolved":
            continue
        evidence = row.get("evidence_refs")
        if not str(row.get("answer") or "").strip() or not isinstance(evidence, list) or not evidence or any(not isinstance(v, str) or not v.strip() for v in evidence):
            return "fact_resolution_requires_answer_and_evidence"
        accepted.append(row)
    for row in accepted:
        writer.emit("channel.question.resolved", actor=member, causation_id=reply_event_id,
                    correlation_id=channel["channel_id"], payload={"channel_id":channel["channel_id"],"thread_id":thread,
                        "question_id":row["question_id"],"resolution":"evidence","resolved_by":member,
                        "answer":row["answer"],"evidence_refs":row["evidence_refs"],"source":source})
    return ""
