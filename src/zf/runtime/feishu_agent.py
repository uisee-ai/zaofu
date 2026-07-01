"""Feishu-facing command router for ZaoFu integration edges.

The router intentionally stays above the deterministic kernel: it parses a
small `/zf ...` command set and then delegates mutations to
ControlledActionService.  It never writes runtime truth directly.
"""

from __future__ import annotations

import shlex
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter, ZfEvent
from zf.core.security.redaction import redact_obj
from zf.integrations.feishu.gateway import FeishuCommandEnvelope
from zf.integrations.feishu.queries import QueryExecutor
from zf.runtime.control_actions import ControlledActionService


FEISHU_AGENT_SOURCE = "feishu_agent"
QUERY_COMMANDS = {"status", "tasks", "task", "cost", "blockers", "handoff"}
HELP_COMMANDS = {"help", "commands", "cmds", "?"}
LEGACY_AUTH_REQUIRED_COMMANDS = {
    "ask",
    "pause",
    "resume",
    "retry",
    "cancel",
    "note",
    "create",
    "update",
    "request-fanout",
    "approve",
    "deny",
}
_WORKFLOW_COMMANDS = {"workflow", "wf"}
_ATTENTION_COMMANDS = {"attention"}


@dataclass(frozen=True)
class FeishuAgentCommand:
    ok: bool
    status: str
    reason: str = ""
    kind: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    channel_id: str = ""
    thread_id: str = "main"
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeishuAgentResult:
    ok: bool
    status: str
    reason: str = ""
    posted: int = 0
    action_response: dict[str, Any] | None = None
    reply_event_id: str = ""
    workflow_event_id: str = ""


def parse_feishu_agent_command(
    text: str,
    *,
    default_channel_id: str,
    default_thread_id: str = "main",
    require_prefix: str = "/zf",
    accept_plain_text: bool = False,
) -> FeishuAgentCommand:
    """Parse a Feishu/OpenClaw text command into a bounded ZaoFu intent."""

    raw_text = str(text or "").strip()
    prefix = str(require_prefix or "").strip()
    if prefix and raw_text.startswith(prefix):
        body = raw_text[len(prefix):].strip()
    elif accept_plain_text:
        body = raw_text
    else:
        return FeishuAgentCommand(
            ok=False,
            status="rejected",
            reason=f"message must start with {prefix or '/zf'}",
        )
    if not body:
        return FeishuAgentCommand(ok=False, status="rejected", reason="message text is required")

    channel_id = str(default_channel_id or "").strip()
    thread_id = str(default_thread_id or "main").strip() or "main"
    channel_match = re.match(
        r"^(?:channel|ch)\s+([A-Za-z0-9_.:-]+)\s*(.*)$",
        body,
        flags=re.IGNORECASE,
    )
    if channel_match:
        channel_id = channel_match.group(1).strip()
        body = channel_match.group(2).strip()
    if not channel_id:
        return FeishuAgentCommand(ok=False, status="rejected", reason="target channel_id is required")

    explicit_post, post_text = _strip_post_prefix(body)
    if explicit_post:
        return _channel_post_command(channel_id, thread_id, post_text)

    tokens = _split_command(body)
    if not tokens:
        return FeishuAgentCommand(ok=False, status="rejected", reason="message text is required")
    command = tokens[0].lower()
    args = tokens[1:]
    if command in HELP_COMMANDS:
        return FeishuAgentCommand(
            ok=True,
            status="accepted",
            kind="help",
            command="help",
            args=args,
            channel_id=channel_id,
            thread_id=thread_id,
        )
    if command in QUERY_COMMANDS:
        return FeishuAgentCommand(
            ok=True,
            status="accepted",
            kind="query",
            command=command,
            args=args,
            channel_id=channel_id,
            thread_id=thread_id,
        )
    if command in _WORKFLOW_COMMANDS:
        return _workflow_command(args, channel_id=channel_id, thread_id=thread_id)
    if command in _ATTENTION_COMMANDS:
        return _attention_command(args, channel_id=channel_id, thread_id=thread_id)
    if command in LEGACY_AUTH_REQUIRED_COMMANDS:
        return FeishuAgentCommand(
            ok=True,
            status="accepted",
            kind="unsupported_auth_required",
            command=command,
            args=args,
            channel_id=channel_id,
            thread_id=thread_id,
        )

    # Backward compatibility with the original inbound bridge: unknown `/zf ...`
    # text is treated as a ZaoFu channel message instead of being discarded.
    return _channel_post_command(channel_id, thread_id, body)


