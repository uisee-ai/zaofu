"""Provider capability must be observed through Codex, not a privileged unshare probe."""
import subprocess

import pytest

from zf.runtime.provider_sandbox import probe_codex_sandbox


@pytest.mark.parametrize("code,error", [(0, ""), (1, "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted")])
def test_current_cli_runs_bounded_workspace_profile(tmp_path, monkeypatch, code, error):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "--help":
            return subprocess.CompletedProcess(argv, 0, "--permission-profile --include-managed-config", "")
        assert kwargs["timeout"] == 20
        return subprocess.CompletedProcess(argv, code, "", error)
    monkeypatch.setattr("zf.runtime.provider_sandbox.subprocess.run", run)
    result = probe_codex_sandbox(tmp_path, codex="/bin/codex")
    assert result["returncode"] == code and result["stderr"] == error
    assert result["phase"] == "launch"
    assert len(calls) == 2
    assert calls[-1] == ["/bin/codex", "sandbox", "--permission-profile", ":workspace",
                        "--include-managed-config", "-C", str(tmp_path), "--", "/usr/bin/true"]
    assert not any("bypass" in a or "unshare" in a for argv in calls for a in argv)


@pytest.mark.parametrize("help_text", ["unknown interface", "--permission-profile"])
def test_unknown_cli_is_not_a_successful_sandbox_probe(tmp_path, monkeypatch, help_text):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, help_text, "")
    monkeypatch.setattr("zf.runtime.provider_sandbox.subprocess.run", run)
    result = probe_codex_sandbox(tmp_path)
    assert result["returncode"] != 0 and result["phase"] == "interface"
    assert len(calls) == 1


def test_timeout_stops_before_launch_and_preserves_diagnostic(tmp_path, monkeypatch):
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 20, stderr=b"stalled help")
    monkeypatch.setattr("zf.runtime.provider_sandbox.subprocess.run", run)
    result = probe_codex_sandbox(tmp_path)
    assert result["returncode"] == 124 and "stalled help" in result["stderr"]


def test_configured_probe_checks_shared_substrate_once_for_restricted_roles(tmp_path, monkeypatch):
    from zf.core.config.schema import ProviderSessionConfig, RoleConfig, ZfConfig
    from zf.runtime.provider_sandbox import probe_configured_codex_sandbox

    config = ZfConfig(roles=[
        RoleConfig(name=name, backend="codex", provider_session=ProviderSessionConfig(sandbox="workspace-write"))
        for name in ("dev", "verify")
    ] + [RoleConfig(name="legacy", backend="codex"), RoleConfig(name="test", backend="mock")])
    calls = []
    def probe(root):
        calls.append(root)
        return {"returncode": 0, "phase": "launch", "stderr": "", "stdout": ""}
    monkeypatch.setattr("zf.runtime.provider_sandbox.probe_codex_sandbox", probe)

    result = probe_configured_codex_sandbox(config, tmp_path)
    assert result["roles"] == ["dev", "verify"]
    assert result["probe"]["returncode"] == 0
    assert calls == [tmp_path]


def test_configured_probe_preserves_unrestricted_and_mock_policies(tmp_path, monkeypatch):
    from zf.core.config.schema import RoleConfig, ZfConfig
    from zf.runtime.provider_sandbox import probe_configured_codex_sandbox

    def unexpected(_root):
        pytest.fail("unrestricted or mock configuration must not launch a sandbox probe")
    monkeypatch.setattr("zf.runtime.provider_sandbox.probe_codex_sandbox", unexpected)
    config = ZfConfig(roles=[RoleConfig(name="legacy", backend="codex"), RoleConfig(name="test", backend="mock")])
    assert probe_configured_codex_sandbox(config, tmp_path) is None
