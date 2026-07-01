"""ZF-TR-NESTED-GUARD-001 (doc 40 §6 I55) — recursion guard in worker briefings.

Every worker briefing (any role except ``orchestrator``) must contain:
1. ``Active task: <task_id>`` as the literal first line
2. A ``## Recursion Guard (强制)`` section with the 3 hard boundaries
"""

from __future__ import annotations

import pytest

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.task.schema import Task, TaskContract
from zf.runtime.injection import generate_task_briefing


def _config_with_role(role: RoleConfig) -> ZfConfig:
    """Build the smallest ZfConfig that satisfies generate_task_briefing."""
    return ZfConfig(roles=[role])


def _make_task(task_id: str = "TASK-42", title: str = "demo") -> Task:
    return Task(
        id=task_id,
        title=title,
        status="in_progress",
        contract=TaskContract(behavior="Do the thing"),
    )


def _make_role(
    name: str,
    publishes: list[str] | None = None,
    triggers: list[str] | None = None,
) -> RoleConfig:
    role = RoleConfig(
        name=name,
        role_kind="auto",
        publishes=publishes or [f"{name}.done"],
        triggers=triggers or ["task.dispatched"],
    )
    # instance_id is a runtime attribute not in __init__; assign defensively
    if not getattr(role, "instance_id", ""):
        role.instance_id = f"{name}-1"
    return role


# ---------------------------------------------------------------------------
# Active task first-line marker
# ---------------------------------------------------------------------------


def test_briefing_first_line_is_active_task_marker_for_dev() -> None:
    role = _make_role("dev")
    config = _config_with_role(role)
    briefing = generate_task_briefing(config, role, _make_task("TASK-7"))
    first_line = briefing.splitlines()[0]
    assert first_line == "Active task: TASK-7"


def test_briefing_first_line_is_active_task_marker_for_review() -> None:
    role = _make_role(
        "review",
        publishes=["review.approved", "review.rejected"],
    )
    config = _config_with_role(role)
    briefing = generate_task_briefing(config, role, _make_task("TASK-X"))
    assert briefing.splitlines()[0] == "Active task: TASK-X"


def test_briefing_first_line_consistent_across_roles() -> None:
    """All worker roles share the same first-line shape — recovery
    scripts grep this literally."""
    for role_name in ("dev", "review", "test", "judge", "arch", "critic"):
        role = _make_role(role_name)
        config = _config_with_role(role)
        briefing = generate_task_briefing(
            config, role, _make_task(f"TASK-{role_name}")
        )
        first = briefing.splitlines()[0]
        assert first.startswith("Active task: "), (
            f"role={role_name} first line was {first!r}"
        )
        assert f"TASK-{role_name}" in first


# ---------------------------------------------------------------------------
# Recursion guard section presence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role_name,publishes",
    [
        ("dev", ["dev.build.done"]),
        ("review", ["review.approved", "review.rejected"]),
        ("test", ["test.passed", "test.failed"]),
        ("judge", ["judge.passed", "judge.failed"]),
        ("arch", ["arch.proposal.done"]),
        ("critic", ["design.critique.done"]),
    ],
)
def test_recursion_guard_section_present_for_worker_roles(
    role_name: str, publishes: list[str]
) -> None:
    role = _make_role(role_name, publishes=publishes)
    config = _config_with_role(role)
    briefing = generate_task_briefing(config, role, _make_task())
    assert "## Recursion Guard" in briefing, (
        f"role={role_name} missing Recursion Guard section"
    )


def test_recursion_guard_lists_three_boundaries() -> None:
    role = _make_role("dev")
    config = _config_with_role(role)
    briefing = generate_task_briefing(config, role, _make_task())

    # Boundary 1: nested same-role sub-agent forbidden
    assert "No nested dev sub-agent" in briefing
    # Boundary 2: no direct truth mutation
    assert "No direct truth mutation" in briefing
    assert "TaskStore" in briefing
    assert ".zf/events.jsonl" in briefing
    # Boundary 3: no self-declared release
    assert "No self-declared release" in briefing
    assert "ship.*" in briefing


def test_recursion_guard_substitutes_role_name_dev() -> None:
    role = _make_role("dev")
    config = _config_with_role(role)
    briefing = generate_task_briefing(config, role, _make_task())
    assert "No nested dev sub-agent" in briefing
    # No other role names should appear in boundary 1's instruction
    assert "No nested review sub-agent" not in briefing


def test_recursion_guard_substitutes_role_name_review() -> None:
    role = _make_role("review", publishes=["review.approved"])
    config = _config_with_role(role)
    briefing = generate_task_briefing(config, role, _make_task())
    assert "No nested review sub-agent" in briefing


def test_recursion_guard_invariant_ordering() -> None:
    """Recursion guard must follow the Heartbeat section but come
    before the judge completion audit (when applicable)."""
    role = _make_role("judge", publishes=["judge.passed", "judge.failed"])
    config = _config_with_role(role)
    briefing = generate_task_briefing(config, role, _make_task())
    idx_heartbeat = briefing.find("## Periodic Heartbeat")
    idx_guard = briefing.find("## Recursion Guard")
    idx_audit = briefing.find("## Judge Completion Audit")
    assert -1 < idx_heartbeat < idx_guard, (
        "Recursion Guard should come after Heartbeat"
    )
    if idx_audit != -1:
        assert idx_guard < idx_audit, (
            "Recursion Guard should come before Judge Completion Audit"
        )
