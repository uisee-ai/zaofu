"""Bounded binary upload edge for Channel attachments."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from zf.runtime.channel_attachments import MAX_ATTACHMENT_BYTES, capture_channel_attachment
from zf.runtime.channel_projection import project_channel


def build_channel_attachment_router(
    *, resolve_ctx: Callable[[str], Any], mutation_auth_error: Callable[..., dict | None],
    session_cookie: Callable[[Request], str | None],
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/projects/{project_id}/channels/{channel_id}/attachments")
    async def upload(
        project_id: str, channel_id: str, request: Request,
        name: str, thread_id: str = "main",
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        error = mutation_auth_error(
            "channel-attachment-upload", authorization=authorization,
            x_zf_web_token=x_zf_web_token, web_session_token=session_cookie(request),
        )
        if error is not None:
            body = dict(error)
            return JSONResponse(body, status_code=int(body.pop("_status_code", 403)))
        ctx = resolve_ctx(project_id)
        if not project_channel(ctx.state_dir, channel_id):
            raise HTTPException(404, "Channel not found")
        if not thread_id or len(thread_id) > 255:
            raise HTTPException(422, "Invalid thread id")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_ATTACHMENT_BYTES:
                raise HTTPException(413, "Attachment exceeds the 10 MiB limit")
        try:
            ref = capture_channel_attachment(
                ctx.state_dir, channel_id=channel_id, thread_id=thread_id,
                name=name, mime=request.headers.get("content-type", "application/octet-stream").split(";")[0].lower(),
                data=bytes(data),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Linking to a message and emitting channel.attachment.uploaded remain
        # the controlled ingress action's responsibility. Unsent uploads are sidecars.
        return JSONResponse({"attachment": ref}, status_code=201)

    return router