def execute_feishu_agent_command(
    *,
    command: FeishuAgentCommand,
    state_dir: Path,
    writer: EventWriter,
    requested: ZfEvent,
    config: ZfConfig | None,
    project_root: Path | None,
    actor: str,
    source: str,
    member_id: str,
    inbound_refs: dict[str, Any] | None = None,
) -> FeishuAgentResult:
    """Execute a parsed Feishu command through deterministic ZaoFu APIs."""

    refs = inbound_refs if isinstance(inbound_refs, dict) else {}
    if command.kind == "channel_post":
        response = _controlled(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            actor=actor,
            source=source,
            requested=requested,
            action="channel-post-message",
            requested_action="/zf channel post",
            payload={
                "channel_id": command.channel_id,
                "thread_id": command.thread_id,
                "message_id": f"msg-feishu-inbound-{requested.id[:12]}",
                "member_id": member_id,
                "role": "user",
                "source": source,
                "text": command.text,
                "refs": refs,
            },
        )
        return FeishuAgentResult(
            ok=bool(response.get("ok")),
            status=str(response.get("status") or "posted"),
            posted=1 if response.get("ok") else 0,
            action_response=response,
            reply_event_id=str(response.get("event_id") or ""),
        )

    if command.kind == "query":
        envelope = FeishuCommandEnvelope(
            command=command.command,
            args=list(command.args),
            user_id=str(refs.get("sender_id") or member_id),
            chat_id=str(refs.get("chat_id") or ""),
            message_id=str(refs.get("message_id") or ""),
            idempotency_key=str(refs.get("idempotency_key") or requested.id),
            source="openclaw-feishu",
        )
        reply = QueryExecutor(Path(state_dir)).execute(envelope)
        return _post_agent_reply(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            requested=requested,
            actor=actor,
            command=command,
            text=reply,
            refs=refs,
        )

    if command.kind == "help":
        return _post_agent_reply(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            requested=requested,
            actor=actor,
            command=command,
            text=_help_text(),
            refs=refs,
        )

    if command.kind == "unsupported_auth_required":
        return _post_agent_reply(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            requested=requested,
            actor=actor,
            command=command,
            text=_auth_required_text(command.command),
            refs=refs,
        )

    if command.kind == "workflow_invoke":
        payload = {
            **command.payload,
            "channel_id": command.channel_id,
            "thread_id": command.thread_id,
            "requested_by": FEISHU_AGENT_SOURCE,
            "source_refs": _workflow_source_refs(
                refs,
                source=source,
                requested_event_id=requested.id,
                command=command.command,
            ),
        }
        response = _controlled(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            actor=actor,
            source=FEISHU_AGENT_SOURCE,
            requested=requested,
            action="workflow-invoke",
            requested_action="/zf workflow invoke",
            payload=payload,
        )
        reply = _format_workflow_response(response)
        posted = _post_agent_reply(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            requested=requested,
            actor=actor,
            command=command,
            text=reply,
            refs={**refs, "workflow_response": redact_obj(response)},
        )
        return FeishuAgentResult(
            ok=bool(response.get("ok")),
            status=str(response.get("status") or "requested"),
            reason=str(response.get("reason") or ""),
            posted=posted.posted,
            action_response=response,
            reply_event_id=posted.reply_event_id,
            workflow_event_id=str(response.get("event_id") or ""),
        )

    if command.kind == "attention_action":
        action = str(command.payload.get("action") or "")
        payload = {
            key: value
            for key, value in command.payload.items()
            if key != "action"
        }
        payload.setdefault("source", "openclaw-feishu")
        payload.setdefault("source_event_id", str(refs.get("message_id") or requested.id))
        response = _controlled(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            actor=actor,
            source=FEISHU_AGENT_SOURCE,
            requested=requested,
            action=action,
            requested_action=f"/zf attention {action.removeprefix('attention-')}",
            payload=payload,
        )
        posted = _post_agent_reply(
            state_dir=state_dir,
            writer=writer,
            config=config,
            project_root=project_root,
            requested=requested,
            actor=actor,
            command=command,
            text=_format_attention_response(response),
            refs={**refs, "attention_response": redact_obj(response)},
        )
        return FeishuAgentResult(
            ok=bool(response.get("ok")),
            status=str(response.get("status") or "recorded"),
            reason=str(response.get("reason") or ""),
            posted=posted.posted,
            action_response=response,
            reply_event_id=posted.reply_event_id,
        )

    return FeishuAgentResult(ok=False, status="rejected", reason="unsupported Feishu agent command")


