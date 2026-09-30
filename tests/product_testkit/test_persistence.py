from pathlib import Path

import pytest

from .persistence import assert_idempotent_state, assert_json_state, snapshot_file, run_persistence_scenarios


def test_persistence_snapshot_json_and_idempotency(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text('{"status":"ready"}', encoding="utf-8")
    before = snapshot_file(path)
    assert_json_state(path, {"status": "ready"})
    assert_idempotent_state(before, snapshot_file(path))
    path.write_text('{"status":"done"}', encoding="utf-8")
    with pytest.raises(AssertionError):
        assert_idempotent_state(before, snapshot_file(path))


def test_persistence_rejects_symlink(tmp_path: Path):
    target = tmp_path / "real.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        snapshot_file(link)


def test_persistence_scenario_runs_ordered_commands_and_writes_receipt(tmp_path: Path):
    [row] = run_persistence_scenarios(
        [{"id": "migration", "commands": ["python -c 'print(1)'", "python -c 'print(2)'"], "timeout_seconds": 10}],
        project_root=tmp_path, output_dir=tmp_path / "evidence",
    )
    assert row["passed"] is True
    assert row["verdict"] == "pass"
    assert len(row["steps"]) == 2
    assert (tmp_path / "evidence" / "persistence.migration.receipt.json").is_file()


def test_persistence_scenario_stops_on_failed_command(tmp_path: Path):
    [row] = run_persistence_scenarios(
        [{"id": "migration", "commands": ["python -c 'raise SystemExit(3)'", "python -c 'print(2)'"], "timeout_seconds": 10}],
        project_root=tmp_path, output_dir=tmp_path / "evidence",
    )
    assert row["passed"] is False
    assert row["verdict"] == "fail"
    assert len(row["steps"]) == 1
