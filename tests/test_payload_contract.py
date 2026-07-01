"""EVAL-PAYLOAD-CONTRACT-001 — completion event 6-field payload contract."""

from __future__ import annotations

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.payload_schemas import (
    SUCCESS_EVENT_TYPES,
    VERIFY_EVENT_TYPES,
    build_invalid_event_payload,
    validate_completion_payload,
    warn_completion_payload,
)


def _ev(event_type: str, payload: dict | None = None) -> ZfEvent:
    return ZfEvent(
        type=event_type,
        actor="dev-1",
        task_id="TASK-X",
        payload=payload or {},
    )


# ---------------------------------------------------------------------------
# Set declarations
# ---------------------------------------------------------------------------


def test_success_event_types_count() -> None:
    """Declared completion event types."""
    assert SUCCESS_EVENT_TYPES == frozenset({
        "dev.build.done",
        "review.approved",
        "verify.passed",
        "test.passed",
        "judge.passed",
        "arch.proposal.done",
        "design.critique.done",
    })


def test_verify_event_types_subset_of_success() -> None:
    assert VERIFY_EVENT_TYPES.issubset(SUCCESS_EVENT_TYPES)


# ---------------------------------------------------------------------------
# validate_completion_payload — missing-field detection
# ---------------------------------------------------------------------------


def test_non_completion_event_returns_empty() -> None:
    """Events outside SUCCESS_EVENT_TYPES bypass the contract."""
    assert validate_completion_payload(
        _ev("worker.heartbeat", {}),
    ) == []


def test_design_critique_approval_payload_bypasses_completion_contract() -> None:
    payload = {
        "verdict": "approve",
        "checks": [{"name": "source_coverage", "passed": True}],
    }

    assert validate_completion_payload(_ev("design.critique.done", payload)) == []
    assert warn_completion_payload(_ev("design.critique.done", payload)) == []


def test_complete_dev_build_done_no_errors() -> None:
    """Full 6-field payload → empty error list."""
    payload = {
        "summary": "added jwt middleware",
        "changed_files": ["src/auth/jwt.py"],
        "evidence_refs": [
            {"kind": "git", "path": "abc123", "status": "committed"},
        ],
        "residual_risks": ["no refresh token endpoint yet"],
        "next_agent_input": "review jwt.py for token expiry",
    }
    assert validate_completion_payload(_ev("dev.build.done", payload)) == []


def test_missing_summary_flagged() -> None:
    payload = {
        "changed_files": ["x.py"],
        "evidence_refs": [{"path": "abc"}],
    }
    errors = validate_completion_payload(_ev("dev.build.done", payload))
    assert "summary" in errors


def test_empty_summary_flagged() -> None:
    payload = {
        "summary": "   ",
        "changed_files": ["x.py"],
        "evidence_refs": [{"path": "abc"}],
    }
    errors = validate_completion_payload(_ev("dev.build.done", payload))
    assert "summary" in errors


def test_missing_changed_files_flagged() -> None:
    payload = {
        "summary": "did the thing",
        "evidence_refs": [{"path": "abc"}],
    }
    errors = validate_completion_payload(_ev("dev.build.done", payload))
    assert "changed_files" in errors


def test_wrong_type_changed_files_flagged() -> None:
    """changed_files must be list/tuple, not str."""
    payload = {
        "summary": "x",
        "changed_files": "src/x.py",  # str, not list
        "evidence_refs": [{"path": "abc"}],
    }
    errors = validate_completion_payload(_ev("dev.build.done", payload))
    assert "changed_files" in errors


def test_missing_evidence_refs_flagged() -> None:
    payload = {
        "summary": "x",
        "changed_files": ["x.py"],
    }
    errors = validate_completion_payload(_ev("review.approved", payload))
    assert "evidence_refs" in errors


def test_empty_evidence_refs_flagged() -> None:
    payload = {
        "summary": "x",
        "changed_files": ["x.py"],
        "evidence_refs": [],
    }
    errors = validate_completion_payload(_ev("review.approved", payload))
    assert "evidence_refs" in errors