def _post_agent_reply(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: ZfConfig | None,
    project_root: Path | None,
    requested: ZfEvent,
    actor: str,
    command: FeishuAgentCommand,
    text: str,
    refs: dict[str, Any],
) -> FeishuAgentResult:
    response = _controlled(
        state_dir=state_dir,
        writer=writer,
        config=config,
        project_root=project_root,
        actor=actor,
        source=FEISHU_AGENT_SOURCE,
        requested=requested,
        action="channel-post-message",
        requested_action=f"/zf {command.command or command.kind} reply",
        payload={
            "channel_id": command.channel_id,
            "thread_id": command.thread_id,
            "message_id": f"msg-feishu-agent-{requested.id[:12]}",
            "member_id": "zaofu-feishu-agent",
            "role": "assistant",
            "source": FEISHU_AGENT_SOURCE,
            "text": text,
            "refs": {
                "feishu_agent": {
                    "requested_event_id": requested.id,
                    "command": command.command,
                    "kind": command.kind,
                },
                **refs,
            },
        },
    )
    return FeishuAgentResult(
        ok=bool(response.get("ok")),
        status=str(response.get("status") or "posted"),
        posted=1 if response.get("ok") else 0,
        action_response=response,
        reply_event_id=str(response.get("event_id") or ""),
    )


