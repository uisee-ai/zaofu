from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Sequence

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import RuntimeWebTerminalConfig
from zf.web.terminal_backend import (
    HerdrProjectRuntime,
    HerdrTerminalResource,
    TerminalAgent,
    TerminalBridgeSpec,
    TerminalCapability,
    TerminalRuntimeError,
)
from zf.web.terminal_backend_herdr import HerdrTerminalBackend, SubprocessCommandRunner
from zf.web.terminal_environment import terminal_subprocess_env
from zf.web.terminal_gateway import (
    AttachmentTicketStore,
    BridgeProtocolError,
    HerdrNDJSONBridge,
    relay_terminal_websocket,
)
from zf.web import terminal_gateway
from zf.web.terminal_registry import REGISTRY_FILENAME
from zf.web.terminal_service import TerminalService
from zf.web.terminal_usage import TerminalUsageService


def _config(**overrides: object) -> RuntimeWebTerminalConfig:
    values = {
        "enabled": True,
        "max_frame_bytes": 64 * 1024,
        "bridge_queue_bytes": 128 * 1024,
    }
    values.update(overrides)
    return RuntimeWebTerminalConfig(**values)


def test_terminal_subprocess_env_strips_control_secrets_and_parent_agent_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_PASSCODE", "do-not-leak")
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "do-not-leak")
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", "do-not-leak")
    monkeypatch.setenv("ZF_STATE_ENCRYPT_KEY", "do-not-leak")
    monkeypatch.setenv("ZF_DOC156_REQUEST_ID", "keep-context")
    monkeypatch.setenv("OPENAI_API_KEY", "keep-provider-credential")
    monkeypatch.setenv("CODEX_HOME", "/provider/config")
    monkeypatch.setenv("CODEX_CI", "1")
    monkeypatch.setenv("CODEX_SESSION_ID", "parent-codex-session")
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-codex-thread")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "parent-claude")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-claude-session")

    env = terminal_subprocess_env()

    assert "ZF_WEB_PASSCODE" not in env
    assert "ZF_WEB_ACTION_TOKEN" not in env
    assert "ZF_FEISHU_ACTION_TOKEN_SECRET" not in env
    assert "ZF_STATE_ENCRYPT_KEY" not in env
    assert "CODEX_CI" not in env
    assert "CODEX_SESSION_ID" not in env
    assert "CODEX_THREAD_ID" not in env
    assert "CLAUDECODE" not in env
    assert "CLAUDE_CODE_ENTRYPOINT" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert env["ZF_DOC156_REQUEST_ID"] == "keep-context"
    assert env["OPENAI_API_KEY"] == "keep-provider-credential"
    assert env["CODEX_HOME"] == "/provider/config"


def test_herdr_command_runner_applies_the_sanitized_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_PASSCODE", "do-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "keep-provider-credential")
    environments: list[dict[str, str]] = []

    def fake_run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs["env"]
        assert isinstance(env, dict)
        environments.append(env)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def fake_popen(argv: Sequence[str], **kwargs: object) -> SimpleNamespace:
        del argv
        env = kwargs["env"]
        assert isinstance(env, dict)
        environments.append(env)
        return SimpleNamespace()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    runner = SubprocessCommandRunner()

    runner.run(("herdr", "--version"), timeout=1)
    runner.spawn(("herdr", "--session", "zf-project", "server"))

    assert len(environments) == 2
    assert all("ZF_WEB_PASSCODE" not in env for env in environments)
    assert all(env["OPENAI_API_KEY"] == "keep-provider-credential" for env in environments)


