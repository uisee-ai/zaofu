"""S7 matrix cases for untrusted issue input and dangerous sinks."""

from __future__ import annotations

import json
import subprocess
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.cli.feishu import _handle_event_data
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.config.schema import ProjectConfig, ZfConfig
from tests.test_external_issue_ingress import _mirror, _service
from zf.core.events import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.integrations.feishu.gateway import AuthLevel
from zf.integrations.feishu.lark_cli import LarkCliRunner
from zf.core.events import EventWriter
from zf.runtime.agent_session_output import read_agent_output_artifact
from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter
from zf.integrations.forge.base import IssuePublishRequest
from zf.integrations.forge.github import GitHubComProvider
from zf.integrations.forge.gitlab import GitLabComProvider
from zf.runtime.openclaw_provider import OpenClawGatewayClient
from zf.runtime.control_actions import ControlledActionService

from tests.test_openclaw_gateway_client import _ControlSocketServer, _binding


class _ProviderText:
    type = "text"

    def __init__(self, content: str) -> None:
        self.content = content


@pytest.mark.parametrize(
    "body",
    [
        "$(touch {sentinel}) && git push --force origin main",
        "../../events.jsonl\n\"; rm -rf {sentinel}; #",
        "<img src=x onerror=fetch('https://sink.invalid')>",
    ],
    ids=["shell-and-push", "path-and-delete", "browser-payload"],
)
def test_external_issue_body_stays_untrusted_data_and_never_reaches_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    """Ingress persists a body as evidence; it must not interpret its text."""
    sentinel = tmp_path / "must-not-be-created"
    mirror = _mirror(created_at="2026-09-01T00:00:00+00:00")
    service, _ = _service(tmp_path, mirror)
    service.mirrors.upsert(mirror, body.format(sentinel=sentinel))
    # The ingress normally records the current source commit with git.  Keep
    # that legitimate identity read deterministic so this matrix can forbid
    # every subprocess invocation from the untrusted body path itself.
    monkeypatch.setattr(service, "_canonical_source_revision", lambda: "a" * 40)
    calls: list[tuple[object, ...]] = []
    real_run = subprocess.run

    def observed_subprocess(*args: object, **kwargs: object) -> object:
        command = args[0] if args else kwargs.get("args")
        calls.append(tuple(command) if isinstance(command, (list, tuple)) else (command,))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(
        "zf.runtime.external_issue_ingress.subprocess",
        SimpleNamespace(run=observed_subprocess, SubprocessError=subprocess.SubprocessError),
    )

    result = service.poll_once()

    assert result.ok is True
    flattened = " ".join(str(part) for call in calls for part in call)
    assert "touch" not in flattened
    assert "git push" not in flattened
    assert "rm -rf" not in flattened
    assert not sentinel.exists()
    current = service.mirrors.get(mirror.number)
    assert current is not None
    assert service.mirrors.read_body(current) == body.format(sentinel=sentinel)
    events = EventLog(service.state_dir / "events.jsonl").read_all()
    received = [event for event in events if event.type == "external_issue.received"]
    assert len(received) == 1
    source_ref = received[0].payload["source_ref"]
    source = (service.state_dir / source_ref).resolve()
    assert source.is_relative_to((service.state_dir / "artifacts").resolve())
    assert json.loads(source.read_text(encoding="utf-8"))["trust"] == (
        "untrusted_external_input"
    )
    assert not any(event.type in {"shell.executed", "git.pushed"} for event in events)


def test_feishu_command_text_is_untrusted_data_and_never_reaches_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An authorized Feishu note must not interpret command-like text."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n  name: s7-feishu\n  state_dir: .zf\n"
        "roles:\n  - name: dev\n    backend: mock\n"
        "integrations:\n  feishu_identity:\n"
        "    enabled: true\n    users:\n"
        "      ou_operator:\n        operator: alice\n        level: operator\n",
        encoding="utf-8",
    )
    assert main(["init"]) == 0
    context = resolve_project_context()
    sentinel = tmp_path / "must-not-be-created"
    text = f"$(touch {sentinel}) && git push --force origin main"
    result = _handle_event_data(
        {
            "type": "button_action",
            "payload": {"action": f"note:{text}", "message_id": "s7-feishu-1"},
            "user_id": "ou_operator",
            "chat_id": "s7-chat",
        },
        context=context,
        user_levels={"ou_operator": AuthLevel.OPERATOR},
    )

    assert result["status"] == "completed"
    assert not sentinel.exists()
    events = EventLog(context.state_dir / "events.jsonl").read_all()
    notes = [event for event in events if event.type == "human.note"]
    assert len(notes) == 1
    assert notes[0].payload["text"] == text
    assert not any(event.type in {"shell.executed", "git.pushed"} for event in events)


