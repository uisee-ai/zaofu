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
        row = next((r for r in reversed(rows) if r.get("request_id") == request_id
                    and r.get("thread_id", "main") == thread_id and r.get("invalid_reply_ref")), None)
        if not row:
            raise HTTPException(404, "No saved candidate for this generation")
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
