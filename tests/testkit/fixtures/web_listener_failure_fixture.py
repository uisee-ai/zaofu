"""Opt-in inner pytest intentionally dies with a live API; never a green case."""

import os
from pathlib import Path
import signal
import time

import pytest

from tests.conformance.test_zaofu_testkit_s4_web_listener_host import _project, _start_api, _wait_ready
from tests.testkit.evidence import write_json_atomic


def test_api_owner_dies_without_finally():
    raw = os.environ.get("ZF_LISTENER_FAILURE_ROOT")
    if not raw:
        pytest.skip("requires explicit outer cleanup fixture")
    root = Path(raw)
    project, _ = _project(root)
    output = root / "api"
    output.mkdir()
    api, port = _start_api(project, output)
    _wait_ready(api, port)
    write_json_atomic(root / "api-owner.json", {
        "pid": os.getpid(), "api_pid": api.pid, "port": port, "pgid": os.getpgid(os.getpid()),
    })
    if os.environ["ZF_LISTENER_FAILURE_MODE"] == "runner-sigkill":
        os.kill(os.getpid(), signal.SIGKILL)
    time.sleep(60)
    pytest.fail("outer timeout did not stop the deliberately hung owner")
