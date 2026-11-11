"""Apply a complete, conservative merge plan to one unpublished free round."""
from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.channel_question_graph import validate_question_graph
from zf.runtime.channel_libre import round_ledger, stable_id
from zf.runtime.channel_question_dedup import question_ledger_digest
from zf.runtime.channel_projection import project_channel


def apply_libre_dedup(*, state_dir, writer, channel_id, thread_id, request_id, payload, actor, source, causation_id, task_id=None):
    with locked_path(state_dir / "locks" / stable_id("libre", channel_id, thread_id)):
        return _apply_locked(state_dir=state_dir, writer=writer, channel_id=channel_id, thread_id=thread_id,
                             request_id=request_id, payload=payload, actor=actor, source=source, causation_id=causation_id, task_id=task_id)


def _apply_locked(*, state_dir, writer, channel_id, thread_id, request_id, payload, actor, source, causation_id, task_id=None):
    channel = project_channel(state_dir, channel_id) or {}
    request = next((r for r in channel.get("question_dedup_requests", []) if r.get("request_id") == request_id), {})
    root = request.get("libre_round_id")
    row = channel.get("libre_rounds", {}).get(root, {})
    ledger = round_ledger(channel, root)
    digest = question_ledger_digest(ledger, thread_id=thread_id)
    base = {"channel_id": channel_id, "thread_id": thread_id, "request_id": request_id,
            "schema_version": "channel.question.dedup.v1", "source": source}

    def reject(reason):
        writer.emit("channel.question.dedup.rejected", actor=actor, causation_id=causation_id,
                    correlation_id=channel_id, task_id=task_id, payload={**base, "reason": reason})
        return False, reason

    if request.get("status") == "applied":
        return True, "already_applied"
    if not row or row.get("publication") or request.get("status") != "requested":
        return False, "question_batch_already_published_or_inactive"
    if actor != request.get("target_member_id"):
        return reject("dedup_target_mismatch")
    if payload.get("ledger_digest") != digest or request.get("ledger_digest") != digest:
        return reject("stale_ledger_digest")
    questions = {q["question_id"]: q for q in ledger["open_questions"] if q.get("status") == "open"}
    groups = payload.get("groups")
    kept = payload.get("keep_question_ids")
    if not isinstance(groups, list) or not isinstance(kept, list) or any(not isinstance(x, str) for x in kept):
        return reject("groups_and_complete_keep_question_ids_required")
    if len(kept) != len(set(kept)) or not set(kept) <= questions.keys():
        return reject("invalid_keep_question_ids")
    merges = {}
    for group in groups:
        if not isinstance(group, dict):
            return reject("invalid_merge_group")
        canonical = group.get("canonical_question_id")
        ids = group.get("merge_question_ids")
        if canonical not in kept or not isinstance(ids, list) or not isinstance(group.get("reason"), str) or not group["reason"].strip():
            return reject("invalid_merge_canonical_or_reason")
        option_mappings = group.get("option_mappings") or {}
        if not isinstance(option_mappings, dict):
            return reject("invalid_option_mappings")
        for qid in ids:
            if not isinstance(qid, str) or qid not in questions or qid in kept or qid in merges:
                return reject("invalid_or_reused_merge_source")
            if any(questions[qid].get(key) != questions[canonical].get(key) for key in ("kind", "target_member_id")):
                return reject("incompatible_question_kind_or_target")
            # An option mapping is a model's semantic claim, not a string-similarity heuristic.
            options = {o["id"] for o in questions[qid].get("options", [])}
            target_options = {o["id"] for o in questions[canonical].get("options", [])}
            mapping = option_mappings.get(qid, {})
            if options or target_options:
                if (not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()) or set(mapping) != options
                        or set(mapping.values()) != target_options or len(mapping.values()) != len(set(mapping.values()))):
                    return reject("complete_one_to_one_option_mapping_required")
            merges[qid] = (canonical, group["reason"])
    if set(kept) | set(merges) != set(questions):
        return reject("unaccounted_original_questions")
    proposed = []
    for question in channel.get("open_questions", []):
        if question["question_id"] in merges:
            continue
        proposed.append({**question, "depends_on": [merges.get(dep, (dep, ""))[0] for dep in question.get("depends_on", [])]})
    if validate_question_graph(proposed):
        return reject("merge_would_break_dependency_graph")
    plan = [{"question_id": qid, "into_question_id": canonical, "reason": reason}
            for qid, (canonical, reason) in merges.items()]
    event = ZfEvent(type="channel.question.dedup.applied", actor=actor, causation_id=causation_id,
                    correlation_id=channel_id, task_id=task_id, payload={**base,
                        "input_ledger_digest": digest, "group_count": len(groups), "merge_count": len(merges),
                        "keep_question_ids": kept, "libre_merge_plan": plan})
    result_rows = [{**q, **({"status": "merged", "merged_into": merges[q["question_id"]][0],
                           "merged_event_id": event.id} if q["question_id"] in merges else {})}
                   for q in ledger["open_questions"]]
    event.payload["output_ledger_digest"] = question_ledger_digest({**ledger, "open_questions": result_rows}, thread_id=thread_id)
    writer.append(event)
    return True, "applied"
