"""Startup success must not hide missing task execution capabilities."""
import json
import subprocess

import pytest

from zf.cli.main import main
from zf.runtime.provider_task_capabilities import probe_task_capabilities


def test_probes_keep_workspace_policy_and_collect_each_failure(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["timeout"] == 20
        assert kwargs["cwd"] == tmp_path
        return subprocess.CompletedProcess(argv, 1, "", "operation not permitted")

    monkeypatch.setattr("zf.runtime.provider_task_capabilities.subprocess.run", run)
    result = probe_task_capabilities(tmp_path, ["local-listen", "docker", "git-metadata"])
    assert [r["capability"] for r in result] == ["local-listen", "docker", "git-metadata"]
    assert all(r["status"] == "blocked" for r in result)
    assert all(argv[:5] == ["codex", "sandbox", "--permission-profile", ":workspace",
                           "--include-managed-config"] for argv in calls)
    assert "--privileged" not in str(calls) and "danger-full-access" not in str(calls)
    assert "image" in calls[1] and "inspect" in calls[1] and "run" not in calls[1]
    assert calls[1][calls[1].index("--host") + 1] == "unix:///var/run/docker.sock"


def test_capability_timeout_does_not_prevent_other_diagnostics(tmp_path, monkeypatch):
    def run(argv, **kwargs):
        if "docker" in argv:
            raise subprocess.TimeoutExpired(argv, 20)
        return subprocess.CompletedProcess(argv, 0, "bound loopback", "")

    monkeypatch.setattr("zf.runtime.provider_task_capabilities.subprocess.run", run)
    result = probe_task_capabilities(tmp_path, ["docker", "local-listen"])
    assert result[0]["returncode"] == 124 and result[0]["status"] == "blocked"
    assert result[1]["status"] == "observed"
    assert result[1]["qualification"] == "loopback_bind_only"


def test_unknown_capability_never_executes(tmp_path, monkeypatch):
    monkeypatch.setattr("zf.runtime.provider_task_capabilities.subprocess.run",
                        lambda *a, **k: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="unsupported capability"):
        probe_task_capabilities(tmp_path, ["arbitrary-command"])


def test_doctor_capability_failure_is_not_overridden_by_startup_success(tmp_path, monkeypatch, capsys):
    from zf.cli import doctor

    monkeypatch.setattr(doctor, "_codex_provider_preflight", lambda: {
        "ok": True, "codex": {"path": "codex"}, "issues": [],
        "sandbox": {"status": "ok", "qualification": "command_startup_only"},
    })
    calls = []

    def probe(root, capabilities, **kwargs):
        calls.append((root, capabilities))
        return [{"capability": "docker", "status": "blocked", "returncode": 1}]

    monkeypatch.setattr("zf.runtime.provider_task_capabilities.probe_task_capabilities", probe)
    assert main(["doctor", "provider", "--workdir", str(tmp_path),
                 "--capability", "docker", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert calls == [(tmp_path, ["docker"])]
    assert result["sandbox"]["status"] == "ok" and not result["ok"]
    assert result["capabilities"][0]["status"] == "blocked"
