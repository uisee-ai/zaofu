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
