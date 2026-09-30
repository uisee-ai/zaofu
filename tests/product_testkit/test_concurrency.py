import json
import sys

import pytest

from .concurrency import assert_status_monotonic, assert_unique_keys, run_concurrency_scenarios


def test_concurrency_assertions_detect_duplicates_and_regressions():
    assert_unique_keys([{"id": "a"}, {"id": "b"}], key="id")
    with pytest.raises(AssertionError):
        assert_unique_keys([{"id": "a"}, {"id": "a"}], key="id")
    order = {"pending": 0, "running": 1, "done": 2}
    assert_status_monotonic(["pending", "running", "done"], order=order)
    with pytest.raises(AssertionError):
        assert_status_monotonic(["running", "pending"], order=order)


def test_concurrent_process_fixture_collects_all_results_without_losing_records(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "path = pathlib.Path(sys.argv[1]); key = sys.argv[2]\n"
        "with path.open('a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps({'id': key}) + '\\n'); stream.flush()\n",
        encoding="utf-8",
    )
    from .concurrency import run_concurrent_processes

    result = run_concurrent_processes(
        [[sys.executable, str(script), str(tmp_path / "records.jsonl"), str(index)] for index in range(4)],
        cwd=tmp_path,
        timeout_seconds=5,
    )

    assert result.passed
    records = [json.loads(line) for line in (tmp_path / "records.jsonl").read_text().splitlines()]
    assert_unique_keys(records, key="id")
    assert {row["id"] for row in records} == {str(index) for index in range(4)}


def test_concurrency_scenario_adapter_runs_tokenized_workers(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text("import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ok')\n", encoding="utf-8")
    [row] = run_concurrency_scenarios(
        [{"id": "workers", "commands": [
            f"{sys.executable} worker.py out-a", f"{sys.executable} worker.py out-b",
        ], "timeout_seconds": 5}],
        project_root=tmp_path, output_dir=tmp_path / "evidence",
    )
    assert row["passed"] is True
    assert (tmp_path / "out-a").read_text() == "ok"
    assert (tmp_path / "evidence" / "concurrency.workers.receipt.json").is_file()


def test_concurrency_missing_executable_is_environment_failure(tmp_path):
    [row] = run_concurrency_scenarios(
        [{"id": "missing", "commands": ["definitely-not-installed-zf-worker"], "timeout_seconds": 1}],
        project_root=tmp_path, output_dir=tmp_path / "evidence",
    )
    assert row["passed"] is False
    assert row["verdict"] == "environment_failure"
