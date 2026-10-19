"""Runtime bridge from headless Kanban turns to durable WebChat resources."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from zf.core.security.redaction import redact_obj, redact_text
from zf.web.agent_artifact_store import AgentArtifactStore
from zf.web.agent_conversation_store import AgentConversationStore, ConversationNotFound
from zf.web.agent_runtime_db import digest_json
from zf.web.agent_tool_policy import AgentToolCatalog
from zf.web.agent_profile_builder import AgentProfileBuilder
from zf.web.agent_turn_store import (
    AgentInteractionValidationError,
    AgentTurnConflict,
    AgentTurnStore,
    TurnClaim,
)
from zf.web.headless_agent import HeadlessMessage


def durable_webchat_enabled() -> bool:
    """Allow an operator to roll message execution back to the legacy path."""

    value = str(os.environ.get("ZF_KANBAN_WEBCHAT_RUNTIME") or "durable").strip().lower()
    return value not in {"0", "false", "off", "legacy"}


@dataclass(frozen=True)
class DurableWebChatRun:
    project_id: str
    conversation_id: str
    thread_id: str
    turn_id: str
    user_message_id: str
    claim: TurnClaim
    tool_catalog_digest: str


def prepare_durable_webchat_run(
    *,
    state_dir: Path,
    project_root: Path,
    payload: Mapping[str, Any],
    message: str,
    backend: str,
    permission_profile: str,
    turn_id: str,
    user_message_id: str,
    task_id: str = "",
) -> DurableWebChatRun:
    project_id = str(payload.get("project_id") or project_root.name or "default")
    conversation_id = str(payload.get("conversation_id") or f"kanban-agent:{project_id}")
    thread_id = str(payload.get("thread_key") or payload.get("thread_id") or "main")
    current_profile = AgentProfileBuilder(
        state_dir, project_root=project_root,
    ).current("kanban-agent")
    profile_body = (
        current_profile.get("profile")
        if isinstance((current_profile or {}).get("profile"), dict)
        else {}
    )
    if profile_body:
        profile_backend = str(profile_body.get("provider") or "")
        profile_permission = str(profile_body.get("permission_profile") or "")
        if profile_backend and profile_backend != backend:
            raise AgentTurnConflict(
                f"applied profile provider is {profile_backend!r}; requested {backend!r}"
            )
        if profile_permission and profile_permission != permission_profile:
            raise AgentTurnConflict(
                "applied profile permission_profile does not match the requested turn"
            )
        requested_profile_digest = str(payload.get("profile_digest") or "")
        current_profile_digest = str((current_profile or {}).get("profile_digest") or "")
        if requested_profile_digest and requested_profile_digest != current_profile_digest:
            raise AgentTurnConflict("requested profile digest is stale")
    selected_tools = profile_body.get("tools") if isinstance(profile_body.get("tools"), list) else None
    tool_catalog = AgentToolCatalog().projection(
        surface="kanban_agent", selected_tools=selected_tools,
    )
    conversations = AgentConversationStore(state_dir)
    retry_message_id = str(payload.get("retry_message_id") or "").strip()
    if retry_message_id:
        try:
            retry_source = conversations.get_message(
                retry_message_id, project_id=project_id,
            )
            if (
                str(retry_source.get("conversation_id") or "") != conversation_id
                or (
                    str(retry_source.get("thread_id") or "") != thread_id
                    and not conversations.message_visible_in_thread(
                        retry_message_id,
                        project_id=project_id,
                        conversation_id=conversation_id,
                        thread_id=thread_id,
                    )
                )
            ):
                raise AgentTurnConflict("retry source belongs to another conversation or thread")
            if str(retry_source.get("role") or "") == "assistant":
                input_message_id = str(retry_source.get("parent_message_id") or "")
                if not input_message_id:
                    raise AgentTurnConflict("assistant retry has no bound input message")
                retry_source = conversations.get_message(
                    input_message_id, project_id=project_id,
                )
            if str(retry_source.get("role") or "") != "user":
                raise AgentTurnConflict("retry source must resolve to an immutable user message")
            if str(retry_source.get("conversation_id") or "") != conversation_id:
                raise AgentTurnConflict("retry source belongs to another conversation or thread")
            if (
                str(retry_source.get("thread_id") or "") != thread_id
                and not conversations.message_visible_in_thread(
                    str(retry_source["message_id"]),
                    project_id=project_id,
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                )
            ):
                raise AgentTurnConflict("retry source belongs to another conversation or thread")
            if str(retry_source.get("content") or "") != message:
                raise AgentTurnConflict("retry content must match the immutable source input")
            user_message_id = str(retry_source["message_id"])
        except ConversationNotFound as exc:
            raise AgentTurnConflict("retry source message not found") from exc
    else:
        conversations.add_message(
            project_id=project_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            role="user",
            origin="web",
            content=message,
            message_id=user_message_id,
            attachment_refs=list(payload.get("attachment_refs") or []),
        )
    compact_summary = conversations.latest_summary(
        project_id=project_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
    )
    permission_digest = digest_json({"permission_profile": permission_profile})
    profile_digest = str(
        (current_profile or {}).get("profile_digest")
        or payload.get("profile_digest")
        or digest_json({
            "backend": backend,
            "permission_profile": permission_profile,
        })
    )
    context_digest = str(payload.get("context_digest") or digest_json({
        "task_id": task_id,
        "context_refs": payload.get("context_refs") or [],
        "workflow_context": payload.get("workflow_context") or {},
        "compact_summary_digest": (compact_summary or {}).get("summary_digest") or "",
    }))
    store = AgentTurnStore(state_dir)
    turn, _ = store.submit(
        project_id=project_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        backend=backend,
        permission_profile=permission_profile,
        idempotency_key=str(
            payload.get("webchat_idempotency_key")
            or (turn_id if retry_message_id else user_message_id)
        ),
        turn_id=turn_id,
        task_id=task_id,
        input_message_id=user_message_id,
        permission_digest=permission_digest,
        tool_catalog_digest=str(tool_catalog["digest"]),
        profile_digest=profile_digest,
        context_digest=context_digest,
        request={
            "message": message,
            "payload": redact_obj(dict(payload)),
            "project_root": str(project_root),
            "compact_summary": compact_summary or {},
            "retry_message_id": retry_message_id,
            "profile_revision": {
                "profile_id": (current_profile or {}).get("profile_id") or "",
                "revision": (current_profile or {}).get("revision") or 0,
                "profile_digest": profile_digest,
            },
            "agent_profile": redact_obj(profile_body),
        },
    )
    claim = store.claim(
        str(turn["turn_id"]),
        owner=f"web:{os.getpid()}",
        lease_seconds=120.0,
        expected_digests={
            "permission_digest": permission_digest,
            "tool_catalog_digest": str(tool_catalog["digest"]),
            "profile_digest": profile_digest,
            "context_digest": context_digest,
        },
    )
    return DurableWebChatRun(
        project_id=project_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        turn_id=str(turn["turn_id"]),
        user_message_id=user_message_id,
        claim=claim,
        tool_catalog_digest=str(tool_catalog["digest"]),
    )


class DurablePartRecorder:
    def __init__(self, *, state_dir: Path, run: DurableWebChatRun) -> None:
        self.state_dir = Path(state_dir)
        self.run = run
        self.store = AgentTurnStore(state_dir)
        self.artifacts = AgentArtifactStore(state_dir)
        self.stale_part_count = 0
        self.pending_interaction: dict[str, Any] | None = None
        self.invalid_interaction_draft: dict[str, Any] | None = None

    def __call__(self, message: HeadlessMessage) -> None:
        native = (
            message.raw.get("native_interaction")
            if isinstance(message.raw, dict)
            and isinstance(message.raw.get("native_interaction"), dict)
            else None
        )
        if message.type == "question" and native is not None:
            try:
                request_id = str(native.get("request_id") or "")
                self.pending_interaction = self.store.request_interaction(
                    turn_id=self.run.turn_id,
                    attempt_id=self.run.claim.attempt_id,
                    kind="ask_user",
                    interaction_id=(f"{request_id}:{self.run.turn_id}" if request_id else ""),
                    request=dict(native),
                    currentness={
                        "attempt_id": self.run.claim.attempt_id,
                        "tool_catalog_digest": self.run.tool_catalog_digest,
                    },
                )[0]
            except AgentInteractionValidationError as exc:
                self.invalid_interaction_draft = {
                    "errors": list(exc.errors),
                    "native_request": dict(native),
                }
        content = message.content or message.output or ""
        content = redact_text(str(content))
        content_ref = ""
        content_digest = digest_json(content)
        refs: dict[str, Any] = {}
        if len(content.encode("utf-8", errors="replace")) > 12_000 or message.type in {"tool", "tool_result"}:
            artifact, _ = self.artifacts.capture_bytes(
                project_id=self.run.project_id,
                logical_name=f"turns/{self.run.turn_id}/parts/{message.type}.txt",
                data=content.encode("utf-8"),
                media_type="text/plain",
                producer={"agent_id": "kanban-agent"},
                lineage={
                    "conversation_id": self.run.conversation_id,
                    "thread_id": self.run.thread_id,
                    "turn_id": self.run.turn_id,
                    "attempt_id": self.run.claim.attempt_id,
                },
            )
            content_ref = str(artifact["storage_uri"])
            content_digest = str(artifact["digest"])
            refs["artifact_version_id"] = artifact["version_id"]
        try:
            self.store.append_part(
                self.run.claim,
                kind=message.type or "status",
                state="committed",
                content_ref=content_ref,
                content_digest=content_digest,
                preview=content,
                refs=refs,
            )
        except AgentTurnConflict:
            self.stale_part_count += 1


def mark_durable_run_running(
    state_dir: Path,
    run: DurableWebChatRun,
    *,
    provider_session_id: str = "",
) -> None:
    AgentTurnStore(state_dir).mark_running(
        run.claim,
        provider_session_id=provider_session_id,
        lease_seconds=120.0,
    )


def fail_durable_run(state_dir: Path, run: DurableWebChatRun, *, reason: str) -> bool:
    try:
        AgentTurnStore(state_dir).settle(run.claim, state="failed", reason=reason)
    except AgentTurnConflict:
        return False
    return True


def _question_semantic_signature(request: Mapping[str, Any]) -> tuple[tuple[Any, ...], ...]:
    """Compare provider-native and legacy question envelopes by user-visible intent."""

    raw_questions = request.get("questions")
    questions = (
        [item for item in raw_questions if isinstance(item, Mapping)]
        if isinstance(raw_questions, list)
        else [request]
    )
    signature: list[tuple[Any, ...]] = []
    for question in questions:
        raw_options = question.get("options")
        options: list[tuple[str, str]] = []
        if isinstance(raw_options, list):
            for option in raw_options:
                if isinstance(option, Mapping):
                    options.append((
                        str(option.get("id") or option.get("value") or "").strip().casefold(),
                        str(option.get("label") or "").strip().casefold(),
                    ))
                else:
                    options.append(("", str(option).strip().casefold()))
        signature.append((
            str(question.get("id") or question.get("question_id") or "").strip().casefold(),
            str(question.get("question") or question.get("text") or "").strip().casefold(),
            tuple(options),
            bool(question.get("allow_other", request.get("allow_other", True))),
        ))
    return tuple(signature)


def settle_durable_reply(
    *,
    state_dir: Path,
    run: DurableWebChatRun,
    reply: str,
    provider_session_id: str,
    ok: bool,
    error: str = "",
    plan_request: Mapping[str, Any] | None = None,
    action_proposal: Mapping[str, Any] | None = None,
    invalid_interaction_draft: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    store = AgentTurnStore(state_dir)
    if not ok:
        applied = fail_durable_run(state_dir, run, reason=error or "provider_failed")
        return {"state": "failed", "applied": applied}
    message, _ = AgentConversationStore(state_dir).add_message(
        project_id=run.project_id,
        conversation_id=run.conversation_id,
        thread_id=run.thread_id,
        role="assistant",
        origin="runtime",
        content=reply,
        parent_message_id=run.user_message_id,
    )
    try:
        store.append_part(
            run.claim,
            kind="final_text",
            state="committed",
            content_ref=str(message["content_ref"]),
            content_digest=str(message["content_digest"]),
            preview=reply,
            refs={"message_id": message["message_id"]},
        )
    except AgentTurnConflict:
        return {"state": store.get(run.turn_id)["state"], "applied": False}

    pending = store.list_interactions(
        turn_id=run.turn_id, status="pending", limit=2,
    )
    interaction: dict[str, Any] | None = pending[0] if pending else None
    invalid_errors: list[str] = [
        str(item)
        for item in (
            invalid_interaction_draft.get("errors", [])
            if isinstance(invalid_interaction_draft, Mapping)
            else []
        )
        if str(item)
    ]
    if isinstance(plan_request, Mapping) and isinstance(action_proposal, Mapping):
        invalid_errors.append("plan_request and action_proposal are mutually exclusive")
    elif interaction is not None and isinstance(action_proposal, Mapping):
        invalid_errors.append("pending question and action_proposal are mutually exclusive")
    elif interaction is not None and isinstance(plan_request, Mapping):
        native_request = interaction.get("request")
        if (
            not isinstance(native_request, Mapping)
            or _question_semantic_signature(native_request)
            != _question_semantic_signature(plan_request)
        ):
            invalid_errors.append(
                "provider-native question and legacy plan_request conflict"
            )
    elif interaction is None and isinstance(plan_request, Mapping):
        request_id = str(plan_request.get("request_id") or plan_request.get("plan_id") or "")
        try:
            interaction, _ = store.request_interaction(
                turn_id=run.turn_id,
                attempt_id=run.claim.attempt_id,
                kind="ask_user",
                interaction_id=(f"{request_id}:{run.turn_id}" if request_id else ""),
                revision=int(plan_request.get("revision") or 1),
                request=dict(plan_request),
                currentness={
                    "attempt_id": run.claim.attempt_id,
                    "tool_catalog_digest": run.tool_catalog_digest,
                },
            )
        except AgentInteractionValidationError as exc:
            invalid_errors.extend(exc.errors)
    elif interaction is None and isinstance(action_proposal, Mapping):
        proposal_id = str(action_proposal.get("proposal_id") or "")
        try:
            interaction, _ = store.request_interaction(
                turn_id=run.turn_id,
                attempt_id=run.claim.attempt_id,
                kind="action_approval",
                interaction_id=(f"{proposal_id}:{run.turn_id}" if proposal_id else ""),
                request=dict(action_proposal),
                currentness={
                    "attempt_id": run.claim.attempt_id,
                    "tool_catalog_digest": run.tool_catalog_digest,
                },
            )
        except AgentInteractionValidationError as exc:
            invalid_errors.extend(exc.errors)
    if invalid_errors:
        invalid_body = json.dumps(redact_obj({
            "schema_version": "webchat-invalid-interaction.v1",
            "errors": invalid_errors,
            "plan_request": dict(plan_request) if isinstance(plan_request, Mapping) else None,
            "action_proposal": dict(action_proposal) if isinstance(action_proposal, Mapping) else None,
            "native_draft": (
                dict(invalid_interaction_draft)
                if isinstance(invalid_interaction_draft, Mapping)
                else None
            ),
        }), ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        invalid_artifact, _ = AgentArtifactStore(state_dir).capture_bytes(
            project_id=run.project_id,
            logical_name=f"turns/{run.turn_id}/invalid-interaction.json",
            data=invalid_body,
            media_type="application/json",
            producer={"agent_id": "kanban-agent"},
            lineage={
                "conversation_id": run.conversation_id,
                "thread_id": run.thread_id,
                "turn_id": run.turn_id,
                "attempt_id": run.claim.attempt_id,
            },
        )
        store.append_part(
            run.claim,
            kind="invalid_interaction",
            state="committed",
            content_ref=str(invalid_artifact["storage_uri"]),
            content_digest=str(invalid_artifact["digest"]),
            preview="; ".join(invalid_errors),
            refs={"artifact_version_id": invalid_artifact["version_id"]},
        )
        turn = store.settle(
            run.claim,
            state="failed",
            reason="invalid_pending_interaction",
            provider_session_id=provider_session_id,
            output_ref=str(message["content_ref"]),
        )
        return {
            "state": turn["state"],
            "applied": True,
            "errors": invalid_errors,
            "invalid_artifact": invalid_artifact,
            "message": message,
        }
    state = "waiting_input" if interaction is not None else "completed"
    try:
        turn = store.settle(
            run.claim,
            state=state,
            reason="pending_interaction" if interaction is not None else "provider_completed",
            provider_session_id=provider_session_id,
            output_ref=str(message["content_ref"]),
            pending_interaction_id=str((interaction or {}).get("interaction_id") or ""),
        )
    except AgentTurnConflict:
        return {"state": store.get(run.turn_id)["state"], "applied": False}
    return {"state": turn["state"], "applied": True, "interaction": interaction, "message": message}


def claim_next_queued_input(
    state_dir: Path,
    run: DurableWebChatRun,
) -> dict[str, Any] | None:
    next_turn_id = str(uuid.uuid4())
    return AgentTurnStore(state_dir).claim_next_queue(
        project_id=run.project_id,
        conversation_id=run.conversation_id,
        thread_id=run.thread_id,
        claimed_turn_id=next_turn_id,
    )


__all__ = [
    "DurablePartRecorder",
    "DurableWebChatRun",
    "claim_next_queued_input",
    "durable_webchat_enabled",
    "fail_durable_run",
    "mark_durable_run_running",
    "prepare_durable_webchat_run",
    "settle_durable_reply",
]
