from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_standalone_issue_core_host_runs_without_zaofu(tmp_path) -> None:
    store = tmp_path / "sessions"
    source_root = Path(__file__).parents[1] / "src"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "issue_core",
            "--store",
            str(store),
            "--report",
            "Refresh fails.\n\nReproduction:\n1. Open page\n2. Click Refresh\n\nExpected:\nPage updates",
        ],
        cwd=str(tmp_path),
        env={"PYTHONPATH": str(source_root)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["session_id"].startswith("issue-session-")
    assert payload["status"] in {"ready", "ask_user"}


def test_standalone_host_can_resume_and_confirm_without_runtime(tmp_path) -> None:
    store = tmp_path / "sessions"
    source_root = Path(__file__).parents[1] / "src"
    env = {"PYTHONPATH": str(source_root)}
    first = subprocess.run(
        [
            sys.executable, "-m", "issue_core", "--store", str(store),
            "--report", "Refresh fails.\n\nReproduction:\n1. Click Refresh\n\nExpected:\nPage updates",
        ],
        cwd=str(tmp_path), env=env, capture_output=True, text=True,
    )
    assert first.returncode == 0, first.stderr
    created = json.loads(first.stdout)
    assert created["status"] == "ready"
    confirmed = subprocess.run(
        [
            sys.executable, "-m", "issue_core", "--store", str(store),
            "--session-id", created["session_id"], "--confirm",
        ],
        cwd=str(tmp_path), env=env, capture_output=True, text=True,
    )
    assert confirmed.returncode == 0, confirmed.stderr
    payload = json.loads(confirmed.stdout)
    assert payload["status"] == "completed"
    assert payload["publication_snapshot_ref"].startswith("sha256:")
