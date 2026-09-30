"""S1 process-level checks for every named TestKit crash point."""

from __future__ import annotations

from pathlib import Path
import os
import sys

import pytest

from tests.testkit.cleanup import assert_no_child_process_leaks, child_processes
from tests.testkit.command_runner import run_command
from tests.testkit.failpoints import COMMIT_POINTS


@pytest.mark.parametrize("point", COMMIT_POINTS)
def test_each_named_failpoint_is_observable_in_a_fresh_process(
    tmp_path: Path,
    point: str,
) -> None:
    """A crash-point case must fail closed and leave no child process behind."""
    marker = tmp_path / "failpoint-hit.txt"
    repo_root = Path(__file__).resolve().parents[2]
    script = (
        "from pathlib import Path; "
        "from tests.testkit.failpoints import FailpointController; "
        f"Path({str(marker)!r}).write_text({point!r}, encoding='utf-8'); "
        f"FailpointController(fail_at={point!r}).hit({point!r})"
    )
    baseline = child_processes()
    receipt = run_command(
        [sys.executable, "-c", script],
        command_id=f"failpoint-{point}",
        cwd=tmp_path,
        output_dir=tmp_path / "receipts",
        env={"PYTHONPATH": os.pathsep.join([str(repo_root), os.environ.get("PYTHONPATH", "")] )},
        timeout_seconds=5,
    )

    assert receipt.passed is False
    assert receipt.timed_out is False
    assert receipt.exit_code not in (None, 0)
    assert marker.read_text(encoding="utf-8") == point
    assert point in Path(receipt.stderr_ref).read_text(encoding="utf-8")
    assert_no_child_process_leaks(baseline)