class FakeBackend:
    def __init__(self) -> None:
        self.agents: list[TerminalAgent] = []
        self.created = 0
        self.stopped: list[str] = []
        self.renamed: list[tuple[str, str]] = []
        self.rename_available = True
        self.rename_failure = False
        self.existing: set[str] = set()
        self.bridges: list[dict[str, object]] = []
        self.provider_args: list[tuple[str, ...]] = []

    def probe(self) -> TerminalCapability:
        return TerminalCapability(
            available=True,
            binary="/fake/herdr",
            version="0.8.0",
            schema_available=True,
            observe_bridge=True,
            control_bridge=True,
            tab_rename=self.rename_available,
        )

    def list_agents(self, runtime: HerdrProjectRuntime) -> list[TerminalAgent]:
        return self.agents

    def ensure_project_runtime(self, session_name: str) -> HerdrProjectRuntime:
        return HerdrProjectRuntime(session_name=session_name, server_pid=123)

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
    ) -> HerdrTerminalResource:
        del project_root, label, provider_kind, start_timeout_seconds
        self.provider_args.append(provider_args)
        self.created += 1
        tab_id = f"tab-{self.created}"
        self.existing.add(tab_id)
        return HerdrTerminalResource(
            workspace_id=workspace_id or "workspace-1",
            tab_id=tab_id,
            pane_id=f"pane-{self.created}",
            terminal_id=f"terminal-{self.created}",
            agent_name=agent_name,
        )

    def terminal_exists(self, *, runtime: HerdrProjectRuntime, tab_id: str) -> bool:
        del runtime
        return tab_id in self.existing

    def stop_terminal(self, *, runtime: HerdrProjectRuntime, tab_id: str) -> None:
        del runtime
        self.stopped.append(tab_id)
        self.existing.discard(tab_id)

    def rename_terminal(
        self,
        *,
        runtime: HerdrProjectRuntime,
        tab_id: str,
        title: str,
    ) -> None:
        del runtime
        if self.rename_failure:
            raise TerminalRuntimeError("herdr_rename_failed", "rename rejected", status_code=503)
        self.renamed.append((tab_id, title))

    def bridge_spec(
        self,
        *,
        runtime: HerdrProjectRuntime,
        target: str,
        mode: str,
        takeover: bool,
        cols: int,
        rows: int,
    ) -> TerminalBridgeSpec:
        self.bridges.append(
            {
                "session_name": runtime.session_name,
                "target": target,
                "mode": mode,
                "takeover": takeover,
                "cols": cols,
                "rows": rows,
            }
        )
        return TerminalBridgeSpec(
            argv=(
                "/bin/true",
                runtime.session_name,
                target,
                mode,
                "takeover" if takeover else "normal",
            ),
            mode=mode,
            cols=cols,
            rows=rows,
        )


def _service(tmp_path: Path, backend: FakeBackend | None = None) -> TerminalService:
    state_dir = tmp_path / "runtime-state"
    return TerminalService(
        project_id="project-a",
        project_root=tmp_path,
        state_dir=state_dir,
        config=_config(),
        allowed_providers=("claude-code", "codex", "opencode", "pi"),
        backend=backend or FakeBackend(),
        usage_binding_wait_seconds=0,
    )


