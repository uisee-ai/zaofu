from tests.continuity_provider_command import provider_command


def test_qualification_keeps_sandbox_and_scopes_additional_writes(tmp_path):
    root, state = tmp_path / "product", tmp_path / "isolated-state"
    command = provider_command(root, state, "test prompt")
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert command.count("--add-dir") == 1
    assert command[command.index("--add-dir") + 1] == str(state.resolve())
    assert command[command.index("-C") + 1] == str(root.resolve())
    assert "--ignore-user-config" not in command
    assert not any("bypass" in argument or "danger-full-access" in argument for argument in command)


def test_sandbox_preflight_uses_current_cli_and_preserves_failed_evidence(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    import pytest
    from tests.continuity_provider_command import require_provider_sandbox
    calls = []
    def fail(command, **kwargs):
        calls.append(command)
        if command[-1] == "--help":
            return SimpleNamespace(returncode=0, stdout="--permission-profile --include-managed-config", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted")
    monkeypatch.setattr("zf.runtime.provider_sandbox.subprocess.run", fail)
    with pytest.raises(RuntimeError, match="sandbox preflight failed"):
        require_provider_sandbox(tmp_path, tmp_path / "state", tmp_path / "evidence")
    receipt = json.loads((tmp_path / "evidence/sandbox-preflight.json").read_text())
    assert receipt["returncode"] == 1 and "RTM_NEWADDR" in receipt["stderr"]
    assert len(calls) == 2 and calls[-1][:2] == ["codex", "sandbox"]
    assert "linux" not in calls[-1] and calls[-1][-2:] == ["--", "/usr/bin/true"]
    assert calls[-1][calls[-1].index("--permission-profile") + 1] == ":workspace"
    assert not any("bypass" in arg or "danger-full-access" in arg for arg in calls[-1])


def test_sandbox_preflight_success_is_not_real_provider_qualification(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from tests.continuity_provider_command import require_provider_sandbox
    monkeypatch.setattr("zf.runtime.provider_sandbox.subprocess.run", lambda *a, **kw:
        SimpleNamespace(returncode=0, stdout="--permission-profile --include-managed-config", stderr=""))
    evidence = tmp_path / "evidence"
    require_provider_sandbox(tmp_path, tmp_path / "state", evidence)
    assert json.loads((evidence / "sandbox-preflight.json").read_text())["returncode"] == 0
    assert not (evidence / "qualification.json").exists()
