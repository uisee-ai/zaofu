"""Provider-neutral protocol for the continuous Issue Agent."""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class ProviderRequest:
    system: str
    user: str
    context: Mapping[str, Any]
    allowed_tools: tuple[str, ...] = ()
    timeout_seconds: int = 180


@dataclass(frozen=True)
class ProviderResponse:
    ok: bool
    text: str = ""
    tool_calls: tuple[Mapping[str, Any], ...] = ()
    error: str = ""


class IssueAgentProvider(Protocol):
    name: str

    def complete(self, request: ProviderRequest) -> ProviderResponse: ...


class JsonProviderAgent:
    """Adapter helper for providers that return a JSON object in ``text``."""

    def __init__(self, provider: IssueAgentProvider) -> None:
        self.provider = provider

    def complete_json(self, request: ProviderRequest) -> dict[str, Any]:
        import json

        response = self.provider.complete(request)
        if not response.ok:
            raise RuntimeError(response.error or "Issue Agent provider failed")
        try:
            value = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise ValueError("Issue Agent provider returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("Issue Agent provider response must be a JSON object")
        return value


class SubprocessJsonProvider:
    """Run a provider-neutral JSONL-like adapter without importing a host runtime.

    The command receives one JSON request on stdin and must emit one JSON object
    with ``ok``, ``text``, ``tool_calls`` and ``error`` fields on stdout.  The
    adapter is intentionally synchronous and bounded so it can be hosted by a
    CLI, a Web worker, or a plain subprocess supervisor.
    """

    def __init__(self, command: Sequence[str], *, timeout_seconds: int = 180) -> None:
        self.command = tuple(str(item) for item in command)
        self.timeout_seconds = int(timeout_seconds)
        if not self.command or any(not item for item in self.command):
            raise ValueError("provider command must be a non-empty sequence")
        if self.timeout_seconds <= 0:
            raise ValueError("provider timeout must be positive")
        self.name = self.command[0]

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        payload = {
            "system": request.system,
            "user": request.user,
            "context": dict(request.context),
            "allowed_tools": list(request.allowed_tools),
            "timeout_seconds": request.timeout_seconds,
        }
        try:
            completed = subprocess.run(
                list(self.command),
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=min(self.timeout_seconds, request.timeout_seconds),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ProviderResponse(ok=False, error=str(exc))
        if completed.returncode != 0:
            return ProviderResponse(
                ok=False,
                error=(completed.stderr or f"provider exited {completed.returncode}")[-2000:],
            )
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return ProviderResponse(ok=False, error=f"provider returned invalid JSON: {exc}")
        if not isinstance(value, Mapping):
            return ProviderResponse(ok=False, error="provider response must be a JSON object")
        tool_calls = value.get("tool_calls") or ()
        if not isinstance(tool_calls, Sequence) or isinstance(tool_calls, (str, bytes)):
            return ProviderResponse(ok=False, error="provider tool_calls must be an array")
        return ProviderResponse(
            ok=bool(value.get("ok", True)),
            text=str(value.get("text") or ""),
            tool_calls=tuple(item for item in tool_calls if isinstance(item, Mapping)),
            error=str(value.get("error") or ""),
        )


__all__ = [
    "IssueAgentProvider", "JsonProviderAgent", "ProviderRequest", "ProviderResponse",
    "SubprocessJsonProvider",
]
