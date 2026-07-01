"""EVAL-ACCEPTANCE-CRITERIA-001 — per-criterion evidence mapping tests."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.housekeeping import (
    _coerce_acceptance_evidence,
    apply_acceptance_evidence_event,
    apply_task_contract_event,
)


# ---------------------------------------------------------------------------
# Schema — TaskContract has new fields
# ---------------------------------------------------------------------------


def test_task_contract_acceptance_criteria_field_exists() -> None:
    c = TaskContract()
    assert hasattr(c, "acceptance_criteria")
    assert c.acceptance_criteria == []


def test_task_contract_acceptance_evidence_field_exists() -> None:
    c = TaskContract()
    assert hasattr(c, "acceptance_evidence")
    assert c.acceptance_evidence == {}


def test_task_contract_construct_with_criteria() -> None:
    c = TaskContract(
        acceptance_criteria=["c1", "c2", "c3"],
        acceptance_evidence={"c1": ["evt-1"], "c2": ["evt-2"]},
    )
    assert c.acceptance_criteria == ["c1", "c2", "c3"]
    assert c.acceptance_evidence == {"c1": ["evt-1"], "c2": ["evt-2"]}


def test_task_contract_serializes_with_criteria() -> None:
    c = TaskContract(
        acceptance_criteria=["c1"],
        acceptance_evidence={"c1": ["evt-1"]},
    )
    d = asdict(c)
    assert "acceptance_criteria" in d
    assert "acceptance_evidence" in d
    assert d["acceptance_criteria"] == ["c1"]
    assert d["acceptance_evidence"] == {"c1": ["evt-1"]}


# ---------------------------------------------------------------------------
# _coerce_acceptance_evidence helper
# ---------------------------------------------------------------------------


def test_coerce_none_returns_empty_dict() -> None:
    assert _coerce_acceptance_evidence(None) == {}


def test_coerce_non_dict_returns_existing() -> None:
    """Bad value falls back to existing."""
    assert _coerce_acceptance_evidence(
        "invalid", existing={"c1": ["evt-1"]},
    ) == {"c1": ["evt-1"]}


def test_coerce_merges_with_existing() -> None:
    """New refs append to existing buckets."""
    out = _coerce_acceptance_evidence(
        {"c1": ["evt-2"]},
        existing={"c1": ["evt-1"]},
    )
    assert out["c1"] == ["evt-1", "evt-2"]


def test_coerce_dedups_existing_refs() -> None:
    out = _coerce_acceptance_evidence(
        {"c1": ["evt-1", "evt-2", "evt-1"]},  # evt-1 dup
        existing={"c1": ["evt-1"]},
    )
    assert out["c1"] == ["evt-1", "evt-2"]


def test_coerce_strips_empty_refs() -> None:
    out = _coerce_acceptance_evidence(
        {"c1": ["", "evt-2", "  "]},
    )
    assert out["c1"] == ["evt-2"]


def test_coerce_strips_empty_keys() -> None:
    out = _coerce_acceptance_evidence(
        {"": ["evt-1"], "c1": ["evt-2"]},
    )
    assert "" not in out
    assert out["c1"] == ["evt-2"]


def test_coerce_ignores_non_list_refs() -> None:
    out = _coerce_acceptance_evidence(
        {"c1": "not-a-list"},
    )
    assert "c1" not in out


# ---------------------------------------------------------------------------
# apply_task_contract_event — acceptance_criteria flows through
# ---------------------------------------------------------------------------


def _new_store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "kanban.json")


def _add_task(store: TaskStore, task_id: str = "TASK-AC") -> Task:
    t = Task(
        id=task_id, title="demo", status="in_progress",
        contract=TaskContract(behavior="do thing"),
    )
    store.add(t)
    return t


def test_contract_update_sets_acceptance_criteria(tmp_path: Path) -> None:
    store = _new_store(tmp_path)
    _add_task(store)
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="TASK-AC",
        payload={
            "contract": {
                "behavior": "do thing",
                "acceptance_criteria": ["criterion 1", "criterion 2"],
            }
        },
    )
    apply_task_contract_event(store, event)
    refreshed = store.get("TASK-AC")
    assert refreshed.contract.acceptance_criteria == [
        "criterion 1", "criterion 2",
    ]


def test_contract_update_sets_acceptance_evidence(tmp_path: Path) -> None:
    store = _new_store(tmp_path)
    _add_task(store)
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="TASK-AC",
        payload={
            "contract": {
                "behavior": "do thing",
                "acceptance_criteria": ["c1"],
                "acceptance_evidence": {"c1": ["evt-existing"]},
            }
        },
    )
    apply_task_contract_event(store, event)
    refreshed = store.get("TASK-AC")
    assert refreshed.contract.acceptance_evidence == {"c1": ["evt-existing"]}


# ---------------------------------------------------------------------------
# apply_acceptance_evidence_event — merge from review/test/judge events
# ---------------------------------------------------------------------------


def test_event_outside_completion_types_noop(tmp_path: Path) -> None:
    store = _new_store(tmp_path)
    _add_task(store)
    event = ZfEvent(
        type="worker.heartbeat", actor="dev-1", task_id="TASK-AC",
        payload={"acceptance_evidence_update": {"c1": ["evt-1"]}},
    )
    apply_acceptance_evidence_event(store, event)
    refreshed = store.get("TASK-AC")
    assert refreshed.contract.acceptance_evidence == {}


def test_event_without_payload_noop(tmp_path: Path) -> None:
    store = _new_store(tmp_path)
    _add_task(store)
    event = ZfEvent(
        type="review.approved", actor="review", task_id="TASK-AC",
        payload={},
    )
    apply_acceptance_evidence_event(store, event)
    refreshed = store.get("TASK-AC")
    assert refreshed.contract.acceptance_evidence == {}


def test_review_event_merges_acceptance_evidence(tmp_path: Path) -> None:
    store = _new_store(tmp_path)
    task = _add_task(store)
    task.contract = TaskContract(
        behavior="do thing",
        acceptance_criteria=["c1", "c2"],
    )
    store.update(task.id, contract=asdict(task.contract))

    event = ZfEvent(
        type="review.approved", actor="review", task_id="TASK-AC",
        payload={"acceptance_evidence_update": {"c1": ["evt-rev-1"]}},
    )
    apply_acceptance_evidence_event(store, event)
    refreshed = store.get("TASK-AC")
    assert refreshed.contract.acceptance_evidence == {"c1": ["evt-rev-1"]}


def test_judge_event_accumulates_with_prior_review_evidence(
    tmp_path: Path,
) -> None:
    """Successive review.approved + judge.passed both add evidence."""
    store = _new_store(tmp_path)
    _add_task(store)

    rev_event = ZfEvent(
        type="review.approved", actor="review", task_id="TASK-AC",
        payload={"acceptance_evidence_update": {"c1": ["evt-rev"]}},
    )
    apply_acceptance_evidence_event(store, rev_event)

    judge_event = ZfEvent(
        type="judge.passed", actor="judge", task_id="TASK-AC",
        payload={"acceptance_evidence_update": {
            "c1": ["evt-judge"],
            "c2": ["evt-judge-c2"],
        }},
    )
    apply_acceptance_evidence_event(store, judge_event)
    refreshed = store.get("TASK-AC")
    assert refreshed.contract.acceptance_evidence == {
        "c1": ["evt-rev", "evt-judge"],
        "c2": ["evt-judge-c2"],
    }


def test_source_token_resolves_to_event_id(tmp_path: Path) -> None:
    """A worker can use ``$source`` shorthand referring to its own
    event id."""
    store = _new_store(tmp_path)
    _add_task(store)
    event = ZfEvent(
        type="review.approved", actor="review", task_id="TASK-AC",
        payload={"acceptance_evidence_update": {"c1": ["$source"]}},
    )
    apply_acceptance_evidence_event(store, event)
    refreshed = store.get("TASK-AC")
    assert refreshed.contract.acceptance_evidence["c1"] == [event.id]


def test_idempotent_re_apply(tmp_path: Path) -> None:
    """Re-applying the same event does not double-add evidence."""
    store = _new_store(tmp_path)
    _add_task(store)
    event = ZfEvent(
        type="review.approved", actor="review", task_id="TASK-AC",
        payload={"acceptance_evidence_update": {"c1": ["evt-rev"]}},
    )
    apply_acceptance_evidence_event(store, event)
    apply_acceptance_evidence_event(store, event)
    refreshed = store.get("TASK-AC")
    assert refreshed.contract.acceptance_evidence == {"c1": ["evt-rev"]}


def test_missing_task_noop(tmp_path: Path) -> None:
    store = _new_store(tmp_path)
    event = ZfEvent(
        type="review.approved", actor="review", task_id="TASK-NEVER",
        payload={"acceptance_evidence_update": {"c1": ["evt-1"]}},
    )
    apply_acceptance_evidence_event(store, event)  # must not raise


# ---------------------------------------------------------------------------
# Wire-up grep — Orchestrator._apply_housekeeping invokes the merge
# ---------------------------------------------------------------------------


def test_apply_housekeeping_calls_acceptance_evidence_merge() -> None:
    """Source-level grep proof that orchestrator routes completion
    events to apply_acceptance_evidence_event."""
    import inspect
    from zf.runtime.orchestrator import Orchestrator
    source = inspect.getsource(Orchestrator._apply_housekeeping)
    assert "apply_acceptance_evidence_event" in source
