"""Small pytest reporter used by the ZaoFu TestKit runner.

JUnit XML remains an interoperability artifact.  This reporter preserves the
pytest-specific facts (including xfail/xpass and setup/teardown failures) that
JUnit intentionally flattens.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from .case_evidence import CaseEvidence, CaptureBudget

_CONFIG: pytest.Config | None = None
_CASE = pytest.StashKey[CaseEvidence]()
_BUDGET = pytest.StashKey[CaptureBudget]()


@pytest.fixture
def testkit_case_evidence(request: pytest.FixtureRequest, tmp_path: Path) -> CaseEvidence:
    config = request.config
    target = _report_target(config)
    if not target:
        pytest.fail("ordinary evidence requires the explicit TestKit runner/report")
    if _BUDGET not in config.stash:
        config.stash[_BUDGET] = CaptureBudget()

    def sync() -> None:
        state = _state(config)
        state["results"][request.node.nodeid]["ordinary_evidence"] = evidence.record()
        _write(config, {"schema_version": "zf-testkit-pytest.v1", **state})

    evidence = CaseEvidence(nodeid=request.node.nodeid, source=Path(request.node.path),
                            owned_root=tmp_path, output=Path(target).parent / "case-evidence",
                            budget=config.stash[_BUDGET], on_change=sync)
    request.node.stash[_CASE] = evidence
    sync()
    return evidence


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    report = yield
    evidence = item.stash.get(_CASE, None)
    if evidence is not None:
        if report.when in {"setup", "call"}:
            evidence.checkpoint(f"{report.when}-report")
        if report.when == "teardown":
            phases = _state(item.config)["results"][item.nodeid]["phases"]
            if report.passed and getattr(report, "rerun", None) is None and not hasattr(report, "wasxfail") and all(
                phases.get(phase, {}).get("outcome") == "passed" and "wasxfail" not in phases[phase]
                for phase in ("setup", "call")
            ):
                evidence.discard_after_pass()
        _state(item.config)["results"][item.nodeid]["ordinary_evidence"] = evidence.record()
    return report


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None):
    evidence = item.stash.get(_CASE, None)
    if evidence is not None:
        evidence.checkpoint("pre-teardown")
    return (yield)


def pytest_configure(config: pytest.Config) -> None:
    global _CONFIG
    _CONFIG = config

def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("zaofu-testkit")
    group.addoption(
        "--testkit-report",
        action="store",
        default=None,
        help="write the structured TestKit pytest report to this path",
    )


def _state(config: pytest.Config) -> dict[str, Any]:
    state = getattr(config, "_zaofu_testkit_state", None)
    if state is None:
        state = {
            "items": [],
            "results": {},
            "collection_errors": [],
            "deselected": [],
            "session_finished": False,
            "session_exitstatus": None,
            "worker_id": os.environ.get("PYTEST_XDIST_WORKER", "master"),
            "testrun_uid": os.environ.get("PYTEST_XDIST_TESTRUNUID", "single-process"),
        }
        setattr(config, "_zaofu_testkit_state", state)
    return state


def _write(config: pytest.Config, payload: dict[str, Any]) -> None:
    target = _report_target(config)
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{__import__('os').getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _report_target(config: pytest.Config) -> str | None:
    """Use an exclusive report file for each xdist worker."""
    target = config.getoption("--testkit-report")
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    uid = os.environ.get("PYTEST_XDIST_TESTRUNUID")
    if target and worker and uid:
        path = Path(target)
        return str(path.parent / "workers" / uid / worker / path.name)
    return target


def pytest_collection_finish(session: pytest.Session) -> None:
    state = _state(session.config)
    state["items"] = [item.nodeid for item in session.items]
    for nodeid in state["items"]:
        state["results"].setdefault(
            nodeid, {"nodeid": nodeid, "phases": {}, "status": "not_run"},
        )
    _write(session.config, {"schema_version": "zf-testkit-pytest.v1", **state})


def pytest_deselected(items: list[pytest.Item]) -> None:
    if not items:
        return
    config = items[0].config
    _state(config)["deselected"].extend(item.nodeid for item in items)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.failed:
        # A collection error has no normal runtest report, so keep it explicit
        # instead of silently turning the case into an absent result.
        if _CONFIG is not None:
            _state(_CONFIG)["collection_errors"].append({
                "nodeid": report.nodeid,
                "longrepr": str(report.longrepr),
            })


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if _CONFIG is None:
        return
    state = _state(_CONFIG)
    result = state["results"].setdefault(
        report.nodeid,
        {"nodeid": report.nodeid, "phases": {}},
    )
    phase: dict[str, Any] = {
        "outcome": report.outcome,
        "duration": report.duration,
    }
    if hasattr(report, "wasxfail"):
        phase["wasxfail"] = str(report.wasxfail)
    if report.failed or (report.skipped and report.longrepr is not None):
        phase["longrepr"] = str(report.longrepr)
    rerun = getattr(report, "rerun", None)
    if rerun is not None:
        attempt = str(int(rerun))
        attempts = result.setdefault("attempts", {})
        attempt_phases = attempts.setdefault(attempt, {})
        attempt_phases[report.when] = phase
        result["status"] = _final_case_status(result)
    else:
        result["phases"][report.when] = phase
        result["status"] = _case_status(result["phases"])
    # A hard exit cannot run sessionfinish. Persist only observed phase facts;
    # the unfinished-session flag still prevents the outer runner going green.
    _write(_CONFIG, {"schema_version": "zf-testkit-pytest.v1", **state})


def _case_status(phases: dict[str, Any]) -> str:
    """Use the existing verdict precedence for both checkpoints and final output."""
    call = phases.get("call", {})
    setup = phases.get("setup", {})
    teardown = phases.get("teardown", {})
    if call.get("outcome") == "failed":
        return "xpassed" if str(call.get("longrepr", "")).startswith("[XPASS(strict)]") else "failed"
    if setup.get("outcome") == "failed" or teardown.get("outcome") == "failed":
        return "error"
    if call.get("outcome") == "skipped":
        return "xfailed" if call.get("wasxfail") is not None else "skipped"
    if call.get("outcome") == "passed" and call.get("wasxfail") is not None:
        return "xpassed"
    if call.get("outcome") == "passed" and setup.get("outcome") == teardown.get("outcome") == "passed":
        return "passed"
    return "not_run"


def _final_case_status(result: dict[str, Any]) -> str:
    """Classify all rerun attempts; a recovered flaky case is not green."""
    attempts = result.get("attempts", {})
    if not isinstance(attempts, dict) or not attempts:
        return _case_status(result.get("phases", {}))
    ordered = [attempts[key] for key in sorted(attempts, key=lambda value: int(value))]
    current = _case_status(ordered[-1])
    prior_failed = any(
        _case_status(phase) in {"failed", "error"}
        or phase.get("call", {}).get("outcome") == "rerun"
        for phase in ordered[:-1]
    )
    if prior_failed and current == "passed":
        return "flaky_pass"
    return current


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = _state(session.config)
    state["session_finished"] = True
    state["session_exitstatus"] = int(exitstatus)
    collected = set(state["items"])
    # Plugins such as Schemathesis may emit log reports for generated
    # subtests.  They are not independent pytest collection items and do not
    # receive the normal setup/teardown lifecycle.  Preserve successful
    # subtests as passed evidence, while retaining any failed phase as an
    # explicit collection error so it cannot be mistaken for a green run.
    for nodeid in list(state["results"]):
        if nodeid in collected:
            continue
        result = state["results"][nodeid]
        if any(
            phase.get("outcome") == "failed"
            for phase in result.get("phases", {}).values()
            if isinstance(phase, dict)
        ):
            state["collection_errors"].append({
                "nodeid": nodeid,
                "longrepr": "generated subtest phase failed",
            })
        else:
            result["status"] = "passed"
    for nodeid in state["items"]:
        result = state["results"].setdefault(
            nodeid,
            {"nodeid": nodeid, "phases": {}, "status": "not_run"},
        )
        result["status"] = _final_case_status(result)
    _write(session.config, {"schema_version": "zf-testkit-pytest.v1", **state})