def test_provider_output_is_stored_as_evidence_and_never_executed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider text is an evidence payload, not a command interpreter input."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    sentinel = tmp_path / "provider-must-not-execute"
    command_like = f"$(touch {sentinel}) && git push --force origin main\n"
    # Force the raw-output contract to create a sidecar, exercising the same
    # path used for large provider transcripts rather than only a short event.
    provider_text = command_like * 128
    monkeypatch.setenv("ZF_AGENT_SESSION_RAW_OUTPUT_THRESHOLD_BYTES", "64")
    log = EventLog(state_dir / "events.jsonl")
    emitter = AgentSessionStreamEmitter(
        writer=EventWriter(log),
        identity=AgentSessionIdentity(
            run_id="run-s7-provider",
            thread_id="thread-s7-provider",
            source="provider",
            actor="provider",
            task_id="TASK-S7-PROVIDER",
        ),
        flush_interval_s=0.0,
    )

    emitter.start()
    emitter.emit_message(_ProviderText(provider_text))
    completed = emitter.complete()

    assert not sentinel.exists()
    ref = (completed.payload.get("refs") or {}).get("raw_output")
    assert isinstance(ref, dict)
    artifact = read_agent_output_artifact(state_dir, str(ref["raw_ref"]))
    assert artifact["content"] == provider_text
    events = log.read_all()
    assert not any(event.type in {"shell.executed", "git.pushed"} for event in events)


def test_openclaw_prompt_stays_remote_message_data_and_never_executes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Command-like channel text is transported as data, never local code."""
    sentinel = tmp_path / "openclaw-command-must-not-run"
    command_like = f"$(touch {sentinel}) && git push --force origin main"
    server = _ControlSocketServer(reply="remote-safe")
    server.start()
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "test-token")
    try:
        result = OpenClawGatewayClient().run_turn(
            _binding(server.base_url),
            agent_id="zaofu_s7_untrusted",
            prompt=command_like,
            system_prompt="system boundary",
            timeout_seconds=5.0,
            metadata={"request_id": "s7-untrusted-openclaw"},
        )
    finally:
        server.close()

    assert result.ok is True
    assert result.reply == "remote-safe"
    chat = next(item for item in server.seen if item.get("method") == "chat.send")
    assert command_like in chat["params"]["message"]
    assert not sentinel.exists()


def test_untrusted_issue_attachment_urls_cannot_trigger_network_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the documented GitHub image host may reach the attachment sink."""
    mirror = _mirror(created_at="2026-09-01T00:00:00+00:00")
    service, _ = _service(tmp_path, mirror)
    body = (
        "![internal](https://127.0.0.1:8443/private.png) "
        "![evil](https://evil.example/secret.png) "
        "![github-path](https://github.com/org/repo/assets/secret.png)"
    )
    service.mirrors.upsert(mirror, body)
    calls: list[tuple[object, ...]] = []

    def forbidden_urlopen(*args: object, **kwargs: object) -> object:
        calls.append(args)
        raise AssertionError("untrusted attachment URL reached urlopen")

    monkeypatch.setattr("urllib.request.urlopen", forbidden_urlopen)

    result = service.poll_once()

    assert result.ok is True
    assert calls == []
    events = EventLog(service.state_dir / "events.jsonl").read_all()
    received = [event for event in events if event.type == "external_issue.received"]
    assert len(received) == 1
    assert not [
        ref for ref in received[0].payload.get("artifact_refs", [])
        if isinstance(ref, dict) and ref.get("kind") == "external_issue_attachment"
    ]


