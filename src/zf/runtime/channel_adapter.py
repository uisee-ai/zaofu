"""Provider dispatch helpers for Agent Channel reply requests."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.state.session import SessionStore, ZfNotInitialized
from zf.runtime.channel_dispatch_claim import (
    ChannelDispatchResult,
    with_channel_reply_dispatch_claim,
)
from zf.runtime.channel_dispatch_capacity import (
    channel_dispatch_capacity,
    channel_dispatch_lock,
    dispatch_candidate_waves,
)
from zf.runtime.channel_openclaw import dispatch_openclaw_channel_reply
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.runtime.channel_provider_completion import (
    build_fake_channel_reply,
    run_channel_provider_with_continuations,
)
from zf.runtime.channel_provider_prompt import (
    build_channel_provider_prompt as _build_channel_prompt,
    channel_context_pack_by_id,
)
from zf.runtime.channel_reply_remediation import emit_channel_reply_failed
from zf.runtime.channel_reply_stream import ChannelVisibleMessageEmitter
from zf.runtime.channel_contracts import (
    active_channel_resolved_skill_refs,
    active_channel_skill_refs,
    normalize_permission_profile,
    permission_profile_write_policy,
)
from zf.runtime.channel_provider_profile import (
    channel_profile_permission_fields,
    reject_inactive_channel_member,
)
from zf.runtime.channel_run_owner import (
    active_reply_for_target,
    provider_run_fields_for_request,
)
from zf.runtime.channel_reply_completion import (
    complete_deterministic_reply as _complete_deterministic_reply,
    origin_external_refs as _origin_external_refs,
)
from zf.runtime.channel_sidecar import (
    channel_message_event_payload,
    hydrate_channel_message_text,
)
from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter
from zf.runtime.openclaw_provider import OpenClawGatewayClient
from zf.runtime.provider_permissions import (
    build_provider_permission_snapshot,
    emit_provider_permission_snapshot,
    provider_permission_drift,
    snapshot_with_provider_session,
)


FAKE_BACKENDS = {"fake", "mock", "deterministic"}
REMOTE_BACKENDS = {"codex", "claude-code", "hermes", "openclaw"}
HEADLESS_BACKENDS = {
    "codex": "codex-headless",
    "codex-headless": "codex-headless",
    "codex-app-server": "codex-headless",
    "codex_headless": "codex-headless",
    "claude": "claude-headless",
    "claude-code": "claude-headless",
    "claude-headless": "claude-headless",
    "claude-code-headless": "claude-headless",
    "claude_headless": "claude-headless",
}
DISPATCHABLE_STATUSES = {"pending", "queued"}
DEFAULT_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S = 30 * 60.0

# These replies are self-contained phase operations.  Their context packs carry
# the complete bounded evidence set, so resuming a member's open-ended
# discussion session only replays stale tool history and can exhaust provider
# context before the phase can publish its durable artifact.
_PHASE_SESSION_REF_KEYS = (
    "question_dedup_request_id",
    "synthesis_request_id",
    "cross_review_request_id",
    "consensus_review_id",
)


@with_channel_reply_dispatch_claim
def dispatch_reply_request(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel_id: str,
    request_id: str,
    actor: str,
    source: str,
    allow_queued: bool = False,
    project_root: Path | None = None,
    headless_backends: dict[str, Any] | None = None,
    config: ZfConfig | None = None,
    openclaw_client: OpenClawGatewayClient | None = None,
    deterministic_reply: str | None = None,
    deterministic_reason: str = "",
) -> ChannelDispatchResult:
    state_dir = Path(state_dir)
    with channel_dispatch_lock(state_dir, channel_id):
        channel = project_channel(state_dir, channel_id) or {}
        request = _reply_by_id(channel, request_id)
        if not request:
            return ChannelDispatchResult(skipped=[{"request_id": request_id, "reason": "reply_request_not_found"}])
        status = str(request.get("status") or "")
        if status not in DISPATCHABLE_STATUSES:
            return ChannelDispatchResult(skipped=[{"request_id": request_id, "reason": f"status_{status}"}])
        if status == "queued" and not allow_queued:
            return ChannelDispatchResult(skipped=[{"request_id": request_id, "reason": "queued_waiting_for_drain"}])

        member = _member_by_id(channel, str(request.get("target_member_id") or ""))
        message = _message_by_id(channel, str(request.get("message_id") or ""))
        target_member_id = str(request.get("target_member_id") or "")
        inactive = reject_inactive_channel_member(
            writer=writer,
            member=member,
            request=request,
            request_id=request_id,
            channel_id=channel_id,
            target_member_id=target_member_id,
            actor=actor,
            source=source,
        )
        if inactive:
            return ChannelDispatchResult(failed=[request_id], skipped=[inactive])
        if channel_dispatch_capacity(channel) <= 0:
            return ChannelDispatchResult(skipped=[{
                "request_id": request_id,
                "reason": "parallel_limit",
            }])
        if target_member_id:
            active_reply = active_reply_for_target(
                channel,
                target_member_id,
                exclude_request_id=request_id,
            )
            if active_reply:
                return ChannelDispatchResult(skipped=[{
                    "request_id": request_id,
                    "reason": "target_busy",
                    "active_request_id": str(active_reply.get("request_id") or ""),
                }])
        run_fields = provider_run_fields_for_request(channel_id, request)
        started = writer.emit(
            "channel.agent.reply.started",
            actor=actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=str(request.get("event_id") or "") or None,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": str(request.get("thread_id") or "main"),
                "request_id": request_id,
                "message_id": str(request.get("message_id") or ""),
                "target_member_id": target_member_id,
                "context_pack_id": str(request.get("context_pack_id") or ""),
                "provider_session_id": str(member.get("provider_session_id") or ""),
                "provider_binding_id": str(member.get("provider_binding_id") or ""),
                "remote_agent_id": str(member.get("remote_agent_id") or ""),
                "worker_session_id": _worker_session(member) or str(request.get("worker_session_id") or ""),
                **run_fields,
                "source": source,
            },
        )
    try:
        hydrated_text = hydrate_channel_message_text(Path(state_dir), message, strict=True)
    except Exception as exc:
        emit_channel_reply_failed(
            writer=writer,
            request=request,
            request_id=request_id,
            started_event_id=started.id,
            actor=actor,
            source=source,
            channel_id=channel_id,
            reason=f"message_body_missing: {exc}",
            provider_session_id="",
        )
        return ChannelDispatchResult(dispatched=[request_id], failed=[request_id])
    if hydrated_text:
        message = {**message, "text": hydrated_text}

    if deterministic_reply is not None:
        return _complete_deterministic_reply(
            state_dir=state_dir,
            writer=writer,
            channel=channel,
            message=message,
            request=request,
            request_id=request_id,
            started_event_id=started.id,
            actor=actor,
            source=source,
            reply=deterministic_reply,
            reason=deterministic_reason,
        )

    worker_session_id = _worker_session(member) or str(request.get("worker_session_id") or "")
    if worker_session_id:
        writer.emit(
            "worker.reply.requested",
            actor=actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=started.id,
            correlation_id=channel_id,
            payload={
                "instance_id": worker_session_id,
                "message": str(message.get("text") or ""),
                "task_id": str(request.get("task_id") or ""),
                "channel_id": channel_id,
                "thread_id": str(request.get("thread_id") or "main"),
                "message_id": str(request.get("message_id") or ""),
                "target_member_id": str(request.get("target_member_id") or ""),
                "context_pack_id": str(request.get("context_pack_id") or ""),
                **run_fields,
            },
        )
        return ChannelDispatchResult(dispatched=[request_id])

    backend = str(member.get("backend") or request.get("backend") or "").strip().lower()
    if backend in FAKE_BACKENDS or str(member.get("member_type") or "") in {"persona", "persona_agent"}:
        context_pack = channel_context_pack_by_id(
            channel,
            str(request.get("context_pack_id") or ""),
            state_dir=Path(state_dir),
        )
        response = build_fake_channel_reply(
            member,
            message,
            context_pack,
        )
        reply_payload = channel_message_event_payload(Path(state_dir), {
            "channel_id": channel_id,
            "thread_id": str(request.get("thread_id") or "main"),
            "message_id": f"msg-{request_id}-reply",
            "member_id": str(request.get("target_member_id") or ""),
            "role": "assistant",
            "source": source,
            "text": response,
            "mentions": [],
            "refs": {"request_id": request_id, "run_id": run_fields["run_id"],
                     **_origin_external_refs(message)},
        }, created_by=f"channel-adapter:{source}", source_event_id=started.id)
        message_event = writer.emit(
            "channel.message.posted",
            actor=str(request.get("target_member_id") or actor),
            task_id=str(request.get("task_id") or "") or None,
            causation_id=started.id,
            correlation_id=channel_id,
            payload=reply_payload,
        )
        completed_event = writer.emit(
            "channel.agent.reply.completed",
            actor=actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=message_event.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": str(request.get("thread_id") or "main"),
                "request_id": request_id,
                "message_id": str(request.get("message_id") or ""),
                "target_member_id": str(request.get("target_member_id") or ""),
                "context_pack_id": str(request.get("context_pack_id") or ""),
                "reason": "deterministic fake provider completed",
                **run_fields,
                "source": source,
            },
        )
        emit_structured_reply_events(
            state_dir=Path(state_dir),
            writer=writer,
            channel=channel,
            request=request,
            message=message,
            reply=response,
            reply_event_id=completed_event.id,
            actor=actor,
            source=source,
        )
        return ChannelDispatchResult(dispatched=[request_id], completed=[request_id])

    headless_backend = HEADLESS_BACKENDS.get(backend)
    if headless_backend:
        return _dispatch_headless_reply(
            state_dir=Path(state_dir),
            project_root=project_root,
            writer=writer,
            channel=channel,
            member=member,
            message=message,
            request=request,
            request_id=request_id,
            started_event_id=started.id,
            actor=actor,
            source=source,
            headless_backend=headless_backend,
            headless_backends=headless_backends,
        )

    if backend == "openclaw":
        try:
            result = dispatch_openclaw_channel_reply(
                state_dir=Path(state_dir),
                writer=writer,
                config=config,
                channel=channel,
                member=member,
                message=message,
                request=request,
                request_id=request_id,
                started_event_id=started.id,
                actor=actor,
                source=source,
                client=openclaw_client,
            )
        except Exception as exc:
            emit_channel_reply_failed(
                writer=writer,
                request=request,
                request_id=request_id,
                started_event_id=started.id,
                actor=actor,
                source=source,
                channel_id=channel_id,
                reason=f"openclaw dispatch crashed: {exc}",
                provider_session_id="",
                failure_status="exception",
            )
            return ChannelDispatchResult(dispatched=[request_id], failed=[request_id])
        if result.ok:
            return ChannelDispatchResult(dispatched=[request_id], completed=[request_id])
        return ChannelDispatchResult(dispatched=[request_id], failed=[request_id])

    reason = "provider binding is missing"
    if backend in REMOTE_BACKENDS:
        reason = f"{backend} provider binding is missing"
    writer.emit(
        "channel.agent.reply.failed",
        actor=actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=started.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": str(request.get("thread_id") or "main"),
            "request_id": request_id,
            "message_id": str(request.get("message_id") or ""),
            "target_member_id": str(request.get("target_member_id") or ""),
            "context_pack_id": str(request.get("context_pack_id") or ""),
            "reason": reason,
            **run_fields,
            "source": source,
        },
    )
    return ChannelDispatchResult(dispatched=[request_id], failed=[request_id])


def dispatch_pending_replies(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel_id: str,
    actor: str,
    source: str,
    target_member_id: str = "",
    max_dispatch: int = 6,
    allow_queued: bool = False,
    project_root: Path | None = None,
    headless_backends: dict[str, Any] | None = None,
    config: ZfConfig | None = None,
    openclaw_client: OpenClawGatewayClient | None = None,
    deterministic_reply: str | None = None,
    deterministic_reason: str = "",
) -> ChannelDispatchResult:
    channel = project_channel(Path(state_dir), channel_id) or {}
    candidates = [
        item for item in channel.get("reply_requests") or []
        if str(item.get("status") or "") in DISPATCHABLE_STATUSES
        and (allow_queued or str(item.get("status") or "") == "pending")
        and (not target_member_id or str(item.get("target_member_id") or "") == target_member_id)
    ]
    superseded_request_ids: list[str] = []
    if allow_queued:
        latest_by_target: dict[str, dict[str, Any]] = {}
        superseded: list[dict[str, Any]] = []
        for item in candidates:
            target = str(item.get("target_member_id") or "")
            previous = latest_by_target.get(target)
            if previous is not None:
                superseded.append(previous)
            latest_by_target[target] = item
        for item in superseded:
            _emit_superseded_queued_reply(
                writer=writer,
                channel_id=channel_id,
                request=item,
                actor=actor,
                source=source,
            )
            superseded_request_ids.append(str(item.get("request_id") or ""))
        candidates = list(latest_by_target.values())
    dispatched: list[str] = []
    completed: list[str] = []
    failed: list[str] = [request_id for request_id in superseded_request_ids if request_id]
    skipped: list[dict[str, str]] = []

    def _dispatch_one(item: dict[str, Any]) -> ChannelDispatchResult:
        return dispatch_reply_request(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            request_id=str(item.get("request_id") or ""),
            actor=actor,
            source=source,
            allow_queued=allow_queued,
            project_root=project_root,
            headless_backends=headless_backends,
            config=config,
            openclaw_client=openclaw_client,
            deterministic_reply=deterministic_reply,
            deterministic_reason=deterministic_reason,
        )

    results = dispatch_candidate_waves(
        candidates,
        channel_id=channel_id,
        channel_loader=lambda: project_channel(Path(state_dir), channel_id) or {},
        dispatch_one=_dispatch_one,
        max_dispatch=max_dispatch,
    )
    for result in results:
        dispatched.extend(result.dispatched)
        completed.extend(result.completed)
        failed.extend(result.failed)
        skipped.extend(result.skipped)
    return ChannelDispatchResult(dispatched=dispatched, completed=completed, failed=failed, skipped=skipped)


def _emit_superseded_queued_reply(
    *,
    writer: EventWriter,
    channel_id: str,
    request: dict[str, Any],
    actor: str,
    source: str,
) -> None:
    request_id = str(request.get("request_id") or "")
    if not request_id:
        return
    writer.emit(
        "channel.agent.reply.failed",
        actor=actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=str(request.get("event_id") or "") or None,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": str(request.get("thread_id") or "main"),
            "request_id": request_id,
            "message_id": str(request.get("message_id") or ""),
            "target_member_id": str(request.get("target_member_id") or ""),
            "context_pack_id": str(request.get("context_pack_id") or ""),
            "reason": "superseded by latest queued mention",
            **provider_run_fields_for_request(channel_id, request),
            "source": source,
        },
    )


def _reply_by_id(channel: dict[str, Any], request_id: str) -> dict[str, Any]:
    for item in channel.get("reply_requests") or []:
        if str(item.get("request_id") or "") == request_id:
            return item
    return {}


def _member_by_id(channel: dict[str, Any], member_id: str) -> dict[str, Any]:
    for item in channel.get("members") or []:
        if isinstance(item, dict) and str(item.get("member_id") or "") == member_id:
            return item
    return {}


def _message_by_id(channel: dict[str, Any], message_id: str) -> dict[str, Any]:
    for item in channel.get("messages") or channel.get("recent_messages") or []:
        if isinstance(item, dict) and str(item.get("message_id") or "") == message_id:
            return item
    return {}


def _worker_session(member: dict[str, Any]) -> str:
    return str(
        member.get("backing_worker_session_id")
        or member.get("worker_session_id")
        or member.get("instance_id")
        or "",
    ).strip()


def _permission_drift_block_reason(drift: dict[str, Any]) -> str:
    fields = [
        str(item.get("field") or "")
        for item in drift.get("items", []) or []
        if isinstance(item, dict) and item.get("severity") == "blocking"
    ]
    suffix = f": {', '.join(field for field in fields if field)}" if fields else ""
    return f"provider permission snapshot drift blocked resume{suffix}"


def _dispatch_headless_reply(
    *,
    state_dir: Path,
    project_root: Path | None,
    writer: EventWriter,
    channel: dict[str, Any],
    member: dict[str, Any],
    message: dict[str, Any],
    request: dict[str, Any],
    request_id: str,
    started_event_id: str,
    actor: str,
    source: str,
    headless_backend: str,
    headless_backends: dict[str, Any] | None,
) -> ChannelDispatchResult:
    channel_id = str(channel.get("channel_id") or request.get("channel_id") or "")
    try:
        result = _run_headless_reply(
            state_dir=state_dir,
            project_root=_project_root_for_state(state_dir, project_root),
            writer=writer,
            channel=channel,
            member=member,
            message=message,
            request=request,
            request_id=request_id,
            started_event_id=started_event_id,
            actor=actor,
            source=source,
            backend=headless_backend,
            backends=headless_backends,
        )
    except Exception as exc:
        emit_channel_reply_failed(
            writer=writer,
            request=request,
            request_id=request_id,
            started_event_id=started_event_id,
            actor=actor,
            source=source,
            channel_id=channel_id,
            reason=str(exc),
            provider_session_id="",
            failure_status="exception",
        )
        return ChannelDispatchResult(dispatched=[request_id], failed=[request_id])

    if not bool(getattr(result, "ok", False)):
        reason = str(getattr(result, "error", "") or getattr(result, "status", "") or "headless provider failed")
        emit_channel_reply_failed(
            writer=writer,
            request=request,
            request_id=request_id,
            started_event_id=started_event_id,
            actor=actor,
            source=source,
            channel_id=channel_id,
            reason=reason,
            provider_session_id=str(getattr(result, "provider_session_id", "") or ""),
            failure_status=str(getattr(result, "status", "") or "failed"),
        )
        return ChannelDispatchResult(dispatched=[request_id], failed=[request_id])

    reply = str(getattr(result, "reply", "") or "").strip()
    if not reply:
        reply = "(provider completed without text)"
    thread_id = str(request.get("thread_id") or "main")
    provider_session_id = str(getattr(result, "provider_session_id", "") or "")
    usage = getattr(result, "usage", {}) if isinstance(getattr(result, "usage", {}), dict) else {}
    run_fields = provider_run_fields_for_request(channel_id, request)
    reply_payload = channel_message_event_payload(state_dir, {
        "channel_id": channel_id,
        "thread_id": thread_id,
        "message_id": f"msg-{request_id}-reply",
        "member_id": str(request.get("target_member_id") or ""),
        "role": "assistant",
        "source": str(getattr(result, "backend", "") or headless_backend),
        "text": reply,
        "mentions": [],
        "refs": {
            "request_id": request_id,
            "run_id": run_fields["run_id"],
            "provider_session_id": provider_session_id,
            "usage": usage,
            "continuation_count": int(
                getattr(result, "continuation_count", 0) or 0
            ),
            "completion_reason": str(
                getattr(result, "completion_reason", "") or ""
            ),
            **_origin_external_refs(message),
        },
    }, created_by=f"channel-adapter:{source}", source_event_id=started_event_id)
    message_event = writer.emit(
        "channel.message.posted",
        actor=str(request.get("target_member_id") or actor),
        task_id=str(request.get("task_id") or "") or None,
        causation_id=started_event_id,
        correlation_id=channel_id,
        payload=redact_obj(reply_payload),
    )
    completed_event = writer.emit(
        "channel.agent.reply.completed",
        actor=actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=message_event.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "message_id": str(request.get("message_id") or ""),
            "target_member_id": str(request.get("target_member_id") or ""),
            "context_pack_id": str(request.get("context_pack_id") or ""),
            "provider_session_id": provider_session_id,
            "continuation_count": int(
                getattr(result, "continuation_count", 0) or 0
            ),
            "completion_reason": str(
                getattr(result, "completion_reason", "") or ""
            ),
            "reason": "headless provider completed",
            **run_fields,
            "source": source,
        },
    )
    emit_structured_reply_events(
        state_dir=Path(state_dir),
        writer=writer,
        channel=channel,
        request=request,
        message=message,
        reply=reply,
        reply_event_id=completed_event.id,
        actor=actor,
        source=source,
    )
    return ChannelDispatchResult(dispatched=[request_id], completed=[request_id])


def _run_headless_reply(
    *,
    state_dir: Path,
    project_root: Path,
    writer: EventWriter,
    channel: dict[str, Any],
    member: dict[str, Any],
    message: dict[str, Any],
    request: dict[str, Any],
    request_id: str,
    started_event_id: str,
    actor: str,
    source: str,
    backend: str,
    backends: dict[str, Any] | None,
) -> Any:
    # Lazy import avoids making the deterministic channel projection import the
    # optional headless provider runtime unless a real provider is requested.
    from zf.web.headless_agent import (
        ClaudeHeadlessBackend,
        CodexHeadlessBackend,
        HeadlessTurnResult,
        HeadlessThreadStore,
    )

    adapters = backends or {
        "claude-headless": ClaudeHeadlessBackend(),
        "codex-headless": CodexHeadlessBackend(),
    }
    adapter = adapters.get(backend)
    if adapter is None:
        raise RuntimeError(f"{backend} adapter is unavailable")
    if not adapter.available():
        raise RuntimeError(f"{backend} command is unavailable")

    store = HeadlessThreadStore(state_dir=state_dir, project_root=project_root)
    channel_id = str(channel.get("channel_id") or request.get("channel_id") or "")
    thread_key = _channel_provider_thread_key(
        channel_id=channel_id,
        request=request,
        message=message,
    )
    thread = store.load(scope="project", task_id="", thread_key=thread_key)
    thread_id = str(thread["thread_id"])
    timeout_s = _channel_provider_headless_timeout_s()
    run_fields = provider_run_fields_for_request(channel_id, request)
    runtime_snapshot_ref = str(
        request.get("runtime_snapshot_ref")
        or request.get("snapshot_ref")
        or ""
    )
    permission_profile = normalize_permission_profile(
        member.get("permission_profile") or request.get("permission_profile") or ""
    )

    with store.locked(thread_id):
        thread = store.load(scope="project", task_id="", thread_key=thread_key)
        provider_session_id = store.provider_session_id(thread, backend=backend)
        base_snapshot = build_provider_permission_snapshot(
            backend=backend,
            permission_profile=permission_profile,
            cwd=project_root,
            project_id=str(channel.get("project_id") or channel_id),
            conversation_id=channel_id,
            thread_id=thread_id,
            run_id=str(run_fields["run_id"]),
            provider_session_id=provider_session_id,
            runtime_snapshot_ref=runtime_snapshot_ref,
            role=str(member.get("channel_role") or member.get("role") or ""),
            member_id=str(member.get("member_id") or request.get("target_member_id") or ""),
            source="channel.headless",
            **channel_profile_permission_fields(member),
        )
        drift = provider_permission_drift(
            store.provider_permission_snapshot(thread, backend=backend),
            base_snapshot,
        )
        stream = AgentSessionStreamEmitter(
            writer=writer,
            # wrapped run: channel.message.posted commits the full reply body
            commit_final_text=False,
            identity=AgentSessionIdentity(
                run_id=str(run_fields["run_id"]),
                thread_id=thread_id,
                source="channel.headless",
                actor=actor,
                task_id=str(request.get("task_id") or "") or None,
                causation_id=started_event_id,
                correlation_id=channel_id,
                project_id=str(channel.get("project_id") or channel_id),
                conversation_id=channel_id,
                channel_id=channel_id,
                request_id=request_id,
                message_id=str(request.get("message_id") or ""),
                member_id=str(member.get("member_id") or request.get("target_member_id") or ""),
                target_member_id=str(request.get("target_member_id") or ""),
                provider=str(member.get("provider") or member.get("backend") or backend),
                backend=backend,
                provider_session_id=provider_session_id,
                snapshot_ref=runtime_snapshot_ref,
            ),
        )
        stream.start(
            provider_session_id=provider_session_id,
            permission_snapshot=base_snapshot,
            permission_drift=drift,
        )
        if provider_session_id and drift.get("status") == "blocking":
            result = HeadlessTurnResult(
                ok=False,
                status="permission_drift_blocked",
                backend=backend,
                thread_id=thread_id,
                provider_session_id=provider_session_id,
                reply="",
                messages=[],
                usage={},
                resumed=True,
                error=_permission_drift_block_reason(drift),
                permission_snapshot=base_snapshot,
                permission_drift=drift,
            )
            stream.fail(
                status=result.status,
                reason=result.error,
                provider_session_id=provider_session_id,
                permission_snapshot=base_snapshot,
                permission_drift=drift,
            )
            emit_provider_permission_snapshot(
                writer,
                task_id=str(request.get("task_id") or "") or None,
                causation_id=started_event_id,
                correlation_id=channel_id,
                actor=actor,
                snapshot=base_snapshot,
                drift=drift,
            )
            store.record_turn(thread, result=result, workdir=str(project_root))
            return result

        def pin(session_id: str) -> None:
            store.pin_provider_session(
                thread,
                backend=backend,
                provider_session_id=session_id,
                workdir=str(project_root),
                status="running",
                permission_snapshot=snapshot_with_provider_session(
                    base_snapshot,
                    session_id,
                ),
                permission_drift=drift,
            )

        visible_stream = ChannelVisibleMessageEmitter(stream.emit_message)
        system_prompt = _build_channel_system_prompt(member, channel=channel)

        def run_provider_turn(turn_prompt: str, session_id: str) -> Any:
            return adapter.run_turn(
                prompt=turn_prompt,
                cwd=project_root,
                system_prompt=system_prompt,
                thread_id=thread_id,
                provider_session_id=session_id,
                on_session_id=pin,
                on_message=visible_stream.emit,
                timeout_s=timeout_s,
                thinking_level=str(member.get("thinking_level") or ""),
                run_id=request_id,
                run_thread_id=thread_key,
                project_id=str(channel.get("channel_id") or channel_id),
                conversation_id=channel_id,
                permission_profile=permission_profile,
            )

        result = run_channel_provider_with_continuations(
            run_turn=run_provider_turn,
            initial_prompt=_build_channel_prompt(
                channel=channel,
                member=member,
                message=message,
                request=request,
                state_dir=Path(state_dir),
            ),
            provider_session_id=provider_session_id,
            channel=channel,
            request=request,
            message=message,
        )
        visible_stream.finish()
        final_snapshot = snapshot_with_provider_session(
            base_snapshot,
            str(getattr(result, "provider_session_id", "") or ""),
        )
        result = replace(
            result,
            permission_snapshot=final_snapshot,
            permission_drift=drift,
        )
        usage = getattr(result, "usage", {}) if isinstance(getattr(result, "usage", {}), dict) else {}
        if bool(getattr(result, "ok", False)):
            stream.complete(
                status=str(getattr(result, "status", "") or "completed"),
                reason="headless channel reply completed",
                provider_session_id=str(getattr(result, "provider_session_id", "") or ""),
                usage=usage,
                permission_snapshot=final_snapshot,
                permission_drift=drift,
            )
        else:
            stream.fail(
                status=str(getattr(result, "status", "") or "failed"),
                reason=str(getattr(result, "error", "") or getattr(result, "status", "") or "headless provider failed"),
                provider_session_id=str(getattr(result, "provider_session_id", "") or ""),
                usage=usage,
                permission_snapshot=final_snapshot,
                permission_drift=drift,
            )
        emit_provider_permission_snapshot(
            writer,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=started_event_id,
            correlation_id=channel_id,
            actor=actor,
            snapshot=final_snapshot,
            drift=drift,
        )
        store.record_turn(thread, result=result, workdir=str(project_root))
        return result


def _channel_provider_thread_key(
    *,
    channel_id: str,
    request: dict[str, Any],
    message: dict[str, Any],
) -> str:
    """Return the durable provider-thread key for one Channel reply.

    Conversational contributions retain a member-scoped provider session.  A
    typed deliberation phase is different: it receives an authoritative,
    complete context pack and must be isolated from prior agent tool history.
    Repairs preserve continuity by retaining the original phase request id.
    """
    thread_id = str(request.get("thread_id") or "main")
    member_id = str(request.get("target_member_id") or "")
    base = f"channel:{channel_id}:{thread_id}:{member_id}"
    refs = message.get("refs") if isinstance(message.get("refs"), dict) else {}
    for ref_key in _PHASE_SESSION_REF_KEYS:
        phase_id = str(refs.get(ref_key) or "").strip()
        if phase_id:
            return f"{base}:phase:{ref_key}:{phase_id}"
    return base


def _channel_provider_headless_timeout_s() -> float:
    raw = os.environ.get("ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S")
    if raw is None:
        raw = os.environ.get("ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S")
    if raw is None:
        return DEFAULT_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S
    try:
        timeout_s = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S
    return timeout_s if timeout_s > 0 else DEFAULT_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S


def _build_channel_system_prompt(
    member: dict[str, Any],
    *,
    channel: dict[str, Any] | None = None,
) -> str:
    member_id = str(member.get("member_id") or "agent")
    role = str(member.get("channel_role") or member.get("role") or "channel member")
    provider = str(member.get("backend") or member.get("provider") or "agent")
    permission_profile = normalize_permission_profile(member.get("permission_profile"))
    write_policy = (
        member.get("write_policy")
        if isinstance(member.get("write_policy"), dict)
        else permission_profile_write_policy(permission_profile)
    )
    discussion = (
        channel.get("discussion")
        if isinstance(channel, dict)
        and isinstance(channel.get("discussion"), dict)
        else {}
    )
    mode = discussion.get("mode") or discussion.get("product_mode") or "conversation"
    skill_refs = active_channel_skill_refs(
        member.get("skill_refs") or [],
        discussion_mode=mode,
    )
    resolved_skill_refs = active_channel_resolved_skill_refs(
        member.get("resolved_skill_refs") or [],
        discussion_mode=mode,
    )
    prompt = (
        f"You are {member_id}, a {provider} agent participating in a ZaoFu Agent Channel. "
        f"Your channel role is {role}. Reply as a channel teammate. Keep the answer concise, "
        "grounded in the provided channel context, and do not mutate runtime state directly. "
        "Do not include step-by-step progress narration such as 'I will check' or "
        "'I will write' in the final reply; ZaoFu renders runtime activity separately. "
        "Use concise Markdown. When reporting completed work, prefer sections like "
        "Result, Path, Changes, Not changed, Risks, and Next step when applicable. "
        f"Your channel permission_profile is {permission_profile}. "
        f"Write policy: {redact_obj(write_policy)}. "
        f"Channel skill refs: {redact_obj(skill_refs)}. "
        f"Resolved channel skill refs: {redact_obj(resolved_skill_refs)}. "
        "If work should be executed by ZaoFu, recommend a controlled workflow/action request. "
        "Only write files when the permission_profile and write policy explicitly allow it."
    )
    # Member-declared reply-output contract (folded from channel.member.invited
    # payload.reply_contract): the inviter owns the semantics, the kernel only
    # relays it — e.g. the Feishu kanban agent's action-proposal JSON contract.
    reply_contract = str(member.get("reply_contract") or "").strip()
    if reply_contract:
        prompt = f"{prompt}\n\n{reply_contract}"
    return prompt


def _project_root_for_state(state_dir: Path, project_root: Path | None) -> Path:
    if project_root is not None:
        return Path(project_root).resolve(strict=False)
    try:
        state = SessionStore(state_dir / "session.yaml").load()
        if state.project_root:
            return Path(state.project_root).expanduser().resolve(strict=False)
    except ZfNotInitialized:
        pass
    except Exception:
        pass
    return state_dir.parent.resolve(strict=False)
