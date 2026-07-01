"""P2-1: infer_completion_protocol — completion events derived from
role.publishes, not a hardcoded role-name map.

Verifies the injection layer works for custom YAML roles (e.g. `doc`
publishing `doc.updated`) without requiring Python changes.
"""

from __future__ import annotations

import pytest

from zf.core.config.schema import RoleConfig
from zf.runtime.injection import (
    CompletionProtocol,
    infer_completion_protocol,
)


def _role(name: str, publishes: list[str]) -> RoleConfig:
    return RoleConfig(name=name, publishes=publishes)


def test_dev_uses_done_suffix():
    p = infer_completion_protocol(_role("dev", ["dev.build.done", "dev.blocked"]))
    assert p.success_event == "dev.build.done"
    assert p.failure_event == "dev.blocked"


def test_review_uses_approved_and_rejected():
    p = infer_completion_protocol(_role(
        "review",
        ["review.approved", "review.rejected", "review.suspended",
         "design.critique.done"],
    ))
    # approved has higher priority than done
    assert p.success_event == "review.approved"
    assert p.failure_event == "review.rejected"
    assert p.suspend_event == "review.suspended"
    assert "design.critique.done" in p.other_events


def test_test_uses_passed_failed_suspended():
    p = infer_completion_protocol(_role(
        "test", ["test.passed", "test.failed", "test.suspended"]
    ))
    assert p.success_event == "test.passed"
    assert p.failure_event == "test.failed"
    assert p.suspend_event == "test.suspended"


def test_judge_uses_passed_failed():
    p = infer_completion_protocol(_role(
        "judge", ["judge.passed", "judge.failed"]
    ))
    assert p.success_event == "judge.passed"
    assert p.failure_event == "judge.failed"
    assert p.suspend_event is None


def test_custom_role_doc_with_updated_suffix():
    """Role with no .approved/.done/.passed picks first non-failure publish."""
    p = infer_completion_protocol(_role("doc", ["doc.updated", "doc.blocked"]))
    # Pick first positive publish since no recognized success suffix
    assert p.success_event == "doc.updated"
    assert p.failure_event == "doc.blocked"


def test_custom_role_doc_with_done():
    p = infer_completion_protocol(_role("doc", ["doc.done", "doc.failed"]))
    assert p.success_event == "doc.done"
    assert p.failure_event == "doc.failed"


def test_approved_beats_done():
    p = infer_completion_protocol(_role(
        "mixer", ["mixer.done", "mixer.approved"]
    ))
    assert p.success_event == "mixer.approved"


def test_rejected_beats_failed():
    p = infer_completion_protocol(_role(
        "mixer", ["mixer.approved", "mixer.failed", "mixer.rejected"]
    ))
    assert p.failure_event == "mixer.rejected"


def test_empty_publishes_falls_back():
    p = infer_completion_protocol(_role("odd", []))
    assert p.success_event == "odd.done"
    assert p.failure_event is None
    assert p.suspend_event is None


def test_arch_role_without_failure_event():
    p = infer_completion_protocol(_role(
        "arch", ["arch.proposal.done", "clarification.needed"]
    ))
    assert p.success_event == "arch.proposal.done"
    # `clarification.needed` doesn't match failure suffixes
    assert p.failure_event is None
    assert "clarification.needed" in p.other_events


def test_completion_protocol_used_in_task_briefing():
    """Full integration: briefing text contains the inferred events."""
    from zf.core.config.schema import ZfConfig, ProjectConfig
    from zf.core.task.schema import Task, TaskContract
    from zf.runtime.injection import generate_task_briefing

    role = RoleConfig(
        name="doc",
        instance_id="doc",
        publishes=["doc.updated", "doc.failed", "doc.suspended"],
    )
    task = Task(
        id="T1", title="Update docs",
        contract=TaskContract(behavior="update README"),
    )
    config = ZfConfig(project=ProjectConfig(name="test"), roles=[role])

    briefing = generate_task_briefing(config, role, task)
    assert "doc.updated" in briefing
    assert "doc.failed" in briefing
    assert "doc.suspended" in briefing
    # Old hardcoded string should NOT appear
    assert "dev.build.done" not in briefing


def test_completion_protocol_suspended_hint_only_when_published():
    """Role without .suspended publish doesn't get SUSPEND hint."""
    from zf.core.config.schema import ZfConfig, ProjectConfig
    from zf.core.task.schema import Task, TaskContract
    from zf.runtime.injection import generate_role_instructions

    role = RoleConfig(
        name="dev",
        instance_id="dev",
        publishes=["dev.build.done", "dev.blocked"],
    )
    task = Task(id="T1", title="x", contract=TaskContract(behavior="b"))
    config = ZfConfig(project=ProjectConfig(name="test"), roles=[role])

    out = generate_role_instructions(config, role, task=task)
    assert "SUSPEND" not in out
    assert "dev.build.done" in out
    assert "dev.blocked" in out
