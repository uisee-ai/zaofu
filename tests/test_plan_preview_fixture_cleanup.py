"""Explicit host tier: real Uvicorn SIGTERM closes its isolated fixture."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.host
def test_sigterm_records_simulation_completion_and_removes_owned_fixture(tmp_path):
    receipt = tmp_path / "receipt.json"
    env = {**os.environ, "ZF_PREVIEW_FIXTURE_RECEIPT": str(receipt)}
    with (tmp_path / "server.log").open("w") as output:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "tests.plan_preview_app:create_test_app",
            "--factory", "--host", "127.0.0.1", "--port", "0", "--no-access-log"],
            env=env, stdout=output, stderr=output, start_new_session=True)
        try:
            deadline = time.monotonic() + 25
            while not receipt.exists() and time.monotonic() < deadline:
                assert process.poll() is None, (tmp_path / "server.log").read_text()
                time.sleep(.05)
            started = json.loads(receipt.read_text())
            assert started["status"] == "started"
            root = Path(started["fixture_root"])
            assert root.exists()
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=15)
            result = json.loads(receipt.read_text())
            assert result["status"] == "cleaned" and result["simulation_done_event_id"]
            assert result["fixture_removed"] and not root.exists()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)
