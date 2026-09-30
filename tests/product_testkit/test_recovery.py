import json
import subprocess
import sys

import pytest

from .recovery import assert_recovery_equivalent, freeze_state, run_recovery_scenarios


def test_recovery_requires_baseline_equivalence():
    baseline = {"status": "submitted", "attempt": 1, "updated_at": "t1"}
    recovered = {"status": "submitted", "attempt": 1, "updated_at": "t2"}
    assert_recovery_equivalent(baseline=baseline, recovered=recovered, ignored_keys=("updated_at",))
    with pytest.raises(AssertionError):
        assert_recovery_equivalent(baseline=baseline, recovered={"status": "done", "attempt": 1})


def test_freeze_state_detaches_mutable_input():
    source = {"items": [1]}
    snapshot = freeze_state(source)
    source["items"].append(2)
    assert snapshot == {"items": [1]}


def test_crash_restart_fixture_preserves_state(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(
        "import json, pathlib, sys, time\n"
        "state = pathlib.Path(sys.argv[1]); ready = pathlib.Path(sys.argv[2])\n"
        "if not state.exists(): state.write_text(json.dumps({'value': 1}), encoding='utf-8')\n"
        "ready.write_text('ready', encoding='ascii')\n"
        "while True: time.sleep(1)\n",
        encoding="utf-8",
    )
    state = tmp_path / "state.json"
    ready = tmp_path / "ready"
    from .recovery import run_crash_restart

    receipt = run_crash_restart(
        [sys.executable, str(script), str(state), str(ready)],
        cwd=tmp_path,
        state_path=state,
        ready_path=ready,
        timeout_seconds=5,
    )

    assert receipt.passed
    assert receipt.baseline == receipt.recovered == {"value": 1}
    assert receipt.crash_returncode is not None


def test_recovery_scenario_adapter_persists_structured_receipt(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(
        "import json, pathlib, sys, time\n"
        "state = pathlib.Path(sys.argv[1]); ready = pathlib.Path(sys.argv[2])\n"
        "if not state.exists(): state.write_text(json.dumps({'value': 7}), encoding='utf-8')\n"
        "ready.write_text('ready', encoding='ascii')\n"
        "while True: time.sleep(1)\n", encoding="utf-8",
    )
    output = tmp_path / "out"
    [row] = run_recovery_scenarios(
        [{"id": "restart", "command": f"{sys.executable} worker.py state.json ready",
          "state_path": "state.json", "ready_path": "ready", "timeout_seconds": 5}],
        project_root=tmp_path, output_dir=output,
    )
    assert row["passed"] is True
    assert (output / "recovery.restart.receipt.json").is_file()


def test_recovery_missing_executable_is_environment_failure(tmp_path):
    [row] = run_recovery_scenarios(
        [{"id": "missing", "command": "definitely-not-installed-zf-worker state ready",
          "state_path": "state.json", "ready_path": "ready", "timeout_seconds": 1}],
        project_root=tmp_path, output_dir=tmp_path / "evidence",
    )
    assert row["passed"] is False
    assert row["verdict"] == "environment_failure"
