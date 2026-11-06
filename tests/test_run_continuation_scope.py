"""Recovery selection must preserve gates without starving independent work."""

import pytest

from tests.test_run_continuation import _actions, _events, _goal
from zf.runtime.run_continuation import build_run_continuation_projection


def _select(actions, gates=()):
    return build_run_continuation_projection(
        _events(), goal=_goal(), pending_actions=actions,
        completion_profile={"pending_human_decisions": list(gates)},
    )["next_operation"]


def test_preflight_blocked_head_does_not_starve_ready_action():
    first = {**_actions()[0], "preflight": {"status": "blocked"}}
    second = {**_actions()[0], "checkpoint_id": "ready-other", "task_id": "T2"}
    assert _select([first, second])["checkpoint_id"] == "ready-other"


def test_task_gate_blocks_its_task_but_not_independent_action():
    first = {**_actions()[0], "task_id": "T1"}
    second = {**first, "task_id": "T2", "checkpoint_id": "ready-other"}
    gate = {"blocking_scope": "task", "task_id": "T1", "checkpoint_id": "owner-gate"}
    assert _select([first, second], [gate])["task_id"] == "T2"
    assert _select([first], [gate]) is None


@pytest.mark.parametrize("gate", [
    {"blocking_scope": "run", "task_id": "T1"},
    {"blocking_scope": "task"},
    {"blocking_scope": "unknown", "task_id": "T1"},
    {},
])
def test_ambiguous_or_run_gate_stays_fail_closed(gate):
    assert _select([{**_actions()[0], "task_id": "T2"}], [gate]) is None


def test_unscoped_action_cannot_escape_task_gate():
    assert _select(_actions(), [{"blocking_scope": "task", "task_id": "T1"}]) is None


def test_task_gate_covers_explicit_dependency_scope():
    action = {**_actions()[0], "task_id": "T2", "blocked_by": ["T1"]}
    assert _select([action], [{"blocking_scope": "task", "task_id": "T1"}]) is None
