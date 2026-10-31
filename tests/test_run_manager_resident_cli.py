from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from zf.core.config.schema import RoleConfig
from zf.runtime.run_manager_resident import build_resident_run_manager_briefing


@pytest.mark.parametrize("state_name", ["state", "state 'quoted'"])
def test_resident_commands_execute_bound_cli_with_quoted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_name: str,
) -> None:
    old_bin = tmp_path / "old-bin"
    old_bin.mkdir()
    old_cli = old_bin / "zf"
    old_cli.write_text("#!/bin/sh\nexit 97\n")
    old_cli.chmod(0o755)
    bound_cli = tmp_path / "current harness.py"
    bound_cli.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'state': os.environ['ZF_STATE_DIR']}))\n"
    )
    cli = shlex.join([sys.executable, str(bound_cli), "--launch-bound"])
    monkeypatch.setenv("PATH", str(old_bin))
    monkeypatch.setenv("ZF_CLI_CMD", cli)
    state = tmp_path / state_name
    role = RoleConfig(name="run-manager", instance_id="run-manager")
    briefing = build_resident_run_manager_briefing(
        project_root=tmp_path, state_dir=state, role=role,
    )
    status_line = next(line for line in briefing.splitlines() if line.startswith("1. 运行"))
    status_command = status_line.split("`", 2)[1]
    observation_command = briefing.split("```bash\n", 1)[1].split("\n```", 1)[0]
    for command, verb in ((status_command, "status"), (observation_command, "emit")):
        result = subprocess.run(
            ["/bin/bash", "-c", command], text=True, capture_output=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["state"] == str(state)
        assert payload["argv"][:2] == ["--launch-bound", verb]
        if verb == "emit":
            args = payload["argv"]
            assert args[args.index("--state-dir") + 1] == str(state)
            assert args[args.index("--actor") + 1] == "run-manager"
            assert json.loads(args[args.index("--payload") + 1])["status"] == "watching"
    assert "`zf status`" not in briefing
    assert "`zf emit`" not in briefing
