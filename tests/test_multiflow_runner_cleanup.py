from __future__ import annotations

import argparse
import os
import signal
import subprocess
from pathlib import Path

import pytest

from tests.e2e import generic_workflow_real_provider_drill as general_drill


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "relative",
    [
        "tests/e2e/scripts/run_prod_new_three_workflow_e2e.sh",
        "tests/e2e/scripts/run_oa_clean_four_flow_e2e.sh",
        "tests/e2e/scripts/run_oa_multiflow_blocking_canary_e2e.sh",
        "tests/e2e/scripts/process_tree_cleanup.sh",
    ],
)
def test_multiflow_runner_signal_cleanup_scripts_are_valid(
    relative: str,
) -> None:
    result = subprocess.run(
        ["bash", "-n", str(ROOT / relative)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_general_drill_sigterm_restores_handler_and_cleans_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleaned: list[Path] = []

    def terminate_during_run(*_args, **_kwargs):  # noqa: ANN002, ANN003
        signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(
        general_drill,
        "run_generic_workflow_complex_scenario",
        terminate_during_run,
    )
    monkeypatch.setattr(
        general_drill,
        "_cleanup",
        lambda root: cleaned.append(root),
    )
    previous = signal.getsignal(signal.SIGTERM)

    with pytest.raises(general_drill._DrillTerminated) as terminated:
        general_drill.run(argparse.Namespace(
            confirm_real=True,
            backend="codex",
            timeout_seconds=1,
            model="gpt-5.5",
            reasoning_effort="low",
        ))

    assert terminated.value.signum == signal.SIGTERM
    assert len(cleaned) == 1
    assert signal.getsignal(signal.SIGTERM) == previous


def test_product_runner_sigterm_stops_runtime_and_reaps_watcher(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "zf-stop.args"
    pid_file = tmp_path / "watcher.pid"
    fake_zf = tmp_path / "fake-zf"
    fake_zf.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >\"$ZF_E2E_SIGNAL_MARKER\"\n",
        encoding="utf-8",
    )
    fake_zf.chmod(0o755)
    env = {
        **os.environ,
        "ZF_BIN": str(fake_zf),
        "ZF_E2E_ROOT": str(tmp_path / "run"),
        "ZF_E2E_ALLOW_DIRTY_SOURCE": "true",
        "ZF_E2E_SIGNAL_CLEANUP_SELFTEST": "true",
        "ZF_E2E_SIGNAL_SELFTEST_PID_FILE": str(pid_file),
        "ZF_E2E_SIGNAL_MARKER": str(marker),
    }

    result = subprocess.run(
        [
            "bash",
            str(
                ROOT
                / "tests/e2e/scripts/run_prod_new_three_workflow_e2e.sh"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 143, result.stderr
    assert marker.read_text(encoding="utf-8").strip() == (
        "stop --include-run-manager --clean-workdirs"
    )
    watcher_pid = pid_file.read_text(encoding="utf-8").strip()
    probe = subprocess.run(
        ["kill", "-0", watcher_pid],
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode != 0


def test_clean_four_flow_runner_sigterm_stops_scoped_product_runtime(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "zf-stop.args"
    pid_file = tmp_path / "child.pid"
    descendant_pid_file = tmp_path / "descendant.pid"
    fake_zf = tmp_path / "fake-zf"
    fake_zf.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s|%s\\n' \"$PWD\" \"$*\" >\"$ZF_E2E_SIGNAL_MARKER\"\n",
        encoding="utf-8",
    )
    fake_zf.chmod(0o755)
    run_root = tmp_path / "run"
    env = {
        **os.environ,
        "ZF_BIN": str(fake_zf),
        "ZF_E2E_FOUR_FLOW_ROOT": str(run_root),
        "ZF_E2E_SIGNAL_CLEANUP_SELFTEST": "true",
        "ZF_E2E_SIGNAL_SELFTEST_PID_FILE": str(pid_file),
        "ZF_E2E_SIGNAL_SELFTEST_DESCENDANT_PID_FILE": str(
            descendant_pid_file
        ),
        "ZF_E2E_SIGNAL_MARKER": str(marker),
    }

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "tests/e2e/scripts/run_oa_clean_four_flow_e2e.sh"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 143, result.stderr
    assert marker.read_text(encoding="utf-8").strip() == (
        f"{run_root / 'product/product'}|"
        "stop --include-run-manager --clean-workdirs"
    )
    child_pid = pid_file.read_text(encoding="utf-8").strip()
    descendant_pid = descendant_pid_file.read_text(encoding="utf-8").strip()
    for pid in (child_pid, descendant_pid):
        probe = subprocess.run(
            ["kill", "-0", pid],
            check=False,
            capture_output=True,
            text=True,
        )
        assert probe.returncode != 0


def test_generic_drill_explicitly_isolates_mock_control_plane(monkeypatch):
    from zf.runtime import workflow_runtime_start

    def unexpected_launcher(**_kwargs):
        raise AssertionError("real control plane launcher reached from hybrid drill")

    monkeypatch.setattr(workflow_runtime_start, "ensure_project_runtime", unexpected_launcher)
    monkeypatch.setattr(
        general_drill, "_provider_verifier",
        lambda **_kwargs: lambda _project, _state, _sources, expected: expected,
    )
    result = general_drill.run(argparse.Namespace(
        confirm_real=True, backend="codex", timeout_seconds=5,
        model="test", reasoning_effort="low",
    ))
    assert result["status"] == "passed"
    assert result["control_plane"] == "mock"


def test_generic_drill_schema_allows_real_rejection_without_invalid_fields(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    source = state / "report.json"
    source.write_text("{}", encoding="utf-8")
    expected = {"verdict": "passed", "recommended_action": "complete",
                "goal_coverage": [{"status": "closed"}], "open_gap_refs": []}
    def invoke(**kwargs):
        fields = kwargs["schema"]["properties"]["artifact_delivery_result"]["properties"]
        assert set(fields["verdict"]["enum"]) == {"passed", "rejected", "blocked"}
        assert set(fields["recommended_action"]["enum"]) == {"complete", "gap_plan", "replan", "human", "hold"}
        assert "maxItems" not in fields["open_gap_refs"]
        return {"artifact_delivery_result": expected}, {"raw_stdout": ""}
    monkeypatch.setattr(general_drill, "_invoke_codex", invoke)
    verify = general_drill._provider_verifier(backend="codex", timeout_seconds=5,
                                                model="test", reasoning_effort="low", audit_holder={})
    assert verify(tmp_path, state, {"report": {"ref": str(source)}}, expected) == expected
