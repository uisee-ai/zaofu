from __future__ import annotations

import json
import subprocess

from zf.runtime.plan_synth_handoff import render_plan_synth_completion_command


def test_plan_synth_completion_command_round_trips_shell_sensitive_json(
    tmp_path,
) -> None:
    payload = {
        "fanout_id": "fanout-plan",
        "stage_id": "plan",
        "child_id": "synth",
        "operation_id": "wop-plan-synth",
        "report": {
            "child_id": "synth",
            "plan_md": (
                "Run python3 -c \"from pathlib import Path; "
                "assert Path('app/result.txt').read_bytes() == b'ok\\n'\"."
            ),
        },
    }

    command = render_plan_synth_completion_command(
        cli_command="uv --project /repo run zf",
        actor="plan-critic",
        state_dir=tmp_path / ".zf",
        payload=payload,
    )

    parsed = subprocess.run(
        ["bash", "-n"],
        input=command,
        text=True,
        capture_output=True,
        check=False,
    )
    assert parsed.returncode == 0, parsed.stderr
    lines = command.splitlines()
    assert "--payload-file -" in lines[0]
    delimiter = lines[0].rsplit("<<'", 1)[1].removesuffix("'")
    assert lines[-1] == delimiter
    assert json.loads("\n".join(lines[1:-1])) == payload
