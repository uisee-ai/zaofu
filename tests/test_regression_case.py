"""design 101 §8 C/D/E — deterministic regression eval case."""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.regression_case import (
    REGRESSION_CASE_CAPTURED,
    RegressionCase,
    capture_regression_case,
    evaluate_assertion,
    evaluate_assertions,
    list_regression_cases,
    replay_regression_case,
)


# --- D: deterministic assertion evaluation ---------------------------------


def test_numeric_assertions():
    facts = {"scope_violation": 0, "rework": 1}
    assert evaluate_assertion("scope_violation==0", facts)["passed"] is True
    assert evaluate_assertion("rework<=1", facts)["passed"] is True
    assert evaluate_assertion("rework<1", facts)["passed"] is False
    assert evaluate_assertion("rework>=2", facts)["passed"] is False


def test_gate_not_failed_assertion():
    assert evaluate_assertion("gate:tests not failed", {"gate:tests": "passed"})["passed"] is True
    assert evaluate_assertion("gate:tests not failed", {"gate:tests": "failed"})["passed"] is False


def test_assertions_fail_closed_on_missing_or_unparseable():
    # missing fact must NOT silently pass
    assert evaluate_assertion("nope==0", {})["passed"] is False
    # unparseable predicate must NOT silently pass
    assert evaluate_assertion("garbage", {})["passed"] is False


def test_evaluate_assertions_batch():
    rows = evaluate_assertions(("rework==1", "scope_violation==0"), {"rework": 1, "scope_violation": 0})
    assert [r["passed"] for r in rows] == [True, True]


# --- C: capture + persist --------------------------------------------------


def test_capture_and_list_roundtrip(tmp_path: Path):
    case = capture_regression_case(
        tmp_path,
        case_id="rc-1",
        source_task_id="T1",
        feature_id="F-1",
        source_event_ids=("evt-a", "evt-b"),
        assertions=("scope_violation==0",),
    )
    assert (tmp_path / "artifacts" / "regression" / "rc-1.json").exists()
    cases = list_regression_cases(tmp_path)
    assert len(cases) == 1
    assert cases[0].case_id == "rc-1"
    assert cases[0].source_task_id == "T1"
    assert cases[0].assertions == ("scope_violation==0",)


# --- E: replay -------------------------------------------------------------


def test_replay_assertions_verdict():
    case = RegressionCase(case_id="rc-2", source_task_id="T1", feature_id="F-1",
                          assertions=("scope_violation==0", "rework<=1"))
    ok = replay_regression_case(case, facts={"scope_violation": 0, "rework": 1})
    assert ok["passed"] is True
    bad = replay_regression_case(case, facts={"scope_violation": 2, "rework": 1})
    assert bad["passed"] is False


def test_replay_command_gate():
    passing = RegressionCase(case_id="rc-3", source_task_id="T1", feature_id="F-1", command="true")
    failing = RegressionCase(case_id="rc-4", source_task_id="T1", feature_id="F-1", command="false")
    assert replay_regression_case(passing, facts={}, run_command=True)["passed"] is True
    assert replay_regression_case(failing, facts={}, run_command=True)["passed"] is False


# --- C wiring: controlled action -------------------------------------------


def _service(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    service = ControlledActionService(
        state_dir, EventWriter(log), config=ZfConfig(project=ProjectConfig(name="t"))
    )
    return service, log, state_dir


def test_capture_regression_case_controlled_action(tmp_path: Path):
    service, log, state_dir = _service(tmp_path)
    requested = ZfEvent(type="control.action.requested", actor="web",
                        payload={"task_id": "T1", "assertions": ["scope_violation==0"]})
    result = service._execute_action(
        requested=requested,
        action="capture-regression-case",
        requested_action="capture-regression-case",
        payload={"task_id": "T1", "feature_id": "F-1", "assertions": ["scope_violation==0"]},
    )
    assert result.get("ok") is True
    assert result["result"]["assertions"] == ["scope_violation==0"]
    captured = [e for e in log.read_all() if e.type == REGRESSION_CASE_CAPTURED]
    assert captured and captured[0].task_id == "T1"
    assert list_regression_cases(state_dir)[0].source_task_id == "T1"


def test_replay_regression_case_controlled_action(tmp_path: Path):
    service, log, state_dir = _service(tmp_path)
    req = ZfEvent(type="control.action.requested", actor="web", payload={})
    service._execute_action(
        requested=req, action="capture-regression-case",
        requested_action="capture-regression-case",
        payload={"task_id": "T1", "case_id": "rc1", "assertions": ["rework==0"]},
    )
    # clean → assertion passes
    ok = service._execute_action(
        requested=req, action="replay-regression-case",
        requested_action="replay-regression-case", payload={"case_id": "rc1"},
    )
    assert ok.get("ok") is True and ok["result"]["passed"] is True
    replayed = [e for e in log.read_all() if e.type == "regression.case.replayed"]
    assert replayed and replayed[0].payload["passed"] is True
    # add a rework event → assertion fails on replay
    log.append(ZfEvent(type="task.rework.requested", id="e-rw", task_id="T1"))
    bad = service._execute_action(
        requested=req, action="replay-regression-case",
        requested_action="replay-regression-case", payload={"case_id": "rc1"},
    )
    assert bad["result"]["passed"] is False
    # missing case → 404
    nf = service._execute_action(
        requested=req, action="replay-regression-case",
        requested_action="replay-regression-case", payload={"case_id": "nope"},
    )
    assert nf.get("_status_code") == 404


def test_capture_requires_task_id(tmp_path: Path):
    service, log, _ = _service(tmp_path)
    requested = ZfEvent(type="control.action.requested", actor="web", payload={})
    result = service._execute_action(
        requested=requested, action="capture-regression-case",
        requested_action="capture-regression-case", payload={},
    )
    assert result.get("ok") is not True
    assert result.get("_status_code") == 422
