"""Tests for Task schema."""

from __future__ import annotations

from zf.core.task.schema import Task, TaskContract


def test_task_defaults():
    t = Task(title="Fix login bug")
    assert t.id  # auto-generated
    assert t.title == "Fix login bug"
    assert t.status == "backlog"
    assert t.assigned_to is None
    assert t.blocked_by == []
    assert t.evidence is None


def test_task_with_contract():
    t = Task(
        title="Add auth",
        contract=TaskContract(
            behavior="JWT refresh works",
            verification="pytest tests/test_auth.py",
            scope=["src/auth/**"],
            acceptance="exit_code=0",
            owner_role="dev",
            wave=2,
            shared_files=["src/auth/types.py"],
            exclusive_files=["src/auth/refresh.py"],
            handoff_artifacts=["docs/auth-refresh.md"],
        ),
    )
    assert t.contract.behavior == "JWT refresh works"
    assert t.contract.scope == ["src/auth/**"]
    assert t.contract.wave == 2
    assert t.contract.shared_files == ["src/auth/types.py"]
    assert t.contract.exclusive_files == ["src/auth/refresh.py"]
    assert t.contract.handoff_artifacts == ["docs/auth-refresh.md"]


def test_task_contract_validation_field_defaults_to_empty_dict():
    c = TaskContract()
    assert c.validation == {}


def test_task_contract_validation_field_accepts_structured_spec():
    c = TaskContract(
        validation={
            "kind": "byte_exact",
            "path": "proof.txt",
            "expected": "ok",
        },
    )
    assert c.validation["kind"] == "byte_exact"


def test_task_key():
    t = Task(title="JWT refresh", key="auth:jwt-refresh")
    assert t.key == "auth:jwt-refresh"


def test_task_id_is_unique():
    t1 = Task(title="a")
    t2 = Task(title="b")
    assert t1.id != t2.id


def test_task_timestamps():
    t = Task(title="x")
    assert t.created_at  # auto-set
    assert t.dispatched_at is None
    assert t.completed_at is None
