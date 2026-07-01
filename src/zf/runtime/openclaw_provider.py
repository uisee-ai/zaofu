"""OpenClaw remote gateway binding for Agent Channel providers."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zf.core.config.schema import OpenClawRemoteBindingConfig, ZfConfig
from zf.core.security.redaction import redact_obj, redact_text
from zf.core.workspace.providers import WorkspaceProviderRegistry
from zf.runtime.openclaw_control import run_openclaw_control_chat_turn


@dataclass(frozen=True)
class OpenClawGatewayResult:
    ok: bool
    status: str
    reason: str = ""
    provider_session_id: str = ""
    reply: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


def resolve_openclaw_binding(
    config: ZfConfig | None,
    binding_id: str = "",
) -> tuple[OpenClawRemoteBindingConfig | None, str]:
    workspace_binding, workspace_error = _resolve_workspace_binding(binding_id)
    if workspace_binding is not None:
        return workspace_binding, ""
    if workspace_error and not _is_unconfigured_error(workspace_error):
        return None, workspace_error

    project_binding, project_error = _resolve_project_binding(config, binding_id)
    if project_binding is not None:
        return project_binding, ""
    if project_error and not _is_unconfigured_error(project_error):
        return None, project_error
    if workspace_error and project_error:
        return None, "openclaw provider binding is not configured"
    return None, workspace_error or project_error or "openclaw provider binding is not configured"


def _resolve_workspace_binding(
    binding_id: str = "",
) -> tuple[OpenClawRemoteBindingConfig | None, str]:
    workspace = (
        os.environ.get("ZF_WORKSPACE", "").strip()
        or os.environ.get("ZF_WORKSPACE_ID", "").strip()
        or "default"
    )
    try:
        openclaw = WorkspaceProviderRegistry(workspace=workspace).openclaw()
    except ValueError as exc:
        return None, str(exc)
    return _select_openclaw_binding(
        openclaw,
        binding_id,
        source="workspace provider registry",
    )


def _resolve_project_binding(
    config: ZfConfig | None,
    binding_id: str = "",
) -> tuple[OpenClawRemoteBindingConfig | None, str]:
    if config is None:
        return None, "openclaw provider binding is not configured"
    openclaw = getattr(getattr(config, "providers", None), "openclaw", None)
    return _select_openclaw_binding(
        openclaw,
        binding_id,
        source="project zf.yaml",
    )


def _select_openclaw_binding(
    openclaw: Any,
    binding_id: str = "",
    *,
    source: str,
) -> tuple[OpenClawRemoteBindingConfig | None, str]:
    bindings = getattr(openclaw, "bindings", {}) if openclaw is not None else {}
    if not bindings:
        return None, "openclaw provider binding is not configured"
    selected = str(binding_id or getattr(openclaw, "default_binding", "") or "").strip()
    if not selected and len(bindings) == 1:
        selected = next(iter(bindings))
    if not selected:
        return None, "openclaw provider binding id is required"
    binding = bindings.get(selected)
    if binding is None:
        return None, f"openclaw provider binding {selected!r} is not configured in {source}"
    return binding, ""


def _is_unconfigured_error(error: str) -> bool:
    return error == "openclaw provider binding is not configured"


class OpenClawGatewayClient:
    """Small HTTP client for the OpenClaw gateway `/tools/invoke` surface."""

    def preflight(self, binding: OpenClawRemoteBindingConfig) -> OpenClawGatewayResult:
        last_missing_tool: OpenClawGatewayResult | None = None
        for tool, args in (
            ("session_status", {}),
            ("sessions_list", {"limit": 1}),
            ("cron", {"action": "list"}),
        ):
            result = self.invoke_tool(
                binding,
                tool=tool,
                args=args,
                session_key="agent:main:main",
            )
            if result.ok:
                return result
            if _is_missing_tool_result(result):
                last_missing_tool = result
                continue
            return result
        return last_missing_tool or OpenClawGatewayResult(
            ok=False,
            status="unavailable",
            reason="OpenClaw gateway did not expose a supported preflight tool",
        )

    def ensure_agent(
        self,
        binding: OpenClawRemoteBindingConfig,
        descriptor: dict[str, Any],
    ) -> OpenClawGatewayResult:
        if not binding.provision_agent:
            return OpenClawGatewayResult(
                ok=True,
                status="skipped",
                reason="remote agent provisioning is disabled for this binding",
                provider_session_id=str(descriptor.get("id") or ""),
                payload={"descriptor": _safe_gateway_payload(descriptor)},
            )
        return self.invoke_tool(
            binding,
            tool="agents",
            args={"action": "upsert", "agent": descriptor},
            session_key="agent:main:main",
        )

    def run_turn(
        self,
        binding: OpenClawRemoteBindingConfig,
        *,
        agent_id: str,
        prompt: str,
        system_prompt: str,
        timeout_seconds: float,
        metadata: dict[str, Any],
    ) -> OpenClawGatewayResult:
        control = self.run_control_chat_turn(
            binding,
            agent_id=agent_id,
            prompt=prompt,
            system_prompt=system_prompt,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
        )
        if control.ok:
            return control

        legacy = self.invoke_tool(
            binding,
            tool="sessions_spawn",
            args={
                "agentId": agent_id,
                "message": prompt,
                "systemPrompt": system_prompt,
                "sessionTarget": "isolated",
                "timeoutSeconds": timeout_seconds,
                "metadata": metadata,
            },
            session_key=f"agent:{agent_id}:channel",
        )
        if _is_missing_tool_result(legacy):
            return control
        return legacy

    def run_control_chat_turn(
        self,
        binding: OpenClawRemoteBindingConfig,
        *,
        agent_id: str,
        prompt: str,
        system_prompt: str,
        timeout_seconds: float,
        metadata: dict[str, Any],
    ) -> OpenClawGatewayResult:
        token_result = _binding_token(binding)
        if isinstance(token_result, OpenClawGatewayResult):
            return token_result
        token = token_result
        try:
            result = run_openclaw_control_chat_turn(
                base_url=binding.base_url,
                token=token,
                agent_id=agent_id,
                prompt=prompt,
                system_prompt=system_prompt,
                timeout_seconds=timeout_seconds,
                metadata=metadata,
            )
            return OpenClawGatewayResult(
                ok=result.ok,
                status=result.status,
                reason=result.reason,
                provider_session_id=result.provider_session_id,
                reply=result.reply,
                payload=result.payload,
            )
        except Exception as exc:
            return OpenClawGatewayResult(
                ok=False,
                status="control_rpc_failed",
                reason=redact_text(f"OpenClaw control RPC failed: {exc}"),
            )

    def invoke_tool(
        self,
        binding: OpenClawRemoteBindingConfig,
        *,
        tool: str,
        args: dict[str, Any],
        action: str = "",
        session_key: str = "",
        idempotency_key: str = "",
        message_channel: str = "",
        account_id: str = "",
        message_to: str = "",
        thread_id: str = "",
    ) -> OpenClawGatewayResult:
        token_result = _binding_token(binding)
        if isinstance(token_result, OpenClawGatewayResult):
            return token_result
        token = token_result
        body = {
            "tool": tool,
            "name": tool,
            "args": args,
            "arguments": args,
        }
        if action:
            body["action"] = action
        if session_key:
            body["sessionKey"] = session_key
        if idempotency_key:
            body["idempotencyKey"] = idempotency_key
        request = urllib.request.Request(
            f"{binding.base_url.rstrip('/')}/tools/invoke",
            data=json.dumps(body).encode("utf-8"),
            headers=_headers(
                token,
                message_channel=message_channel,
                account_id=account_id,
                message_to=message_to,
                thread_id=thread_id,
            ),
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=binding.timeout_seconds,
            ) as response:
                text = response.read(2_000_000).decode("utf-8", errors="replace")
                return _parse_gateway_response(
                    status_code=int(getattr(response, "status", 200)),
                    text=text,
                )
        except urllib.error.HTTPError as exc:
            text = exc.read(100_000).decode("utf-8", errors="replace")
            return _parse_gateway_response(status_code=int(exc.code), text=text)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            return OpenClawGatewayResult(
                ok=False,
                status="unreachable",
                reason=redact_text(f"OpenClaw gateway unreachable: {exc}"),
            )

    def send_message(
        self,
        binding: OpenClawRemoteBindingConfig,
        *,
        channel: str,
        account_id: str,
        target: str,
        message: str,
        presentation: dict[str, Any] | None = None,
        agent_id: str = "zaofu-bridge",
        idempotency_key: str = "",
    ) -> OpenClawGatewayResult:
        args: dict[str, Any] = {
            "to": target,
            "target": target,
            "message": message,
            "text": message,
            "channel": channel,
        }
        if account_id:
            args["accountId"] = account_id
            args["account_id"] = account_id
        if presentation:
            args["presentation"] = presentation
        return self.invoke_tool(
            binding,
            tool="message",
            action="send",
            args=args,
            session_key=f"agent:{agent_id}:message",
            idempotency_key=idempotency_key,
            message_channel=channel,
            account_id=account_id,
            message_to=target,
        )


def build_openclaw_agent_descriptor(
    *,
    binding: OpenClawRemoteBindingConfig,
    project_name: str,
    state_dir: Path,
    channel_id: str,
    member_id: str,
    display_name: str,
    channel_role: str,
    permissions: list[str],
    model: str = "",
) -> dict[str, Any]:
    remote_agent_id = _remote_agent_id(project_name, channel_id, member_id)
    return {
        "id": remote_agent_id,
        "name": display_name or member_id,
        "role": channel_role,
        "model": model,
        "workspace": {
            "policy": binding.default_workspace_policy,
            "key": f"zaofu:{project_name}:{channel_id}:{member_id}",
            "state_hint": str(
                state_dir
                / "channel_providers"
                / "openclaw"
                / _safe_path_part(channel_id)
                / _safe_path_part(member_id)
            ),
        },
        "tools": {
            "profile": binding.tool_profile,
            "permissions": list(permissions),
        },
        "subagents": {
            "allow": [],
        },
        "metadata": {
            "managed_by": "zaofu",
            "provider": "openclaw",
            "binding_id": binding.id,
            "channel_id": channel_id,
            "member_id": member_id,
        },
    }


def _binding_token(
    binding: OpenClawRemoteBindingConfig,
) -> str | OpenClawGatewayResult:
    if not binding.token_env:
        return ""
    token = os.environ.get(binding.token_env, "")
    if token:
        return token
    return OpenClawGatewayResult(
        ok=False,
        status="missing_token",
        reason=f"{binding.token_env} is not set",
    )


def _headers(
    token: str,
    *,
    message_channel: str = "",
    account_id: str = "",
    message_to: str = "",
    thread_id: str = "",
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if message_channel:
        headers["x-openclaw-message-channel"] = message_channel
    if account_id:
        headers["x-openclaw-account-id"] = account_id
    if message_to:
        headers["x-openclaw-message-to"] = message_to
    if thread_id:
        headers["x-openclaw-thread-id"] = thread_id
    return headers


def _is_missing_tool_result(result: OpenClawGatewayResult) -> bool:
    reason = result.reason.lower()
    if result.status in {"http_404", "not_found"}:
        return True
    if "tool not available" in reason or "not found" in reason:
        return True
    error = result.payload.get("error") if isinstance(result.payload, dict) else None
    return isinstance(error, dict) and str(error.get("type") or "") == "not_found"


def _parse_gateway_response(*, status_code: int, text: str) -> OpenClawGatewayResult:
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        data = {"text": text}
    if not isinstance(data, dict):
        data = {"result": data}
    if status_code < 200 or status_code >= 300:
        return OpenClawGatewayResult(
            ok=False,
            status=f"http_{status_code}",
            reason=redact_text(f"Gateway returned {status_code}: {text}"),
            payload=_safe_gateway_payload(data),
        )
    top_ok = data.get("ok")
    if top_ok is False:
        reason = _gateway_error_reason(data) or "OpenClaw gateway returned ok=false"
        return OpenClawGatewayResult(
            ok=False,
            status=str(data.get("status") or _gateway_error_type(data) or "failed"),
            reason=reason,
            payload=_safe_gateway_payload(data),
        )
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    if isinstance(result, dict) and result.get("ok") is False:
        return OpenClawGatewayResult(
            ok=False,
            status=str(result.get("status") or "failed"),
            reason=_gateway_error_reason(result) or "OpenClaw tool returned ok=false",
            payload=_safe_gateway_payload(data),
        )
    reply = _first_text(
        result,
        keys=("reply", "text", "output", "message", "content", "stdout"),
    )
    provider_session_id = _first_text(
        result,
        keys=("provider_session_id", "session_id", "sessionId", "id"),
    )
    usage = result.get("usage") if isinstance(result, dict) else {}
    return OpenClawGatewayResult(
        ok=True,
        status=str(
            (result.get("status") if isinstance(result, dict) else "")
            or data.get("status")
            or "completed"
        ),
        provider_session_id=provider_session_id,
        reply=reply,
        usage=usage if isinstance(usage, dict) else {},
        payload=_safe_gateway_payload(data),
    )


def _gateway_error_reason(data: dict[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        return redact_text(
            str(error.get("message") or error.get("reason") or error)
        )
    if error:
        return redact_text(str(error))
    return redact_text(str(data.get("reason") or data.get("message") or ""))


def _gateway_error_type(data: dict[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("type") or "")
    return ""


def _first_text(data: Any, *, keys: tuple[str, ...]) -> str:
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        content = data.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = _first_text(item, keys=keys)
                    if text:
                        return text
                elif isinstance(item, str) and item.strip():
                    return item.strip()
        messages = data.get("messages")
        if isinstance(messages, list):
            for item in messages:
                if isinstance(item, dict):
                    text = _first_text(item, keys=keys)
                    if text:
                        return text
                elif isinstance(item, str) and item.strip():
                    return item.strip()
    return ""


def _remote_agent_id(project_name: str, channel_id: str, member_id: str) -> str:
    raw = f"zaofu_{project_name}_{channel_id}_{member_id}".lower()
    safe = re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")
    return safe[:96] or "zaofu_openclaw_agent"


def _safe_path_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return safe[:80] or "unknown"


def _safe_gateway_payload(value: Any) -> dict[str, Any]:
    redacted = redact_obj(value)
    return redacted if isinstance(redacted, dict) else {"value": redacted}