@pytest.mark.parametrize("provider_kind", ["github", "gitlab"])
def test_untrusted_forge_project_and_issue_text_stay_on_quoted_remote_sink(
    provider_kind: str,
) -> None:
    """Forge adapters must quote project paths and never execute issue text."""
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def transport(method: str, url: str, headers: dict[str, str], body: bytes | None):
        calls.append((method, url, headers, body))
        if provider_kind == "github":
            return 201, json.dumps({
                "number": 7,
                "html_url": "https://github.com/owner/repo/issues/7",
            }).encode()
        return 201, json.dumps({
            "iid": 7,
            "web_url": "https://gitlab.com/owner/repo/-/issues/7",
        }).encode()

    provider = (
        GitHubComProvider(transport)
        if provider_kind == "github"
        else GitLabComProvider(transport)
    )
    malicious = "$(touch /tmp/forge-sink) && git push --force"
    result = provider.publish(
        IssuePublishRequest(
            project="owner/repo",
            title=malicious,
            body=malicious,
            labels=(),
            marker="m",
        ),
        access_token="secret-token",
    )

    assert result.status == "published"
    assert calls
    method, url, headers, body = calls[0]
    assert method == "POST"
    assert url.startswith(
        "https://api.github.com/" if provider_kind == "github" else "https://gitlab.com/"
    )
    assert "owner/repo" in url or "owner%2Frepo" in url
    assert body is not None
    if provider_kind == "github":
        assert malicious.encode() in body
    else:
        decoded = urllib.parse.parse_qs(body.decode("utf-8"))
        assert decoded["title"] == [malicious]
        assert decoded["description"] == [malicious]
    assert b"secret-token" not in body
    assert headers["Authorization"] == "Bearer secret-token"


def test_github_path_traversal_project_is_rejected_before_remote_sink() -> None:
    """GitHub's owner/repository identity rejects path traversal locally."""
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def transport(method: str, url: str, headers: dict[str, str], body: bytes | None):
        calls.append((method, url, headers, body))
        raise AssertionError("malformed project reached the remote transport")

    with pytest.raises(ValueError, match="owner/repository"):
        GitHubComProvider(transport).publish(
            IssuePublishRequest(
                project="owner/../repo",
                title="untrusted",
                body="untrusted",
                labels=(),
                marker="m",
            ),
            access_token="secret-token",
        )
    assert calls == []


def test_untrusted_lark_command_is_rejected_before_subprocess_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Feishu CLI adapter passes free-form text as one argv value."""
    runner = LarkCliRunner(executable="/bin/true", check_version=False)
    calls: list[tuple[object, dict[str, object]]] = []

    def observed_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args[0] if args else None, kwargs))
        return subprocess.CompletedProcess(args[0] if args else [], 0, "{}", "")

    monkeypatch.setattr("zf.integrations.feishu.lark_cli.subprocess.run", observed_run)
    malicious = "$(touch /tmp/lark-sink) && git push --force"
    result = runner.run(("base", "+record-search", malicious))

    assert result.payload == {}
    assert calls
    argv = calls[0][0]
    assert isinstance(argv, list)
    assert malicious in argv
    assert calls[0][1].get("shell") is not True


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-021: regression case_id must be confined below the regression "
        "artifact root; untrusted ../ values must not escape the sink"
    ),
)
def test_regression_case_id_cannot_escape_artifact_root(tmp_path: Path) -> None:
    """Controlled capture must reject path traversal in an untrusted case id."""

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    service = ControlledActionService(
        state_dir,
        EventWriter(log),
        config=ZfConfig(project=ProjectConfig(name="s7-path")),
    )
    requested = ZfEvent(
        type="control.action.requested",
        actor="web",
        payload={"task_id": "TASK-S7-PATH"},
    )

    for case_id in ("../escaped-case", str(tmp_path / "absolute-case")):
        service._execute_action(
            requested=requested,
            action="capture-regression-case",
            requested_action="capture-regression-case",
            payload={
                "task_id": "TASK-S7-PATH",
                "case_id": case_id,
                "assertions": ["scope_violation==0"],
            },
        )

    artifact_root = state_dir / "artifacts" / "regression"
    assert not (state_dir / "artifacts" / "escaped-case.json").exists()
    assert not (tmp_path / "absolute-case.json").exists()
    assert artifact_root.exists()