def _controlled(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: ZfConfig | None,
    project_root: Path | None,
    actor: str,
    source: str,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return ControlledActionService(
        Path(state_dir),
        writer,
        config=config,
        project_root=project_root,
        actor=actor,
        source=source,
        surface=source,
    ).execute(
        action=action,
        requested_action=requested_action,
        requested=requested,
        payload=payload,
    )


def _workflow_command(
    args: list[str],
    *,
    channel_id: str,
    thread_id: str,
) -> FeishuAgentCommand:
    rest = list(args)
    if rest and rest[0].lower() == "invoke":
        rest = rest[1:]
    payload = _parse_key_values(rest)
    positional = [item for item in rest if "=" not in item]
    task_id = str(payload.get("task_id") or (positional[0] if positional else "")).strip()
    pattern_id = str(
        payload.get("pattern_id")
        or payload.get("pattern")
        or (positional[1] if len(positional) > 1 else "")
    ).strip()
    if not task_id:
        return FeishuAgentCommand(ok=False, status="rejected", reason="task_id is required")
    if not pattern_id:
        return FeishuAgentCommand(ok=False, status="rejected", reason="pattern_id is required")
    payload["task_id"] = task_id
    payload["pattern_id"] = pattern_id
    return FeishuAgentCommand(
        ok=True,
        status="accepted",
        kind="workflow_invoke",
        command="workflow",
        args=rest,
        channel_id=channel_id,
        thread_id=thread_id,
        payload=payload,
    )


def _attention_command(
    args: list[str],
    *,
    channel_id: str,
    thread_id: str,
) -> FeishuAgentCommand:
    parsed = _attention_action_payload(args)
    if parsed is None:
        return FeishuAgentCommand(
            ok=False,
            status="rejected",
            reason="usage: /zf attention <ack|resolve|snooze|feedback|escalate> <attention_id|fingerprint>",
        )
    action, payload = parsed
    return FeishuAgentCommand(
        ok=True,
        status="accepted",
        kind="attention_action",
        command="attention",
        args=list(args),
        channel_id=channel_id,
        thread_id=thread_id,
        payload={"action": action, **payload},
    )


def _attention_action_payload(args: list[str]) -> tuple[str, dict[str, Any]] | None:
    if not args:
        return None
    verb = args[0].strip().lower().replace("_", "-")
    aliases = {
        "ack": "attention-ack",
        "acknowledge": "attention-ack",
        "resolve": "attention-resolve",
        "resolved": "attention-resolve",
        "snooze": "attention-snooze",
        "feedback": "attention-feedback",
        "escalate": "attention-escalate",
    }
    action = aliases.get(verb)
    if action is None:
        return None
    rest = list(args[1:])
    target = ""
    if rest and "=" not in rest[0]:
        target = rest.pop(0).strip()
    payload = _parse_key_values(rest)
    if target:
        if target.startswith("attn-"):
            payload.setdefault("attention_id", target)
        else:
            payload.setdefault("fingerprint", target)
    if "id" in payload and "attention_id" not in payload:
        payload["attention_id"] = payload.pop("id")
    payload.setdefault("reason", f"feishu {verb}")
    return action, payload


def _parse_key_values(args: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    loose: list[str] = []
    for item in args:
        if "=" not in item:
            loose.append(item)
            continue
        key, value = item.split("=", 1)
        key = key.strip().replace("-", "_")
        value = value.strip()
        if key in {"scope", "open_questions", "artifact_refs"}:
            payload[key] = [part.strip() for part in value.split(",") if part.strip()]
        else:
            payload[key] = value
    if loose and "reason" not in payload and len(loose) > 2:
        payload["reason"] = " ".join(loose[2:])
    return payload


def _channel_post_command(channel_id: str, thread_id: str, text: str) -> FeishuAgentCommand:
    body = str(text or "").strip()
    if not body:
        return FeishuAgentCommand(ok=False, status="rejected", reason="message text is required")
    return FeishuAgentCommand(
        ok=True,
        status="accepted",
        kind="channel_post",
        command="post",
        channel_id=channel_id,
        thread_id=thread_id,
        text=body,
    )


def _split_command(body: str) -> list[str]:
    try:
        return shlex.split(body)
    except ValueError:
        return body.split()


def _strip_post_prefix(body: str) -> tuple[bool, str]:
    stripped = str(body or "").strip()
    next_body = re.sub(
        r"^(?:post|say|记录|record)\s*[:：]?\s*",
        "",
        stripped,
        flags=re.IGNORECASE,
    ).strip()
    return next_body != stripped, next_body


def _format_workflow_response(response: dict[str, Any]) -> str:
    action = str(response.get("action") or "workflow-invoke")
    status = str(response.get("status") or "unknown")
    parts = [f"{action}: {status}"]
    for key in ("task_id", "pattern_id", "workflow_run_id", "workflow_input_manifest_ref"):
        value = str(response.get(key) or "")
        if value:
            parts.append(f"{key}={value}")
    reason = str(response.get("reason") or "")
    if reason:
        parts.append(reason)
    return " | ".join(parts)


def _format_attention_response(response: dict[str, Any]) -> str:
    action = str(response.get("action") or "attention")
    status = str(response.get("status") or "unknown")
    parts = [f"{action}: {status}"]
    attention_id = str(response.get("attention_id") or "")
    if attention_id:
        parts.append(f"attention_id={attention_id}")
    reason = str(response.get("reason") or "")
    if reason:
        parts.append(reason)
    return " | ".join(parts)


def _help_text() -> str:
    return "\n".join([
        "ZaoFu /zf commands available through OpenClaw Feishu:",
        "- /zf status",
        "- /zf tasks",
        "- /zf task <TASK-ID>",
        "- /zf cost",
        "- /zf blockers",
        "- /zf handoff",
        "- /zf post <message>",
        "- /zf channel <CHANNEL-ID> post <message>",
        "- /zf workflow invoke <TASK-ID> pattern_id=<PATTERN>",
        "- /zf attention <ack|resolve|snooze|feedback|escalate> <ATTENTION-ID|FINGERPRINT> [key=value ...]",
        "",
        "Auth-gated commands exist in the legacy Feishu handler but are not enabled through OpenClaw bridge yet:",
        "- /zf ask, pause, resume, retry, cancel, note",
        "- /zf create, update, request-fanout",
        "- /zf approve, deny",
    ])


def _auth_required_text(command: str) -> str:
    return (
        f"/zf {command} is a ZaoFu auth-gated command. "
        "It is not enabled through OpenClaw Feishu bridge yet because this bridge "
        "does not have a user-level authorization mapping. Use /zf help for the "
        "currently enabled command set."
    )


def _workflow_source_refs(
    refs: dict[str, Any],
    *,
    source: str,
    requested_event_id: str,
    command: str,
) -> dict[str, str]:
    return {
        "feishu_agent_source": source,
        "feishu_agent_requested_event_id": requested_event_id,
        "feishu_agent_command": command,
        "feishu_chat_id": str(refs.get("chat_id") or ""),
        "feishu_message_id": str(refs.get("message_id") or ""),
        "feishu_sender_id": str(refs.get("sender_id") or ""),
        "feishu_sender_name": str(refs.get("sender_name") or ""),
        "feishu_idempotency_key": str(refs.get("idempotency_key") or ""),
    }