def test_web_terminal_config_defaults_are_on(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text('version: "1.0"\nproject:\n  name: demo\n', encoding="utf-8")

    config = load_config(path).runtime.web_terminal

    assert config.enabled is True
    assert config.backend == "herdr"


def test_web_terminal_config_accepts_explicit_opt_out(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text(
        'version: "1.0"\n'
        "project:\n  name: demo\n"
        "runtime:\n  web_terminal:\n    enabled: false\n",
        encoding="utf-8",
    )

    assert load_config(path).runtime.web_terminal.enabled is False


def test_web_terminal_config_loads_typed_limits(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text(
        'version: "1.0"\n'
        "project:\n  name: demo\n"
        "runtime:\n  web_terminal:\n"
        "    enabled: true\n"
        "    herdr_binary: /opt/herdr/bin/herdr\n"
        "    allowed_origins: [https://zaofu.example]\n"
        "    max_sessions: 4\n"
        "    max_frame_bytes: 65536\n"
        "    bridge_queue_bytes: 131072\n"
        "    allow_takeover: false\n",
        encoding="utf-8",
    )

    config = load_config(path).runtime.web_terminal

    assert config.enabled is True
    assert config.herdr_binary == "/opt/herdr/bin/herdr"
    assert config.max_sessions == 4
    assert config.allow_takeover is False


def test_claude_terminal_binds_private_native_session_and_projects_usage(
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    usage = TerminalUsageService(
        state_dir=tmp_path / "runtime-state",
        claude_projects_root=tmp_path / "claude-projects",
        codex_sessions_root=tmp_path / "codex-sessions",
    )
    service = TerminalService(
        project_id="project-a",
        project_root=tmp_path,
        state_dir=tmp_path / "runtime-state",
        config=_config(),
        allowed_providers=("claude-code", "codex"),
        backend=backend,
        usage_service=usage,
        usage_binding_wait_seconds=0,
    )

    record = service.create_session(provider="claude-code", slot="review")
    projection = service.project_session(record)

    assert backend.provider_args == [("--session-id", record.provider_session_id)]
    assert record.provider_session_id
    assert record.usage_binding_status == "bound"
    assert "provider_session_id" not in projection
    assert "provider_session_path" not in projection
    assert projection["usage"]["status"] == "awaiting_usage"


@pytest.mark.parametrize("late_identity", ["consistent", "conflict"])
def test_codex_terminal_late_binds_usage_after_browser_trust_gate(
    tmp_path: Path,
    late_identity: str,
) -> None:
    backend = FakeBackend()
    sessions_root = tmp_path / "codex-sessions"
    usage = TerminalUsageService(
        state_dir=tmp_path / "runtime-state",
        claude_projects_root=tmp_path / "claude-projects",
        codex_sessions_root=sessions_root,
        codex_shell_snapshots_root=tmp_path / "codex-shell-snapshots",
    )
    service = TerminalService(
        project_id="project-a",
        project_root=tmp_path,
        state_dir=tmp_path / "runtime-state",
        config=_config(),
        allowed_providers=("codex",),
        backend=backend,
        usage_service=usage,
        usage_binding_wait_seconds=0,
    )
    record = service.create_session(provider="codex", slot="review")
    assert record.usage_binding_status == "pending"
    session_timestamp = datetime.fromtimestamp(
        record.usage_binding_started_at_ns / 1_000_000_000,
        tz=timezone.utc,
    ).isoformat()
    native_id = "01a01f2e-dfd8-70f2-8283-e1d08dd4bd01"
    transcript = (
        sessions_root / "2026" / "08" / "20" / f"rollout-demo-{native_id}.jsonl"
    )
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "session_meta",
                    "timestamp": session_timestamp,
                    "payload": {
                        "id": native_id,
                        "timestamp": session_timestamp,
                        "cwd": str(tmp_path),
                        "model_provider": "openai",
                    },
                },
                {"type": "turn_context", "payload": {"model": "gpt-5.6-sol"}},
                {
                    "type": "event_msg",
                    "timestamp": "2026-08-20T15:01:02Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 10,
                                "cached_input_tokens": 4,
                                "output_tokens": 2,
                            }
                        },
                    },
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    session = service.list_sessions()["sessions"][0]
    registry = json.loads(
        (service.state_dir / REGISTRY_FILENAME).read_text(encoding="utf-8")
    )

    assert session["usage"]["status"] == "observed"
    assert session["usage"]["total_tokens"] == 12
    assert registry["sessions"][0]["provider_session_id"] == native_id
    assert "provider_session_id" not in session

    usage.settle(service.get_session(record.session_id))
    hidden = transcript.rename(transcript.with_suffix(".hidden"))
    hook_id = (
        native_id
        if late_identity == "consistent"
        else "41850c7c-eff2-4a55-ba87-0ef93ea373e3"
    )
    backend.agents = [
        TerminalAgent(record.terminal_id, record.agent_name, "codex", "idle", hook_id)
    ]
    refreshed = service.list_sessions()["sessions"][0]
    stored = service.get_session(record.session_id)
    if late_identity == "consistent":
        assert stored.provider_session_id == native_id
        assert stored.usage_binding_status == "bound"
        assert refreshed["usage"]["status"] == "awaiting_usage"
        hidden.rename(transcript)
        assert service.list_sessions()["sessions"][0]["usage"]["total_tokens"] == 12
        assert service.get_session(record.session_id).herdr_native_id == native_id
    else:
        assert stored.provider_session_id == ""
        assert stored.usage_binding_status == "conflict"
        assert refreshed["usage"]["status"] == "unavailable"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("    backend: node-pty\n", "runtime.web_terminal.backend"),
        ("    herdr_binary: bin/herdr\n", "herdr_binary"),
        ("    allowed_providers: [codex]\n", "Unknown key"),
        ("    allowed_origins: [https://zaofu.example/path]\n", "allowed_origins"),
        ("    max_sessions: 0\n", "max_sessions"),
        ("    bridge_queue_bytes: 999999999\n", "bridge_queue_bytes"),
        (
            "    max_sessions: 64\n"
            "    max_attachments_per_session: 64\n",
            "aggregate bridge queue budget",
        ),
        ("    unknown: true\n", "Unknown key"),
    ],
)
def test_web_terminal_config_rejects_unsafe_values(
    tmp_path: Path, body: str, message: str
) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text(
        'version: "1.0"\nproject:\n  name: demo\nruntime:\n  web_terminal:\n' + body,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_service_create_is_slot_idempotent_and_registry_contains_no_terminal_bytes(
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    service = _service(tmp_path, backend)
    event_path = service.state_dir / "events.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_text('{"type":"sentinel","bytes":"do-not-touch"}\n', encoding="utf-8")

    first = service.create_session(provider="codex", slot="primary", title="Codex")
    second = service.create_session(provider="codex", slot="primary", title="ignored")

    assert first.session_id == second.session_id
    assert backend.created == 1
    registry = json.loads((service.state_dir / REGISTRY_FILENAME).read_text(encoding="utf-8"))
    assert registry["project_id"] == "project-a"
    serialized = json.dumps(registry)
    assert "terminal.frame" not in serialized
    assert "ansi" not in serialized
    assert event_path.read_text(encoding="utf-8") == '{"type":"sentinel","bytes":"do-not-touch"}\n'


def test_public_projection_hides_backend_resource_identifiers(tmp_path: Path) -> None:
    service = _service(tmp_path)
    record = service.create_session(provider="codex", slot="primary")

    response = service.list_sessions()
    projected = response["sessions"][0]

    assert projected["session_id"] == record.session_id
    assert response["capability"]["binary"] == "herdr"
    assert {
        "project_root",
        "herdr_session",
        "workspace_id",
        "tab_id",
        "pane_id",
        "terminal_id",
        "agent_name",
    }.isdisjoint(projected)


def test_service_concurrent_create_mints_one_resource(tmp_path: Path) -> None:
    backend = FakeBackend()
    service = _service(tmp_path, backend)

    with ThreadPoolExecutor(max_workers=2) as executor:
        records = list(
            executor.map(
                lambda _: service.create_session(provider="claude-code", slot="shared"),
                range(2),
            )
        )

    assert {record.session_id for record in records} == {records[0].session_id}
    assert backend.created == 1


def test_service_stop_is_explicit_and_tab_close_is_idempotent(tmp_path: Path) -> None:
    backend = FakeBackend()
    service = _service(tmp_path, backend)
    record = service.create_session(provider="opencode", slot="primary")

    stopped = service.stop_session(record.session_id)
    stopped_again = service.stop_session(record.session_id)

    assert stopped.state == "stopped"
    assert stopped_again.state == "stopped"
    assert backend.stopped == [record.tab_id]


def test_service_rename_updates_herdr_then_canonical_title(tmp_path: Path) -> None:
    backend = FakeBackend()
    service = _service(tmp_path, backend)
    record = service.create_session(provider="codex", slot="primary", title="Codex 1")

    renamed = service.rename_session(record.session_id, "\x00 Review API\n")

    assert renamed.title == "Review API"
    assert backend.renamed == [(record.tab_id, "Review API")]
    assert service.get_session(record.session_id).title == "Review API"


def test_service_rename_is_atomic_when_herdr_rejects_it(tmp_path: Path) -> None:
    backend = FakeBackend()
    service = _service(tmp_path, backend)
    record = service.create_session(provider="codex", slot="primary", title="Codex 1")
    backend.rename_failure = True

    with pytest.raises(TerminalRuntimeError, match="rename rejected"):
        service.rename_session(record.session_id, "Review API")

    assert service.get_session(record.session_id).title == "Codex 1"


def test_service_rename_requires_capability_and_printable_title(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.rename_available = False
    service = _service(tmp_path, backend)
    record = service.create_session(provider="codex", slot="primary", title="Codex 1")

    with pytest.raises(TerminalRuntimeError) as unavailable:
        service.rename_session(record.session_id, "Review API")

    assert unavailable.value.code == "terminal_rename_unavailable"
    backend.rename_available = True
    service._capability = None
    with pytest.raises(TerminalRuntimeError) as invalid:
        service.rename_session(record.session_id, "\x00\n\t")
    assert invalid.value.code == "invalid_terminal_title"


def test_action_receipt_is_durable_and_contains_no_terminal_content(tmp_path: Path) -> None:
    service = _service(tmp_path)
    record = service.create_session(provider="codex", slot="primary")

    receipt = service.record_action_receipt("takeover", record.session_id)
    registry = json.loads((service.state_dir / REGISTRY_FILENAME).read_text(encoding="utf-8"))

    assert receipt["action"] == "takeover"
    assert registry["receipts"][-1] == receipt
    assert "bytes" not in json.dumps(receipt)


def test_service_reconcile_marks_externally_missing_terminal(tmp_path: Path) -> None:
    backend = FakeBackend()
    service = _service(tmp_path, backend)
    record = service.create_session(provider="pi", slot="primary")
    backend.existing.remove(record.tab_id)

    reconciled = service.reconcile()

    assert reconciled[0].state == "missing"
    assert "not found" in reconciled[0].diagnostics[0]


class FakeRunner:
    def __init__(self, *, terminal_id: str = "term1") -> None:
        self.argv: list[tuple[str, ...]] = []
        self.terminal_id = terminal_id

    def run(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        command = tuple(argv)
        self.argv.append(command)
        if command[-1] == "--version":
            stdout = "herdr 0.8.0\n"
        elif "schema" in command:
            stdout = '{"schema":"ok"}\n'
        elif command[-1] == "--help":
            stdout = "usage\n"
        elif "workspace" in command and "list" in command:
            stdout = '{"result":{"workspaces":[]}}\n'
        elif "workspace" in command and "create" in command:
            stdout = json.dumps(
                {
                    "result": {
                        "workspace": {"workspace_id": "w1"},
                        "tab": {"tab_id": "t1"},
                        "root_pane": {"pane_id": "p1"},
                    }
                }
            )
        elif "agent" in command and "start" in command:
            stdout = json.dumps(
                {"result": {"agent": {"terminal_id": self.terminal_id}}}
            )
        else:
            stdout = '{"result":{}}\n'
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def spawn(self, argv: Sequence[str]):
        raise AssertionError(f"unexpected spawn: {argv}")


@pytest.mark.parametrize("warming_timeout", [False, True])
def test_herdr_adapter_starts_a_project_named_headless_server(warming_timeout: bool) -> None:
    class StartingRunner(FakeRunner):
        def __init__(self) -> None:
            super().__init__()
            self.workspace_lists = 0
            self.spawned: list[tuple[str, ...]] = []

        def run(
            self, argv: Sequence[str], *, timeout: float
        ) -> subprocess.CompletedProcess[str]:
            command = tuple(argv)
            if "workspace" in command and "list" in command:
                del timeout
                self.argv.append(command)
                self.workspace_lists += 1
                if warming_timeout and self.workspace_lists == 2:
                    raise subprocess.TimeoutExpired(command, 2.0)
                return subprocess.CompletedProcess(
                    command,
                    1 if self.workspace_lists == 1 else 0,
                    "" if self.workspace_lists == 1 else '{"result":{"workspaces":[]}}',
                    '{"error":{"code":"server_not_running"}}' if self.workspace_lists == 1 else "",
                )
            return super().run(argv, timeout=timeout)

        def spawn(self, argv: Sequence[str]):
            self.spawned.append(tuple(argv))
            return SimpleNamespace(pid=4321, poll=lambda: None)

    runner = StartingRunner()
    backend = HerdrTerminalBackend("/opt/herdr/bin/herdr", runner=runner)

    runtime = backend.ensure_project_runtime("zf-project")

    assert runtime == HerdrProjectRuntime(session_name="zf-project", server_pid=4321)
    assert runner.spawned == [
        ("/opt/herdr/bin/herdr", "--session", "zf-project", "server")
    ]


def test_herdr_adapter_uses_public_argv_without_shell(tmp_path: Path) -> None:
    runner = FakeRunner()
    backend = HerdrTerminalBackend("/bin/true", runner=runner)

    capability = backend.probe()
    runtime = backend.ensure_project_runtime("zf-project")
    resource = backend.create_terminal(
        runtime=runtime,
        workspace_id="",
        project_root=tmp_path,
        label="Codex",
        agent_name="zfagent1",
        provider_kind="codex",
        provider_args=("--profile", "web-terminal"),
        start_timeout_seconds=60,
    )
    backend.rename_terminal(runtime=runtime, tab_id=resource.tab_id, title="Review API")
    bridge = backend.bridge_spec(
        runtime=runtime,
        target=resource.pane_id,
        mode="control",
        takeover=True,
        cols=120,
        rows=40,
    )

    assert capability.available is True
    assert capability.tab_rename is True
    assert resource == HerdrTerminalResource("w1", "t1", "p1", "term1", "zfagent1")
    assert (
        "/bin/true",
        "--session",
        "zf-project",
        "agent",
        "start",
        "zfagent1",
        "--kind",
        "codex",
        "--pane",
        "p1",
        "--timeout",
        "60000",
        "--",
        "--profile",
        "web-terminal",
    ) in runner.argv
    assert bridge.argv == (
        "/bin/true",
        "--session",
        "zf-project",
        "terminal",
        "session",
        "control",
        "p1",
        "--takeover",
        "--cols",
        "120",
        "--rows",
        "40",
    )
    assert (
        "/bin/true",
        "--session",
        "zf-project",
        "tab",
        "rename",
        "t1",
        "Review API",
    ) in runner.argv
    assert all(isinstance(command, tuple) for command in runner.argv)


def test_herdr_adapter_keeps_startup_blocked_tui_for_browser_onboarding(
    tmp_path: Path,
) -> None:
    class BlockedRunner(FakeRunner):
        def run(
            self, argv: Sequence[str], *, timeout: float
        ) -> subprocess.CompletedProcess[str]:
            command = tuple(argv)
            if "agent" in command and "start" in command:
                del timeout
                self.argv.append(command)
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "",
                    json.dumps({
                        "error": {
                            "code": "agent_not_ready",
                            "message": "startup is blocked",
                        }
                    }),
                )
            if "agent" in command and "get" in command:
                del timeout
                self.argv.append(command)
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({
                        "result": {
                            "agent": {
                                "terminal_id": "term1",
                                "pane_id": "p1",
                                "name": "zfagent1",
                                "agent_status": "blocked",
                            }
                        }
                    }),
                    "",
                )
            return super().run(argv, timeout=timeout)

    runner = BlockedRunner()
    backend = HerdrTerminalBackend("/bin/true", runner=runner)

    resource = backend.create_terminal(
        runtime=HerdrProjectRuntime(session_name="zf-project"),
        workspace_id="",
        project_root=tmp_path,
        label="Claude",
        agent_name="zfagent1",
        provider_kind="claude",
        provider_args=("--session-id", "native-id"),
        start_timeout_seconds=60,
    )

    assert resource.terminal_id == "term1"
    assert any(command[-3:] == ("agent", "get", "zfagent1") for command in runner.argv)
    assert not any("close" in command for command in runner.argv)


def test_herdr_tab_rename_is_an_optional_capability() -> None:
    class NoRenameRunner(FakeRunner):
        def run(
            self, argv: Sequence[str], *, timeout: float
        ) -> subprocess.CompletedProcess[str]:
            command = tuple(argv)
            if command[-3:] == ("tab", "rename", "--help"):
                del timeout
                self.argv.append(command)
                return subprocess.CompletedProcess(command, 1, "", "unsupported")
            return super().run(argv, timeout=timeout)

    capability = HerdrTerminalBackend("/bin/true", runner=NoRenameRunner()).probe()

    assert capability.available is True
    assert capability.tab_rename is False


def test_herdr_adapter_closes_created_tab_when_agent_identity_is_invalid(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(terminal_id="")
    backend = HerdrTerminalBackend("/bin/true", runner=runner)

    with pytest.raises(TerminalRuntimeError, match="omitted terminal identity"):
        backend.create_terminal(
            runtime=HerdrProjectRuntime(session_name="zf-project"),
            workspace_id="",
            project_root=tmp_path,
            label="Codex",
            agent_name="zfagent1",
            provider_kind="codex",
            provider_args=(),
            start_timeout_seconds=60,
        )

    assert (
        "/bin/true",
        "--session",
        "zf-project",
        "tab",
        "close",
        "t1",
    ) in runner.argv


@pytest.mark.asyncio
async def test_real_subprocess_bridge_against_repo_fake_herdr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_binary = Path(__file__).parent / "fixtures" / "fake-herdr"
    monkeypatch.setenv("ZF_FAKE_HERDR_STATE_DIR", str(tmp_path / "fake-herdr"))
    config = _config(herdr_binary=str(fake_binary))
    service = TerminalService(
        project_id="project-a",
        project_root=tmp_path,
        state_dir=tmp_path / "state",
        config=config,
        allowed_providers=("codex",),
        usage_binding_wait_seconds=0,
    )
    record = service.create_session(provider="codex", slot="primary")
    spec = service.bridge_spec(
        record.session_id,
        mode="control",
        takeover=False,
        cols=120,
        rows=40,
    )
    bridge = HerdrNDJSONBridge(spec, config)

    await bridge.start()
    full = await bridge.read_record()
    await bridge.send_command({"type": "terminal.input", "text": "hello"})
    delta = await bridge.read_record()
    await bridge.send_command(
        {
            "type": "terminal.scroll",
            "direction": "up",
            "lines": 3,
            "source": "wheel",
            "column": 12,
            "row": 4,
            "modifiers": 0,
        }
    )
    scroll_delta = await bridge.read_record()
    await bridge.close()

    assert full is not None and full["full"] is True
    assert delta is not None and delta["seq"] > full["seq"]
    assert scroll_delta is not None and scroll_delta["seq"] > delta["seq"]
    assert b"Fake Herdr scroll wheel up 3" in scroll_delta["_decoded_bytes"]
    assert service.stop_session(record.session_id).state == "stopped"


@pytest.mark.asyncio
async def test_bridge_process_applies_the_sanitized_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_PASSCODE", "do-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "keep-provider-credential")
    captured: dict[str, object] = {}

    async def reject_spawn(*argv: str, **kwargs: object) -> None:
        del argv
        captured.update(kwargs)
        raise FileNotFoundError

    monkeypatch.setattr(terminal_gateway.asyncio, "create_subprocess_exec", reject_spawn)
    bridge = HerdrNDJSONBridge(
        TerminalBridgeSpec(("missing-herdr",), "observe", 120, 40),
        _config(),
    )

    with pytest.raises(TerminalRuntimeError, match="binary was not found"):
        await bridge.start()

    env = captured["env"]
    assert isinstance(env, dict)
    assert "ZF_WEB_PASSCODE" not in env
    assert env["OPENAI_API_KEY"] == "keep-provider-credential"


def test_attachment_ticket_is_single_use_and_project_scoped() -> None:
    store = AttachmentTicketStore()
    ticket = store.issue(
        project_id="a",
        session_id="s1",
        mode="observe",
        takeover=False,
        cols=120,
        rows=40,
        ttl_seconds=30,
        max_attachments=2,
    )

    with pytest.raises(TerminalRuntimeError, match="does not match"):
        store.consume(ticket.token, project_id="b", session_id="s1", mode="observe")
    with pytest.raises(TerminalRuntimeError, match="invalid or expired"):
        store.consume(ticket.token, project_id="a", session_id="s1", mode="observe")


@pytest.mark.asyncio
async def test_bridge_requires_full_frame_and_validates_control_input() -> None:
    bridge = HerdrNDJSONBridge(
        TerminalBridgeSpec(("/bin/true",), "control", 120, 40),
        _config(max_input_bytes=1024),
    )
    reader = asyncio.StreamReader()
    reader.feed_data(
        json.dumps(
            {
                "type": "terminal.frame",
                "seq": 1,
                "encoding": "ansi",
                "width": 120,
                "height": 40,
                "full": False,
                "bytes": "G1sySg==",
            }
        ).encode()
        + b"\n"
    )
    bridge.process = SimpleNamespace(stdout=reader)

    with pytest.raises(BridgeProtocolError, match="first.*full"):
        await bridge.read_record()
    with pytest.raises(BridgeProtocolError, match="exactly one"):
        bridge._validate_command(
            {"type": "terminal.input", "text": "a", "bytes": "YQ=="}
        )


def test_bridge_preserves_bounded_herdr_scroll_semantics() -> None:
    bridge = HerdrNDJSONBridge(
        TerminalBridgeSpec(("/bin/true",), "control", 120, 40),
        _config(max_cols=240, max_rows=80),
    )

    assert bridge._validate_command(
        {
            "type": "terminal.scroll",
            "direction": "up",
            "lines": 3,
            "source": "wheel",
            "column": 17,
            "row": 9,
            "modifiers": 15,
        }
    ) == {
        "type": "terminal.scroll",
        "direction": "up",
        "lines": 3,
        "source": "wheel",
        "column": 17,
        "row": 9,
        "modifiers": 15,
    }
    assert bridge._validate_command(
        {
            "type": "terminal.scroll",
            "direction": "down",
            "lines": 39,
            "source": "page_key",
        }
    ) == {
        "type": "terminal.scroll",
        "direction": "down",
        "lines": 39,
        "source": "page_key",
        "modifiers": 0,
    }


@pytest.mark.parametrize(
    "command",
    [
        {"type": "terminal.scroll", "direction": "left", "lines": 1},
        {"type": "terminal.scroll", "direction": "up", "lines": 0},
        {"type": "terminal.scroll", "direction": "up", "lines": 1, "source": "touch"},
        {"type": "terminal.scroll", "direction": "up", "lines": 1, "column": True},
        {"type": "terminal.scroll", "direction": "up", "lines": 1, "column": 240},
        {"type": "terminal.scroll", "direction": "up", "lines": 1, "row": 80},
        {"type": "terminal.scroll", "direction": "up", "lines": 1, "modifiers": 256},
    ],
)
def test_bridge_rejects_invalid_herdr_scroll_semantics(command: dict[str, object]) -> None:
    bridge = HerdrNDJSONBridge(
        TerminalBridgeSpec(("/bin/true",), "control", 120, 40),
        _config(max_cols=240, max_rows=80),
    )

    with pytest.raises(BridgeProtocolError, match="terminal.scroll"):
        bridge._validate_command(command)


@pytest.mark.asyncio
async def test_bridge_rejects_sequence_gaps_after_full_frame() -> None:
    bridge = HerdrNDJSONBridge(
        TerminalBridgeSpec(("/bin/true",), "observe", 120, 40),
        _config(),
    )
    reader = asyncio.StreamReader()
    for seq, full in ((7, True), (9, False)):
        reader.feed_data(
            json.dumps(
                {
                    "type": "terminal.frame",
                    "seq": seq,
                    "encoding": "ansi",
                    "width": 120,
                    "height": 40,
                    "full": full,
                    "bytes": "YQ==",
                }
            ).encode()
            + b"\n"
        )
    bridge.process = SimpleNamespace(stdout=reader)

    assert (await bridge.read_record())["seq"] == 7
    with pytest.raises(BridgeProtocolError, match="not contiguous"):
        await bridge.read_record()


@pytest.mark.asyncio
async def test_slow_websocket_is_closed_instead_of_dropping_terminal_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FastBridge:
        closed = False

        def __init__(self, spec: TerminalBridgeSpec, config: RuntimeWebTerminalConfig) -> None:
            del spec, config
            self.seq = 0

        async def start(self) -> None:
            return None

        async def read_record(self) -> dict[str, object]:
            self.seq += 1
            await asyncio.sleep(0)
            return {
                "type": "terminal.frame",
                "seq": self.seq,
                "encoding": "ansi",
                "width": 120,
                "height": 40,
                "full": self.seq == 1,
                "bytes": "YQ==",
            }

        async def close(self) -> None:
            self.closed = True

    class SlowWebSocket:
        def __init__(self) -> None:
            self.codes: list[int] = []

        async def send_text(self, value: str) -> None:
            del value
            await asyncio.Event().wait()

        async def send_bytes(self, value: bytes) -> None:
            del value
            await asyncio.Event().wait()

        async def receive(self) -> dict[str, object]:
            await asyncio.Event().wait()
            return {}

        async def close(self, *, code: int) -> None:
            self.codes.append(code)

    monkeypatch.setattr(terminal_gateway, "HerdrNDJSONBridge", FastBridge)
    websocket = SlowWebSocket()

    await asyncio.wait_for(
        relay_terminal_websocket(
            websocket,  # type: ignore[arg-type]
            spec=TerminalBridgeSpec(("fake",), "observe", 120, 40),
            config=_config(bridge_queue_frames=4, bridge_queue_bytes=64 * 1024),
        ),
        timeout=1.0,
    )

    assert websocket.codes == [1013]


@pytest.mark.asyncio
async def test_gateway_sends_frame_metadata_then_binary_ansi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrameBridge:
        def __init__(self, spec: TerminalBridgeSpec, config: RuntimeWebTerminalConfig) -> None:
            del spec, config
            self.records = [
                {
                    "type": "terminal.frame",
                    "seq": 1,
                    "encoding": "ansi",
                    "width": 120,
                    "height": 40,
                    "full": True,
                    "bytes": "YQ==",
                },
                {"type": "terminal.closed", "reason": "released"},
            ]

        async def start(self) -> None:
            return None

        async def read_record(self) -> dict[str, object] | None:
            return self.records.pop(0)

        async def close(self) -> None:
            return None

    class Socket:
        def __init__(self) -> None:
            self.text: list[str] = []
            self.binary: list[bytes] = []
            self.codes: list[int] = []

        async def send_text(self, value: str) -> None:
            self.text.append(value)

        async def send_bytes(self, value: bytes) -> None:
            self.binary.append(value)

        async def receive(self) -> dict[str, object]:
            await asyncio.Event().wait()
            return {}

        async def close(self, *, code: int) -> None:
            self.codes.append(code)

    monkeypatch.setattr(terminal_gateway, "HerdrNDJSONBridge", FrameBridge)
    websocket = Socket()

    await relay_terminal_websocket(
        websocket,  # type: ignore[arg-type]
        spec=TerminalBridgeSpec(("fake",), "observe", 120, 40),
        config=_config(),
    )

    assert json.loads(websocket.text[0]) == {
        "type": "terminal.frame",
        "seq": 1,
        "encoding": "ansi",
        "width": 120,
        "height": 40,
        "full": True,
    }
    assert websocket.binary == [b"a"]
    assert json.loads(websocket.text[1]) == {
        "type": "terminal.closed",
        "reason": "released",
    }
    assert websocket.codes == [1000]


@pytest.mark.asyncio
async def test_bridge_start_failure_closes_only_the_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingBridge:
        def __init__(self, spec: TerminalBridgeSpec, config: RuntimeWebTerminalConfig) -> None:
            del spec, config

        async def start(self) -> None:
            raise TerminalRuntimeError("herdr_unavailable", "unavailable", status_code=503)

        async def close(self) -> None:
            return None

    class Socket:
        def __init__(self) -> None:
            self.codes: list[int] = []

        async def close(self, *, code: int) -> None:
            self.codes.append(code)

    monkeypatch.setattr(terminal_gateway, "HerdrNDJSONBridge", FailingBridge)
    websocket = Socket()

    await relay_terminal_websocket(
        websocket,  # type: ignore[arg-type]
        spec=TerminalBridgeSpec(("missing",), "observe", 120, 40),
        config=_config(),
    )

    assert websocket.codes == [1011]


@pytest.mark.parametrize(
    "code", ["server_not_running", "protocol_mismatch", "permission_denied"]
)
def test_herdr_query_errors_do_not_prove_absence(code: str) -> None:
    class ErrorRunner(FakeRunner):
        def run(self, argv, *, timeout):
            return subprocess.CompletedProcess(
                argv, 1, "", json.dumps({"error": {"code": code}})
            )

    backend = HerdrTerminalBackend("/bin/true", runner=ErrorRunner())
    with pytest.raises(TerminalRuntimeError):
        backend.terminal_exists(runtime=HerdrProjectRuntime("zf-project"), tab_id="t1")
    if code != "server_not_running":
        with pytest.raises(TerminalRuntimeError):
            backend.ensure_project_runtime("zf-project")


def test_mouse_commands_require_supported_bridge_and_valid_coordinates() -> None:
    command = {
        "type": "terminal.mouse",
        "action": "down",
        "button": "left",
        "column": 4,
        "row": 2,
    }
    old = HerdrNDJSONBridge(
        TerminalBridgeSpec(("herdr",), "control", 80, 24), _config()
    )
    with pytest.raises(BridgeProtocolError):
        old._validate_command(command)
    current = HerdrNDJSONBridge(
        TerminalBridgeSpec(("herdr",), "control", 80, 24, mouse=True), _config()
    )
    assert current._validate_command(command) == {**command, "modifiers": 0}
    for overrides in (
        {"column": -1},
        {"row": True},
        {"action": "move"},
        {"button": "middle"},
        {"modifiers": 8},
    ):
        with pytest.raises(BridgeProtocolError):
            current._validate_command({**command, **overrides})
