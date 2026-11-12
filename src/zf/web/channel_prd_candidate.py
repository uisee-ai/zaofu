"""Read rejected PRD bodies on demand, separately from published artifacts."""
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_parsing import structured_reply_payload
from zf.runtime.channel_reply_stream import CHANNEL_CONTRACT_MARKER
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


def build_channel_prd_candidate_router(*, resolve_ctx: Callable[[str], Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/channels/{channel_id}/prd-candidate")
    def candidate(project_id: str, channel_id: str, request_id: str, thread_id: str = "main") -> dict:
        ctx = resolve_ctx(project_id)
        channel = project_channel(ctx.state_dir, channel_id)
        rows = (channel or {}).get("synthesis_requests", [])
        scoped = [r for r in rows if r.get("request_id") == request_id and r.get("thread_id", "main") == thread_id]
        row = next((r for r in reversed(scoped) if r.get("invalid_reply_ref")), scoped[-1] if scoped else None)
        if not row:
            raise HTTPException(404, "No saved candidate for this generation")
        if not row.get("invalid_reply_ref"):
            return _checkpoint_candidate(ctx.state_dir, channel, request_id, thread_id)
        descriptor = row["invalid_reply_ref"]
        scope = descriptor.get("access_scope") or {}
        if scope.get("channel_id") != channel_id or scope.get("thread_id") != thread_id:
            raise HTTPException(409, "Candidate binding mismatch")
        try:
            raw = hydrate_sidecar_ref(ctx.state_dir, descriptor, purpose="prd-candidate-preview").payload
        except SidecarRefError as exc:
            raise HTTPException(409, f"Candidate unavailable: {exc.code}") from exc
        if not isinstance(raw, str):
            raise HTTPException(409, "Candidate body is not text")
        synthesis = structured_reply_payload(raw, "channel_synthesis")
        markdown = synthesis.get("prd_markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            markdown = raw.split(CHANNEL_CONTRACT_MARKER, 1)[0].strip() or raw
        return {"channel_id": channel_id, "thread_id": thread_id, "request_id": request_id,
                "status": "unvalidated_candidate", "markdown": markdown,
                "reason": row.get("contract_error") or row.get("reason") or ""}

    return router


def _checkpoint_candidate(state_dir, channel: dict, request_id: str, thread_id: str) -> dict:
    from zf.runtime.channel_provider_prompt import channel_context_pack_by_id
    from zf.runtime.channel_synthesis_checkpoints import binding_for, load_parts
    from zf.runtime.channel_prd_file import expand_prd_file
    from zf.runtime.channel_prd_composition import expand_prd_composition

    channel_id = channel["channel_id"]
    message = next((m for m in reversed(channel.get("messages", []))
        if m.get("thread_id", "main") == thread_id and (m.get("refs") or {}).get("synthesis_request_id") == request_id), {})
    request = next((r for r in reversed(channel.get("reply_requests", []))
        if r.get("message_id") == message.get("message_id") and r.get("context_pack_id")), {})
    members = channel.get("members") or []
    if isinstance(members, dict):
        members = members.values()
    member = next((m for m in members if m.get("member_id") == request.get("target_member_id")), {})
    if not message or not request or not member:
        raise HTTPException(404, "No saved draft for this generation")
    try:
        pack = channel_context_pack_by_id(channel, request["context_pack_id"], state_dir=state_dir)
        parts = load_parts(state_dir, binding=binding_for(pack, member, message),
                           request_id=request_id, context_digest=pack["context_pack_sha256"])
        document = parts.get("document")
        if not document:
            raise HTTPException(404, "The document portion has not been saved yet")
        refs = {**(message.get("refs") or {}), **request, "channel_id": channel_id}
        document, error = expand_prd_file(document, request=refs, state_dir=state_dir)
        if not error:
            document, error = expand_prd_composition(document, channel=channel, request=refs, state_dir=state_dir)
        if error:
            raise HTTPException(409, error)
        markdown = document.get("prd_markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            raise HTTPException(409, "The saved document has no complete Markdown body")
    except (SidecarRefError, KeyError, ValueError) as exc:
        raise HTTPException(409, "Saved draft integrity or binding could not be verified") from exc
    return {"channel_id": channel_id, "thread_id": thread_id, "request_id": request_id,
            "status": "checkpoint_draft", "markdown": markdown,
            "reason": "本次已保存的文稿，尚未通过最终完整性校验与独立评估，不能用于创建任务。"}
