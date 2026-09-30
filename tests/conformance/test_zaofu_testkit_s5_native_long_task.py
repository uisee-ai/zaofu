"""Explicit real Claude long-tool checkpoint recovery; not OS crash/long thinking."""

import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

import pytest

from tests.conformance.test_zaofu_testkit_s5_native_cli_lifecycle import _exercise_native_cli_lifecycle
from tests.testkit.native_long_task import NativeLongTask


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_native_long_tool_cli_recovery_preserves_completed_chunk(monkeypatch, record_property):
    if os.environ.get("ZF_RUN_NATIVE_LONG_TASK") != "1":
        pytest.skip("set ZF_RUN_NATIVE_LONG_TASK=1 for three-minute native CPU workload")
    workload = NativeLongTask()
    candidate = os.environ.get("ZF_CLAUDE_EXECUTABLE") or shutil.which("claude")
    if not candidate or not Path(candidate).is_file():
        pytest.skip("native Claude executable is unavailable")
    assert Path(candidate).is_absolute() and os.access(candidate, os.X_OK)
    with TemporaryDirectory(prefix="zf-k553-claude-bin-") as bindir:
        link = Path(bindir) / "claude"
        # Enforce child-only updater opt-out even with an existing tmux server;
        # no global install, symlink, config or server environment changes.
        link.write_text(f"#!{sys.executable}\nimport os,sys\n"
                        "os.environ['DISABLE_AUTOUPDATER']='1'\n"
                        f"os.execv({candidate!r}, [{candidate!r}, *sys.argv[1:]])\n")
        link.chmod(0o700)
        monkeypatch.setenv("PATH", f"{bindir}:{os.environ.get('PATH', '')}")
        monkeypatch.setenv("DISABLE_AUTOUPDATER", "1")
        try:
            _exercise_native_cli_lifecycle(monkeypatch, record_property, stage="impl", workload=workload)
        finally:
            record_property("native_long_task", json.dumps(workload.evidence))
