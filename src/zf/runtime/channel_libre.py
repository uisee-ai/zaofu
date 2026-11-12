"""User-led Channel rounds, bounded coordination, and question publication."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.state.locks import locked_path

LIBRE_EVENTS = {
    "channel.libre.round.started", "channel.libre.reply.processed",
    "channel.libre.relay.proposed", "channel.libre.questions.published",
}
TERMINAL = {"completed", "failed", "cancelled", "superseded", "rejected", "timed_out", "stale"}


def is_libre(channel: dict | None) -> bool:
    return (channel or {}).get("discussion", {}).get("mode") == "libre_discuss"


def stable_id(prefix: str, *parts: str) -> str:
    return prefix + "-" + hashlib.sha256(":".join(parts).encode()).hexdigest()[:24]


def round_for_request(channel: dict, request: dict) -> str:
    mid = str(request.get("message_id") or "")
    message = next((m for m in channel.get("messages", []) if m.get("message_id") == mid), {})
    return str((message.get("refs") or {}).get("libre_round_id") or mid)


def fold_libre_event(channel: dict, event, payload: dict) -> None:
    rounds = channel.setdefault("libre_rounds", {})
    root = str(payload.get("round_id") or "")
    if event.type == "channel.libre.round.started":
        rounds.setdefault(root, {**payload, "event_id": event.id, "ts": event.ts,
                                "processed_request_ids": [], "relay": {}, "publication": {}})
    elif root in rounds:
        row = rounds[root]
        if event.type == "channel.libre.reply.processed":
            rid = str(payload.get("request_id") or "")
            if rid not in row["processed_request_ids"]:
                row["processed_request_ids"].append(rid)
                if payload.get("question_error") or payload.get("invitation_error"):
                    row.setdefault("warnings", []).append({"member_id": event.actor,
                        "question_error": payload.get("question_error", ""), "invitation_error": payload.get("invitation_error", "")})
        elif event.type == "channel.libre.relay.proposed":
            row["relay"] = {**payload, "event_id": event.id, "ts": event.ts}
        elif event.type == "channel.libre.questions.published" and not row["publication"]:
            row["publication"] = {**payload, "event_id": event.id, "ts": event.ts}
            for question in channel["open_questions"].values():
                if question.get("libre_round_id") == root:
                    question["presentation_pending"] = False
                    question["dedup_status"] = payload["dedup_status"]


def record_round(writer, channel: dict, message_event, message: dict, targets: list[str]) -> None:
    refs = message.get("refs") or {}
    if not is_libre(channel) or refs.get("libre_control_event_id") or refs.get("synthesis_request_id"):
        return
    root = str(message.get("message_id") or message_event.id)
    if root in channel.get("libre_rounds", {}):
        return
    from zf.runtime.channel_router import detect_channel_mention_tokens, _is_spine_reviewer
    # Project-spine reviewers use their existing report/proposal intent protocol,
    # not channel.agent.reply; they must not stall native chat question publication.
    native_targets = [mid for mid in targets if not any(m.get("member_id") == mid and _is_spine_reviewer(m)
                      for m in channel.get("members", []))]
    writer.emit("channel.libre.round.started", actor="channel-runtime", causation_id=message_event.id,
                correlation_id=channel["channel_id"], payload={
                    "channel_id": channel["channel_id"], "thread_id": message.get("thread_id") or "main",
                    "round_id": root, "targets": native_targets, "leader_member_id": channel.get("leader_member_id", ""),
                    "allow_relay": "all" in detect_channel_mention_tokens(str(message.get("text") or ""),
                                                        explicit_mentions=message.get("mentions") or []),
                    "source": "runtime",
                })


def authorize_control_message(state_dir: Path, writer, channel: dict, event, message: dict) -> list[str] | None:
    refs = message.get("refs") or {}
    control = str(refs.get("libre_control_event_id") or "")
    if not is_libre(channel) or not control:
        return None
    prior = next((e for e in writer.event_log.read_all() if e.id == control), None)
    if (not prior or event.causation_id != control or prior.correlation_id != channel.get("channel_id")
            or message.get("role") != "system" or event.actor not in {"channel-runtime", "orchestrator-reactor"}):
        return []
    p = prior.payload
    if p.get("thread_id", "main") != message.get("thread_id", "main"):
        return []
    if prior.type == "channel.libre.relay.proposed":
        row = channel.get("libre_rounds", {}).get(p.get("round_id"), {})
        if row.get("publication") or not row.get("allow_relay") or prior.actor != channel.get("leader_member_id"):
            return []
        return list(p.get("targets") or [])
    if prior.type == "channel.question.dedup.requested" and p.get("libre_round_id"):
        row = channel.get("libre_rounds", {}).get(p["libre_round_id"], {})
        return [] if row.get("publication") else [p["target_member_id"]]
    if prior.type == "channel.synthesis.repair.requested":
        from zf.runtime.channel_synthesis_lock import synthesis_request_in_flight
        active = synthesis_request_in_flight(channel, p.get("thread_id", "main"))
        return [p["target_member_id"]] if active and active.get("request_id") == p.get("request_id") else []
    return []


def _post_control(state_dir: Path, writer, channel: dict, event, *, text: str, targets: list[str], refs: dict,
                  config=None, project_root=None) -> None:
    from zf.runtime.channel_sidecar import channel_message_event_payload
    from zf.runtime.channel_router import route_channel_message
    mid = f"msg-{event.payload.get('request_id') or event.id}"
    prior = next((e for e in writer.event_log.read_all() if e.type == "channel.message.posted"
                  and e.payload.get("channel_id") == channel["channel_id"] and e.payload.get("message_id") == mid), None)
    if prior is None:
        body = channel_message_event_payload(state_dir, {
            "channel_id": channel["channel_id"], "thread_id": event.payload.get("thread_id", "main"),
            "message_id": mid, "member_id": channel.get("leader_member_id") or "主持人",
            "role": "system", "source": "runtime", "text": text, "mentions": targets,
            "refs": {**refs, "libre_control_event_id": event.id, "display_origin": "host_coordination"},
        }, created_by="channel-runtime", source_event_id=event.id)
        prior = writer.emit("channel.message.posted", actor="channel-runtime", causation_id=event.id,
                            correlation_id=channel["channel_id"], payload=body)
    route_channel_message(state_dir=state_dir, writer=writer, message_event=prior, message_payload=prior.payload,
                          actor="channel-runtime", source="runtime", config=config, project_root=project_root,
                          dispatch_inline=False)


def _requests(channel: dict, root: str) -> list[dict]:
    messages = {m.get("message_id"): m for m in channel.get("messages", [])}
    return [r for r in channel.get("reply_requests", [])
            if (r.get("message_id") == root or (
                (messages.get(r.get("message_id"), {}).get("refs") or {}).get("libre_round_id") == root
                and (messages.get(r.get("message_id"), {}).get("refs") or {}).get("libre_relay")))]


def _question_rows(channel: dict) -> list[dict]:
    rows = channel.get("open_questions") or []
    return list(rows.values()) if isinstance(rows, dict) else list(rows)


def round_ledger(channel: dict, root: str) -> dict:
    return {**channel, "open_questions": [q for q in _question_rows(channel) if q.get("libre_round_id") == root]}


def _newer_human_message(channel: dict, root: str) -> bool:
    found = False
    for message in channel.get("messages", []):
        if message.get("message_id") == root:
            found = True
        elif found and message.get("role") in {"user", "human", "operator"} and not (message.get("refs") or {}).get("synthesis_request_id"):
            return True
    return False


def advance_libre(state_dir: Path, writer, *, channel_id: str, thread_id: str = "main",
                  config=None, project_root=None, now=None) -> list[str]:
    from zf.runtime.channel_projection import project_channel
    from zf.runtime.channel_question_dedup import question_ledger_digest
    emitted = []
    with locked_path(state_dir / "locks" / stable_id("libre", channel_id, thread_id)):
        channel = project_channel(state_dir, channel_id) or {}
        for root, row in channel.get("libre_rounds", {}).items():
            if row.get("thread_id") != thread_id or row.get("publication"):
                continue
            requests = _requests(channel, root)
            initial = [r for r in requests if r.get("message_id") == root]
            rejected_targets = {p.get("target_member_id") for p in channel.get("context_packs", [])
                                if p.get("trigger_message_id") == root and p.get("status") == "rejected"}
            if set(row["targets"]) - {r.get("target_member_id") for r in initial} - rejected_targets:
                continue
            processed = set(row.get("processed_request_ids") or [])
            if any(r.get("status") not in TERMINAL or r.get("status") == "completed" and r.get("request_id") not in processed
                   for r in requests):
                continue
            relay = row.get("relay") or {}
            if relay.get("targets") and row.get("leader_member_id") == channel.get("leader_member_id") and not _newer_human_message(channel, root):
                relay_event = next((e for e in writer.event_log.read_all() if e.id == relay["event_id"]), None)
                if relay_event:
                    if project_root is None:
                        continue  # The reactor owns project/config-aware dispatch.
                    _post_control(state_dir, writer, channel, relay_event, text=f"需要补充核实：{relay['reason']}\n{relay['prompt']}", targets=relay["targets"],
                                  refs={"libre_round_id": root, "libre_relay": True}, config=config, project_root=project_root)
                    relay_mid = f"msg-{relay_event.payload.get('request_id') or relay_event.id}"
                    rejected_relay = {p.get("target_member_id") for p in channel.get("context_packs", [])
                                      if p.get("trigger_message_id") == relay_mid and p.get("status") == "rejected"}
                    if set(relay["targets"]) - rejected_relay - {r.get("target_member_id") for r in requests if r.get("message_id") != root}:
                        continue
            candidates = round_ledger(channel, root)
            questions = [q for q in candidates["open_questions"] if q.get("status") == "open"]
            replied = {r.get("target_member_id") for r in requests if r.get("status") == "completed"}
            need_dedup = len(replied) >= 2 and len(questions) >= 2
            rid = stable_id("libre-dedup", channel_id, root)
            dedup = next((r for r in channel.get("question_dedup_requests", []) if r.get("request_id") == rid), {})
            status = "not_required"
            if need_dedup:
                if not dedup:
                    from zf.runtime.channel_discussion import discussion_synthesizer
                    target = discussion_synthesizer(channel, row["targets"])
                    if target:
                        ev = writer.emit("channel.question.dedup.requested", actor="channel-runtime", correlation_id=channel_id,
                                         payload={"channel_id": channel_id, "thread_id": thread_id, "request_id": rid,
                                                  "target_member_id": target, "ledger_digest": question_ledger_digest(candidates, thread_id=thread_id),
                                                  "libre_round_id": root, "question_count": len(questions), "source": "runtime"})
                        if project_root is not None:
                            _post_control(state_dir, writer, channel, ev,
                                      text="正在整理本轮问题。只合并同一决策的等价提问；相关但不同的问题、场景、选项和独立子问必须保留。拿不准就保留原题。",
                                      targets=[target], refs={"libre_round_id": root, "question_dedup_request_id": rid,
                                                              "question_ledger_digest": ev.payload["ledger_digest"]},
                                      config=config, project_root=project_root)
                        emitted.append(ev.type)
                        continue
                    status = "failed_raw"
                elif dedup.get("status") == "applied":
                    status = "deduplicated"
                else:
                    control_replies = [r for r in channel.get("reply_requests", []) if r.get("message_id") == f"msg-{rid}"]
                    from zf.runtime.channel_adapter import _channel_provider_headless_timeout_s
                    deadline = _channel_provider_headless_timeout_s(config)
                    elapsed = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(dedup["ts"].replace("Z", "+00:00"))).total_seconds()
                    context_failed = any(p.get("trigger_message_id") == f"msg-{rid}" and p.get("status") == "rejected"
                                         for p in channel.get("context_packs", []))
                    if dedup.get("status") == "requested" and elapsed < deadline and not context_failed and not any(r.get("status") in TERMINAL - {"completed"} for r in control_replies):
                        continue
                    status = "failed_raw"
            writer.emit("channel.libre.questions.published", actor="channel-runtime", correlation_id=channel_id,
                        payload={"channel_id": channel_id, "thread_id": thread_id, "round_id": root,
                                 "dedup_status": status, "question_ids": [q["question_id"] for q in questions], "source": "runtime"})
            emitted.append("channel.libre.questions.published")
    return emitted


def adapt_libre_attention(channel: dict, projection: dict, questionnaires: dict) -> None:
    if not is_libre(channel):
        return
    for row in projection.values():
        row.update(can_synthesize=True, can_restart=False, blocking_scope="none", blocks_transition="",
                   kernel_phase="", state="active", reason="user_led_discussion", next_action="chat_or_generate_prd",
                   owner_question_count=len(questionnaires.get(row["thread_id"], [])))


def libre_conversation_metadata(channel: dict, visible_message_ids: set[str]) -> dict:
    """Expose local progress without copying history or diagnostic sidecars into chat."""
    if not is_libre(channel):
        return {}
    requests: dict[str, dict] = {}
    snapshots: dict[str, dict] = {}
    candidates: set[str] = set()
    for row in channel.get("synthesis_requests", []):
        rid = row.get("request_id")
        if row.get("invalid_reply_ref"):
            candidates.add(rid)
        if row.get("prd_input_snapshot"):
            snapshots[rid] = row["prd_input_snapshot"]
        requests[str(row.get("thread_id") or "main")] = {
            **{key: row.get(key) for key in ("thread_id", "request_id", "status", "reason", "target_member_id", "ts", "recovery_required")},
            "prd_input_snapshot": row.get("prd_input_snapshot") or snapshots.get(rid) or {},
            "candidate_available": rid in candidates,
        }
    rounds = {root: {"thread_id": row.get("thread_id"), "publication": row.get("publication") or {}, "warnings": row.get("warnings") or []}
              for root, row in channel.get("libre_rounds", {}).items()
              if not row.get("publication") or root in visible_message_ids}
    return {"libre_rounds": rounds, "synthesis_requests": list(requests.values())}
