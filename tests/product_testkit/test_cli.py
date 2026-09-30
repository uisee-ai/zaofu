from pathlib import Path

from .cli import assert_cli_result, run_cli


def test_cli_probe_captures_success_and_failure(tmp_path: Path):
    ok = run_cli(["python", "-c", "print('usage: demo')"], cwd=tmp_path)
    assert_cli_result(ok, expected_exit=0, required_output="usage")
    bad = run_cli(["python", "-c", "raise SystemExit(2)"], cwd=tmp_path)
    assert_cli_result(bad, expected_exit=2)


def test_cli_probe_timeout_is_explicit(tmp_path: Path):
    result = run_cli(["python", "-c", "import time; time.sleep(1)"], cwd=tmp_path, timeout_seconds=0.01)
    assert result.timed_out
    assert result.exit_code is None
