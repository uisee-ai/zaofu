"""Restricted provider startup fails before transport/model calls on a broken host."""
from pathlib import Path

import pytest
import yaml

from zf.cli.main import main
from zf.core.events.log import EventLog


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "sandbox-preflight", "state_dir": ".state"},
        "session": {"tmux_session": "test-sandbox-preflight"},
        "orchestrator": {"backend": "mock"},
        "roles": [{"name": "dev", "backend": "mock"}],
    }
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump(config))
    assert main(["init"]) == 0
    config["roles"] = [{"name": "dev", "backend": "codex",
                        "provider_session": {"sandbox": "workspace-write"}}]
    path.write_text(yaml.safe_dump(config))
    return tmp_path


@pytest.mark.parametrize("flags", [[], ["--no-watch"], ["--skip-workflow-inspect"],
                                  ["--control-plane-only", "--skip-workflow-inspect"]])
def test_start_rejects_broken_sandbox_before_provider_or_transport(project, monkeypatch, capsys, flags):
    def unexpected(*_args, **_kwargs):
        pytest.fail("sandbox failure must stop before transport or model launch")
    monkeypatch.setattr("zf.cli.start.make_transport", unexpected)
    monkeypatch.setattr("zf.runtime.provider_sandbox.probe_codex_sandbox", lambda root, **kwargs: {
        "phase": "launch", "command": ["codex", "sandbox", "--", "/usr/bin/true"],
        "returncode": 1, "stdout": "", "stderr": "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted",
    })

    assert main(["start", *flags]) == 1
    assert "zf doctor provider" in capsys.readouterr().err
    events = EventLog(project / ".state/events.jsonl").read_all()
    failures = [event for event in events if event.type == "env.preflight.failed"]
    assert len(failures) == 1
    assert failures[0].payload["check"] == "codex_sandbox"
    assert failures[0].payload["hard"] is True
    assert failures[0].payload["roles"] == ["dev"]
    assert failures[0].payload["probe"]["returncode"] == 1
    assert not any(event.type in {"provider.session.config.resolved", "loop.started"} for event in events)


def test_start_rejects_worker_with_invisible_bound_input(project, monkeypatch):
    import subprocess

    def masked_view(argv, **kwargs):
        if argv[-1] == "--help":
            return subprocess.CompletedProcess(argv, 0, "--permission-profile --include-managed-config", "")
        assert argv[-2] == "/bin/cat" and Path(argv[-1]).is_file()
        return subprocess.CompletedProcess(argv, 1, "", "No such file or directory")

    monkeypatch.setattr("zf.runtime.provider_sandbox.subprocess.run", masked_view)
    monkeypatch.setattr("zf.cli.start.make_transport", lambda *a, **kw: pytest.fail("worker started"))
    assert main(["start", "--skip-workflow-inspect"]) == 1
    failures = [e for e in EventLog(project / ".state/events.jsonl").read_all()
                if e.type == "env.preflight.failed"]
    assert len(failures) == 1
    assert failures[0].payload["probe"]["phase"] == "bound_input"
    assert str(project / ".state") in failures[0].payload["detail"]


def test_dry_run_does_not_probe_host_sandbox(project, monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("dry run must not probe host sandbox")
    monkeypatch.setattr("zf.runtime.provider_sandbox.probe_codex_sandbox", unexpected)
    assert main(["start", "--dry-run", "--skip-workflow-inspect"]) == 0
