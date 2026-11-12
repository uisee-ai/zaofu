"""Generate bounded PRD portions, preserving verified completed work on failure."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from zf.runtime.channel_provider_completion import _merge_usage, _normalize_public_items
from zf.runtime.channel_provider_prompt import channel_context_pack_by_id
from zf.runtime.channel_reply_parsing import structured_reply_payload_with_error
from zf.runtime.channel_reply_stream import CHANNEL_CONTRACT_MARKER
from zf.runtime.channel_synthesis_checkpoints import binding_for, fingerprint, load_parts, request_lineage, save_part
from zf.runtime.channel_synthesis_parts import PARTS, part_error, part_prompt
from zf.core.events import EventLog


def run_checkpointed_synthesis(
    *, run_turn: Callable[[str, str], Any], initial_prompt: str, provider_session_id: str,
    channel: dict, member: dict, request: dict, message: dict, state_dir: Path,
    on_progress: Callable[[dict], None], is_active: Callable[[], bool],
) -> Any:
    from zf.web.headless_agent import HeadlessTurnResult

    refs = message.get("refs") or {}
    synthesis_id = str(refs.get("synthesis_request_id") or "")
    channel_id, thread_id = str(channel.get("channel_id") or ""), str(request.get("thread_id") or "main")
    lineage = request_lineage(EventLog(state_dir / "events.jsonl").read_all(), channel_id=channel_id,
                              thread_id=thread_id, request_id=synthesis_id)
    if synthesis_id not in lineage:
        raise ValueError("Checkpointed synthesis requires its durable frozen-input request")
    pack = channel_context_pack_by_id(channel, str(request.get("context_pack_id") or ""), state_dir=state_dir)
    if pack.get("semantic_source_complete") is not True or not pack.get("context_pack_sha256"):
        raise ValueError("Checkpointed synthesis requires the bound complete context")
    binding = binding_for(pack, member, message)
    context_digest = str(pack["context_pack_sha256"])
    saved = load_parts(state_dir, binding=binding, request_id=synthesis_id, context_digest=context_digest)
    session_id = provider_session_id
    result = None
    usage: dict = {}
    messages = []
    turns = 0

    def stopped():
        baseline = result or HeadlessTurnResult(ok=False, status="cancelled", backend=str(member.get("backend") or ""),
            thread_id=thread_id, provider_session_id=session_id, reply="", messages=[], usage={})
        return replace(baseline, ok=False, status="cancelled", reply="", usage=usage, messages=messages,
                       error="本次生成已停止；已保存部分和旧版 PRD 保留")

    def progress(index: int, state: str, *, checkpoint: dict | None = None, retry: bool = False, restored: bool = False, reason: str = "") -> None:
        spec = PARTS[index]
        on_progress({"kind": "synthesis", "id": f"synthesis-{synthesis_id}-{spec.name}",
                     "synthesis_request_id": synthesis_id, "input_fingerprint": fingerprint(binding),
                     "index": index, "total": len(PARTS), "completed": len(saved),
                     "title": spec.title, "state": state, "retry": retry, "restored": restored,
                     "reason": reason, **({"checkpoint": checkpoint} if checkpoint else {})})

    for index, spec in enumerate(PARTS):
        if spec.name in saved:
            progress(index, "completed", restored=True)
    for index, spec in enumerate(PARTS):
        if not is_active():
            return stopped()
        if spec.name in saved:
            continue
        error = ""
        for attempt in range(2):
            progress(index, "started", retry=bool(attempt), reason=error)
            prompt = part_prompt(spec, initial_prompt=initial_prompt, saved=saved, first_turn=not turns,
                                 context_id=str(pack["context_pack_id"]), context_digest=context_digest, error=error)
            turns += 1
            result = _normalize_public_items(run_turn(prompt, session_id))
            usage = _merge_usage(usage, getattr(result, "usage", {}))
            messages.extend(getattr(result, "messages", []) or [])
            session_id = str(getattr(result, "provider_session_id", "") or session_id)
            if not getattr(result, "ok", False):
                progress(index, "failed", reason=str(getattr(result, "error", "") or "Provider did not complete this part"))
                # A missing completion receipt is never an implicit retry grant.
                return replace(result, usage=usage, messages=messages)
            part, error = structured_reply_payload_with_error(str(result.reply or ""), "channel_synthesis_part")
            if not error and part.get("part") != spec.name:
                error = "Provider returned a different synthesis part"
            if not error:
                error = part_error(spec, part.get("fields"))
            if not error:
                if not is_active():
                    return stopped()
                fields = part["fields"]
                descriptor = save_part(state_dir, spec=spec, fields=fields, binding=binding,
                                       request_id=synthesis_id, context_digest=context_digest)
                saved[spec.name] = fields
                progress(index, "completed", checkpoint=descriptor)
                break
        else:
            progress(index, "failed", reason=error)
            return replace(result, ok=False, status="failed", error=f"{spec.title}：{error}", usage=usage, messages=messages)

    assembled = {key: value for spec in PARTS for key, value in saved[spec.name].items()}
    public = str(assembled.get("summary") or "") + "\n\n文稿、需求验收映射、已确认决定及开发交接已整理，正在提交完整性校验。"
    reply = public + "\n" + CHANNEL_CONTRACT_MARKER + "\n" + json.dumps({"channel_synthesis": assembled}, ensure_ascii=False)
    if result is None:
        result = HeadlessTurnResult(ok=True, status="completed", backend=str(member.get("backend") or "codex"),
                                    thread_id=thread_id, provider_session_id=session_id, reply=reply,
                                    messages=[], usage={}, resumed=bool(session_id))
    return replace(result, ok=True, status="completed", reply=reply, usage=usage, messages=messages,
                   incomplete=False, completion_reason="", error="")
