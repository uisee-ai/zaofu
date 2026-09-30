"""Mutation-resistant contract for controlled real-E2E-run actions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from zf.core.events import ZfEvent
from zf.runtime import control_actions_ops as ops_module
from zf.runtime import real_e2e_runner as runner_module
from zf.runtime import run_contract as contract_module
from zf.runtime.control_actions_ops import OpsActionsMixin
from zf.runtime.real_e2e_runner import RealE2ERowResult, RealE2ERunnerResult


def _request() -> ZfEvent:
    return ZfEvent(
        id="evt-request",
        type="control.action.requested",
        correlation_id="RUN-1",
        payload={},
    )


def _service(tmp_path: Path, *, project_root: Path | None = None) -> OpsActionsMixin:
    service = OpsActionsMixin()
    service.state_dir = tmp_path / "state"
    service.project_root = project_root if project_root is not None else tmp_path / "project"
    service.actor = "run-manager"
    service.source = "operator-inbox"
    service.surface = "web"
    service.writer = SimpleNamespace(emit=Mock())
    service._failed = Mock(return_value={"marker": "failed"})  # type: ignore[method-assign]
    service._completed = Mock()  # type: ignore[method-assign]
    return service


def _install_helpers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    contract: object = None,
    result: RealE2ERunnerResult | None = None,
    task_id: str = "TASK-1",
) -> dict[str, Mock]:
    def task_from_payload(actual_payload: object) -> str:
        assert actual_payload is not None
        return task_id

    helpers = {
        "contract": Mock(return_value=contract),
        "runner": Mock(
            return_value=result
            if result is not None
            else RealE2ERunnerResult(
                passed=True,
                result_matrix_ref="artifacts/real-e2e/results.json",
                evidence_refs=["artifacts/real-e2e/cli.json"],
                rows=[
                    RealE2ERowResult(
                        row_id="CLI-1",
                        surface="cli",
                        status="passed",
                        command="zf --help",
                        exit_code=0,
                        evidence_ref="artifacts/real-e2e/cli.json",
                    ),
                ],
            ),
        ),
        "task": Mock(side_effect=task_from_payload),
        "redact": Mock(side_effect=lambda value: {"redacted": value}),
    }
    monkeypatch.setattr(contract_module, "load_run_contract", helpers["contract"])
    monkeypatch.setattr(runner_module, "run_real_e2e_matrix", helpers["runner"])
    monkeypatch.setattr(ops_module, "_task_id_from_payload", helpers["task"])
    monkeypatch.setattr(ops_module, "redact_obj", helpers["redact"])
    return helpers


def test_payload_matrix_aliases_are_combined_deduplicated_and_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "target"
    service = _service(tmp_path, project_root=root)
    helpers = _install_helpers(
        monkeypatch,
        contract={"refs": {"real_e2e_matrix": ["ignored.json"]}},
    )
    service.writer.emit.return_value = ZfEvent(
        id="evt-e2e",
        type="real_e2e.run.completed",
    )
    payload = {
        "real_e2e_matrix_paths": ["a.json", "b.json"],
        "real_e2e_matrix_refs": ["b.json", "c.json"],
        "matrix_refs": "d.json,a.json",
        "evidence_dir": " custom-evidence ",
        "timeout_seconds": "37",
    }

    response = service._real_e2e_run_action(
        requested=_request(),
        action="real-e2e-run",
        requested_action="real.e2e.run",
        payload=payload,
    )

    helpers["contract"].assert_called_once_with(service.state_dir)
    helpers["runner"].assert_called_once_with(
        root,
        {
            "real_e2e_matrix_paths": ["a.json", "b.json", "c.json", "d.json"],
            "evidence_dir": " custom-evidence ",
            "timeout_seconds": 37,
        },
    )
    assert response["status"] == "passed"


@pytest.mark.parametrize(
    ("contract", "expected_refs"),
    [
        ({"refs": {"real_e2e_matrix": "contract.json"}}, ["contract.json"]),
        (
            {"refs": {"real_e2e_matrix": ["one.json", "two.json", "one.json"]}},
            ["one.json", "two.json"],
        ),
    ],
)
def test_run_contract_refs_are_fallback_and_defaults_are_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contract: dict,
    expected_refs: list[str],
) -> None:
    service = _service(tmp_path)
    service.project_root = None
    helpers = _install_helpers(monkeypatch, contract=contract)
    service.writer.emit.return_value = ZfEvent(id="evt-e2e", type="real_e2e.run.completed")

    service._real_e2e_run_action(
        requested=_request(),
        action="real-e2e-run",
        requested_action="real.e2e.run",
        payload={"evidence_dir": "", "timeout_seconds": 0},
    )

    helpers["runner"].assert_called_once_with(
        service.state_dir.parent,
        {
            "real_e2e_matrix_paths": expected_refs,
            "evidence_dir": "artifacts/real-e2e",
            "timeout_seconds": 120,
        },
    )


@pytest.mark.parametrize(
    "contract",
    [
        None,
        [],
        {},
        {"refs": []},
        {"refs": {}},
        {"refs": {"real_e2e_matrix": []}},
    ],
)
def test_missing_matrix_refs_fail_before_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contract: object,
) -> None:
    service = _service(tmp_path)
    helpers = _install_helpers(monkeypatch, contract=contract)
    requested = _request()
    payload: dict = {}

    assert service._real_e2e_run_action(
        requested=requested,
        action="real-e2e-run",
        requested_action="real.e2e.run",
        payload=payload,
    ) == {"marker": "failed"}

    service._failed.assert_called_once_with(  # type: ignore[attr-defined]
        requested=requested,
        action="real-e2e-run",
        requested_action="real.e2e.run",
        task_id="TASK-1",
        reason="no real_e2e_matrix refs declared in payload or run contract",
        status_code=422,
        status="missing_matrix_refs",
    )
    helpers["runner"].assert_not_called()
    service.writer.emit.assert_not_called()
    service._completed.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.parametrize("error", [OSError("runner unavailable"), ValueError("bad matrix")])
def test_runner_failures_return_exact_failure_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    service = _service(tmp_path)
    helpers = _install_helpers(monkeypatch)
    helpers["runner"].side_effect = error
    requested = _request()
    payload = {"real_e2e_matrix_paths": "matrix.json"}

    assert service._real_e2e_run_action(
        requested=requested,
        action="real-e2e-run",
        requested_action="real.e2e.run",
        payload=payload,
    ) == {"marker": "failed"}

    service._failed.assert_called_once_with(  # type: ignore[attr-defined]
        requested=requested,
        action="real-e2e-run",
        requested_action="real.e2e.run",
        task_id="TASK-1",
        reason=f"real E2E run failed: {error}",
        status_code=422,
        status="run_failed",
    )
    service.writer.emit.assert_not_called()
    service._completed.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        (
            RealE2ERunnerResult(
                passed=True,
                result_matrix_ref="results.json",
                evidence_refs=["first.json", "second.json"],
                rows=[
                    RealE2ERowResult("row-1", "cli", "passed", "check-one", 0),
                    RealE2ERowResult("row-2", "web", "passed", "check-two", 0),
                ],
            ),
            "passed",
        ),
        (
            RealE2ERunnerResult(
                passed=False,
                result_matrix_ref="",
                evidence_refs=[],
                rows=[],
            ),
            "failed",
        ),
    ],
)
def test_result_emits_exact_event_completion_and_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: RealE2ERunnerResult,
    expected_status: str,
) -> None:
    service = _service(tmp_path)
    helpers = _install_helpers(monkeypatch, result=result, task_id="TASK-9")
    requested = _request()
    emitted = ZfEvent(id="evt-e2e", type="real_e2e.run.completed")
    service.writer.emit.return_value = emitted
    payload = {"real_e2e_matrix_paths": ["a.json", "a.json"]}

    response = service._real_e2e_run_action(
        requested=requested,
        action="real-e2e-run",
        requested_action="real.e2e.run",
        payload=payload,
    )

    event_payload = {
        "schema_version": "real-e2e-run.event.v1",
        "passed": result.passed,
        "result_matrix_ref": result.result_matrix_ref,
        "evidence_refs": result.evidence_refs,
        "row_count": len(result.rows),
        "matrix_refs": ["a.json"],
        "source": "operator-inbox",
        "surface": "web",
    }
    helpers["redact"].assert_called_once_with(event_payload)
    service.writer.emit.assert_called_once_with(
        "real_e2e.run.completed",
        actor="run-manager",
        task_id="TASK-9",
        causation_id="evt-request",
        correlation_id="RUN-1",
        payload={"redacted": event_payload},
    )
    service._completed.assert_called_once_with(  # type: ignore[attr-defined]
        requested=requested,
        event=emitted,
        action="real-e2e-run",
        requested_action="real.e2e.run",
        status=expected_status,
        task_id="TASK-9",
        extra={
            "passed": result.passed,
            "result_matrix_ref": result.result_matrix_ref,
            "evidence_refs": result.evidence_refs,
            "row_count": len(result.rows),
        },
    )
    assert helpers["task"].call_args_list == [call(payload), call(payload)]
    assert response == {
        "_status_code": 202,
        "ok": True,
        "status": expected_status,
        "action": "real-e2e-run",
        "requested_action": "real.e2e.run",
        "event_id": "evt-e2e",
        **result.to_dict(),
    }


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-057: invalid timeout escapes the controlled failure receipt",
)
def test_invalid_timeout_is_classified_as_controlled_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    _install_helpers(monkeypatch)

    response = service._real_e2e_run_action(
        requested=_request(),
        action="real-e2e-run",
        requested_action="real.e2e.run",
        payload={"real_e2e_matrix_paths": "matrix.json", "timeout_seconds": "not-an-int"},
    )

    assert response["ok"] is False
    assert response["status"] == "run_failed"
    assert response["_status_code"] == 422
