from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from zf.runtime.process_guard import SingleOwnerProcessGuard
from zf.runtime.source_repair_restart_actuator import _recorded_watcher_pid, main


def test_restart_actuator_stops_old_watcher_then_execs_control_plane_only(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state_dir = project / ".zf"
    state_dir.mkdir(parents=True)
    guard = SingleOwnerProcessGuard(
        state_dir / "processes" / "watcher.pid.json", owner_pid=1234, component="watcher",
    )
    assert guard.acquire().acquired

    with patch(
        "zf.runtime.source_repair_restart_actuator._looks_like_watcher",
        side_effect=lambda pid: pid == 1234,
    ), patch(
        "zf.runtime.source_repair_restart_actuator._terminate_watcher",
    ) as terminate, patch(
        "zf.runtime.source_repair_restart_actuator.zf_cli_cmd",
        return_value="uv run zf",
    ), patch(
        "zf.runtime.source_repair_restart_actuator.os.execvp",
        side_effect=RuntimeError("exec captured"),
    ) as execvp, patch(
        "zf.runtime.source_repair_restart_actuator.time.sleep",
    ), patch(
        "zf.runtime.source_repair_restart_actuator.os.chdir",
    ):
        with pytest.raises(RuntimeError, match="exec captured"):
            main([
                "--project-root",
                str(project),
                "--state-dir",
                str(state_dir),
            ])

    terminate.assert_called_once_with(1234)
    assert execvp.call_args.args == (
        "uv",
        ["uv", "run", "zf", "start", "--control-plane-only"],
    )


@pytest.mark.parametrize("guard,looks_like,expected", [
    ({"pid": 1234}, True, 1234),
    ({"owner_pid": 1234, "pid": 5678}, True, 1234),
    ({"owner_pid": 1234}, False, 0),
    ({"owner_pid": "invalid"}, True, 0),
])
def test_restart_actuator_retains_legacy_and_process_identity_fences(tmp_path, guard, looks_like, expected):
    path = tmp_path / "processes" / "watcher.pid.json"
    path.parent.mkdir()
    path.write_text(json.dumps(guard))
    with patch("zf.runtime.source_repair_restart_actuator._looks_like_watcher", return_value=looks_like):
        assert _recorded_watcher_pid(tmp_path) == expected
