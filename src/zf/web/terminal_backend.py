"""Provider-neutral contracts for the Web PTY terminal runtime.

Terminal bytes are deliberately absent from these durable models.  They are
ephemeral bridge traffic, never ZaoFu Task/Event/Workflow truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol


TERMINAL_PROVIDER_KINDS: dict[str, str] = {
    "claude-code": "claude",
    "codex": "codex",
    "opencode": "opencode",
    "pi": "pi",
}


class TerminalRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code

    def projection(self) -> dict[str, object]:
        return {"ok": False, "status": self.code, "reason": str(self)}


@dataclass(frozen=True)
class TerminalCapability:
    available: bool
    backend: str = "herdr"
    binary: str = ""
    version: str = ""
    schema_available: bool = False
    observe_bridge: bool = False
    control_bridge: bool = False
    tab_rename: bool = False
    reason: str = ""
    mouse: bool = False

    def projection(self) -> dict[str, object]:
        value = asdict(self)
        value["binary"] = Path(self.binary).name
        return value


@dataclass(frozen=True)
class HerdrProjectRuntime:
    session_name: str
    server_pid: int | None = None


@dataclass(frozen=True)
class HerdrTerminalResource:
    workspace_id: str
    tab_id: str
    pane_id: str
    terminal_id: str
    agent_name: str
    native_id: str = ""


@dataclass(frozen=True)
class TerminalAgent:
    terminal_id: str
    name: str
    provider: str
    status: str
    native_id: str = ""


@dataclass(frozen=True)
class TerminalBridgeSpec:
    argv: tuple[str, ...]
    mode: str
    cols: int
    rows: int
    mouse: bool = False


@dataclass
class TerminalSessionRecord:
    session_id: str
    slot: str
    title: str
    provider: str
    provider_kind: str
    project_id: str
    project_root: str
    state: str
    generation: int
    created_at: str
    updated_at: str
    herdr_session: str
    workspace_id: str
    tab_id: str
    pane_id: str
    terminal_id: str
    agent_name: str
    provider_session_id: str = ""
    provider_session_path: str = ""
    usage_binding_status: str = ""
    usage_binding_reason: str = ""
    usage_binding_started_at_ns: int = 0
    stopped_at: str = ""
    diagnostics: list[str] = field(default_factory=list)
    herdr_native_id: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> "TerminalSessionRecord":
        fields = cls.__dataclass_fields__
        return cls(**{key: value[key] for key in fields if key in value})

    def projection(
        self,
        *,
        usage: dict[str, object] | None = None,
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "session_id": self.session_id,
            "slot": self.slot,
            "title": self.title,
            "provider": self.provider,
            "provider_kind": self.provider_kind,
            "project_id": self.project_id,
            "state": self.state,
            "generation": self.generation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stopped_at": self.stopped_at,
            "diagnostics": list(self.diagnostics),
        }
        if usage is not None:
            value["usage"] = usage
        return value


class TerminalBackend(Protocol):
    def probe(self) -> TerminalCapability: ...

    def ensure_project_runtime(self, session_name: str) -> HerdrProjectRuntime: ...

    def create_terminal(
        self,
        *,
        runtime: HerdrProjectRuntime,
        workspace_id: str,
        project_root: Path,
        label: str,
        agent_name: str,
        provider_kind: str,
        provider_args: tuple[str, ...],
        start_timeout_seconds: int,
    ) -> HerdrTerminalResource: ...

    def list_agents(self, runtime: HerdrProjectRuntime) -> list[TerminalAgent]: ...

    def terminal_exists(self, *, runtime: HerdrProjectRuntime, tab_id: str) -> bool: ...

    def stop_terminal(self, *, runtime: HerdrProjectRuntime, tab_id: str) -> None: ...

    def rename_terminal(
        self,
        *,
        runtime: HerdrProjectRuntime,
        tab_id: str,
        title: str,
    ) -> None: ...

    def bridge_spec(
        self,
        *,
        runtime: HerdrProjectRuntime,
        target: str,
        mode: str,
        takeover: bool,
        cols: int,
        rows: int,
    ) -> TerminalBridgeSpec: ...