# ---------------------------------------------------------------------------
# tests_run rule: only required for VERIFY_EVENT_TYPES
# ---------------------------------------------------------------------------


def test_dev_build_done_does_not_require_tests_run() -> None:
    """tests_run only required for test/judge events, not dev."""
    payload = {
        "summary": "x",
        "changed_files": ["x.py"],
        "evidence_refs": [{"path": "abc"}],
    }
    errors = validate_completion_payload(_ev("dev.build.done", payload))
    assert "tests_run" not in errors


def test_test_passed_requires_tests_run() -> None:
    payload = {
        "summary": "tests ok",
        "changed_files": [],
        "evidence_refs": [{"path": "log.txt"}],
        # tests_run missing
    }
    errors = validate_completion_payload(_ev("test.passed", payload))
    assert "tests_run" in errors


def test_verify_passed_requires_tests_run() -> None:
    payload = {
        "summary": "verify ok",
        "changed_files": [],
        "evidence_refs": [{"path": "verify.log"}],
    }
    errors = validate_completion_payload(_ev("verify.passed", payload))
    assert "tests_run" in errors


def test_test_passed_empty_tests_run_flagged() -> None:
    payload = {
        "summary": "tests ok",
        "changed_files": [],
        "evidence_refs": [{"path": "log.txt"}],
        "tests_run": [],
    }
    errors = validate_completion_payload(_ev("test.passed", payload))
    assert "tests_run" in errors


def test_judge_passed_with_tests_run() -> None:
    payload = {
        "summary": "judge approved",
        "changed_files": [],
        "evidence_refs": [{"path": "log.txt"}],
        "tests_run": ["pytest tests/"],
    }
    errors = validate_completion_payload(_ev("judge.passed", payload))
    assert errors == []


# ---------------------------------------------------------------------------
# warn_completion_payload — residual_risks / next_agent_input
# ---------------------------------------------------------------------------


def test_warn_missing_residual_risks() -> None:
    payload = {
        "summary": "x",
        "changed_files": [],
        "evidence_refs": [{"path": "a"}],
    }
    warnings = warn_completion_payload(_ev("dev.build.done", payload))
    assert "residual_risks" in warnings
    assert "next_agent_input" in warnings


def test_warn_full_payload_no_warnings() -> None:
    payload = {
        "summary": "x",
        "changed_files": ["x.py"],
        "evidence_refs": [{"path": "a"}],
        "residual_risks": ["some risk"],
        "next_agent_input": "do thing",
    }
    warnings = warn_completion_payload(_ev("dev.build.done", payload))
    assert warnings == []


def test_warn_empty_string_treated_as_missing() -> None:
    payload = {
        "summary": "x",
        "changed_files": [],
        "evidence_refs": [{"path": "a"}],
        "residual_risks": [],
        "next_agent_input": "",
    }
    warnings = warn_completion_payload(_ev("dev.build.done", payload))
    assert "residual_risks" in warnings
    assert "next_agent_input" in warnings


def test_warn_non_completion_event_returns_empty() -> None:
    """Non-success events have no warn fields either."""
    assert warn_completion_payload(_ev("worker.heartbeat", {})) == []


# ---------------------------------------------------------------------------
# build_invalid_event_payload
# ---------------------------------------------------------------------------


def test_build_invalid_payload_includes_required_keys() -> None:
    source = _ev("dev.build.done", {})
    payload = build_invalid_event_payload(
        source, missing=["summary", "changed_files"],
    )
    assert payload["reason"] == "completion_payload_contract_violation"
    assert payload["source_event_id"] == source.id
    assert payload["source_event_type"] == "dev.build.done"
    assert payload["missing_fields"] == ["summary", "changed_files"]
    assert payload["warn_fields"] == []


def test_build_invalid_payload_includes_warnings() -> None:
    source = _ev("dev.build.done")
    payload = build_invalid_event_payload(
        source, missing=["summary"], warnings=["residual_risks"],
    )
    assert payload["warn_fields"] == ["residual_risks"]


