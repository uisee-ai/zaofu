"""Safety checks for the isolated Inbox browser fixture."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "e2e" / "inbox_browser_fixture.py"


@pytest.mark.parametrize(
    ("port", "message"),
    [(8001, "port >= 8002"), (8002, "fixture root already exists")],
)
def test_fixture_refuses_reserved_port_and_existing_state(port: int, message: str) -> None:
    with TemporaryDirectory(prefix="zf-inbox-fixture-", dir="/tmp") as directory:
        root = Path(directory)
        marker = root / "existing-data"
        marker.write_text("must remain unchanged", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(FIXTURE), "--scenario", "plan", "--root", str(root), "--port", str(port)],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}"},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode != 0
        assert message in result.stderr
        assert marker.read_text(encoding="utf-8") == "must remain unchanged"
        assert not (root / ".zf").exists()