# ---------------------------------------------------------------------------
# Backward compat — historical payloads without contract fields don't break
# ---------------------------------------------------------------------------


def test_backward_compat_historical_payload_returns_errors_but_does_not_raise() -> None:
    """Historical events from before this sprint don't have the 6
    fields. validate must report missing list without raising."""
    payload = {"dispatch_id": "disp-1", "role": "dev"}  # legacy shape
    errors = validate_completion_payload(_ev("dev.build.done", payload))
    assert "summary" in errors
    assert "changed_files" in errors
    assert "evidence_refs" in errors


# ---------------------------------------------------------------------------
# Integration: orchestrator emits task.contract.invalid
# ---------------------------------------------------------------------------


def test_orchestrator_has_validate_method() -> None:
    """Wire-up grep: Orchestrator must declare
    _validate_completion_payload_contract."""
    from zf.runtime.orchestrator import Orchestrator
    assert hasattr(Orchestrator, "_validate_completion_payload_contract")


def test_apply_housekeeping_calls_validate() -> None:
    """Source-level grep that _apply_housekeeping wires the check."""
    import inspect
    from zf.runtime.orchestrator import Orchestrator
    source = inspect.getsource(Orchestrator._apply_housekeeping)
    assert "_validate_completion_payload_contract" in source


def test_validate_emits_task_contract_invalid() -> None:
    """End-to-end: _validate_completion_payload_contract on an
    incomplete dev.build.done emits task.contract.invalid."""
    from zf.runtime.orchestrator import Orchestrator

    class _FakeWriter:
        def __init__(self):
            self.appended = []
        def append(self, ev):
            self.appended.append(ev)

    class _FakeOrch:
        event_writer = _FakeWriter()

    orch = _FakeOrch()
    bad_event = _ev("dev.build.done", {"role": "dev"})  # no required fields
    Orchestrator._validate_completion_payload_contract(orch, bad_event)  # type: ignore[arg-type]
    assert len(orch.event_writer.appended) == 1
    emitted = orch.event_writer.appended[0]
    assert emitted.type == "task.contract.invalid"
    assert emitted.payload["reason"] == "completion_payload_contract_violation"
    assert "summary" in emitted.payload["missing_fields"]


def test_validate_skips_complete_payload() -> None:
    from zf.runtime.orchestrator import Orchestrator

    class _FakeWriter:
        def __init__(self):
            self.appended = []
        def append(self, ev):
            self.appended.append(ev)

    class _FakeOrch:
        event_writer = _FakeWriter()

    orch = _FakeOrch()
    good_event = _ev("dev.build.done", {
        "summary": "did the thing",
        "changed_files": ["x.py"],
        "evidence_refs": [{"path": "abc"}],
    })
    Orchestrator._validate_completion_payload_contract(orch, good_event)  # type: ignore[arg-type]
    assert orch.event_writer.appended == []


def test_validate_skips_fanout_child_completion_payload() -> None:
    from zf.runtime.orchestrator import Orchestrator

    class _FakeWriter:
        def __init__(self):
            self.appended = []
        def append(self, ev):
            self.appended.append(ev)

    class _FakeOrch:
        event_writer = _FakeWriter()

    orch = _FakeOrch()
    event = _ev("review.approved", {
        "fanout_id": "fanout-review",
        "child_id": "review-a",
        "status": "completed",
        "report": {"recommendation": "approve"},
    })
    Orchestrator._validate_completion_payload_contract(orch, event)  # type: ignore[arg-type]
    assert orch.event_writer.appended == []


def test_validate_skips_non_completion_event() -> None:
    from zf.runtime.orchestrator import Orchestrator

    class _FakeWriter:
        def __init__(self):
            self.appended = []
        def append(self, ev):
            self.appended.append(ev)

    class _FakeOrch:
        event_writer = _FakeWriter()

    orch = _FakeOrch()
    other = _ev("worker.heartbeat", {})
    Orchestrator._validate_completion_payload_contract(orch, other)  # type: ignore[arg-type]
    assert orch.event_writer.appended == []
