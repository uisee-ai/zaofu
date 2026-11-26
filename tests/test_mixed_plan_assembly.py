import pytest

from zf.runtime.plan_digest import plan_digest_checklist
from zf.runtime.task_map import validate_task_map_payload


def task_map(*, audit_changes=None, third_writer=False):
    audit = {"task_id": "AUDIT", "owner_role": "audit-team", "allowed_paths": [],
             "allowed_paths_reason": "Read the frozen modules", "execution_mode": "verify_only",
             "evidence_contract": {"write_free": True}, "verification": "pytest"}
    audit.update(audit_changes or {})
    tasks = [
        audit, {"task_id": "FEATURE", "owner_role": "features",
                "allowed_paths": ["src/one.py"], "verification": "pytest"},
    ]
    if third_writer:
        tasks.append({"task_id": "SECOND", "owner_role": "feature-two",
                      "allowed_paths": ["src/two.py"], "verification": "pytest"})
    return {"schema_version": "task-map.v1", "tasks": tasks}


def assembly_check(payload):
    return next(c for c in plan_digest_checklist(payload["tasks"]) if c["key"] == "assembly_present")


def test_distinct_read_only_owner_keeps_task_and_independent_evidence():
    payload = task_map()
    result = validate_task_map_payload(payload)
    assert result.passed, result.errors
    assert result.summary["task_count"] == 2 and result.summary["bundle_owner_count"] == 1
    assert assembly_check(payload)["ok"] is True


@pytest.mark.parametrize("overrides", [
    {"evidence_contract": {}}, {"allowed_paths": ["src/edit.py"]},
    {"exclusive_files": ["package.json"]}, {"allowed_paths": None},
    {"execution_mode": "standard"}, {"execution_mode": "external_gate"},
    {"write_free": False}, {"evidence_contract": {"write_free": "true"}},
])
def test_ambiguous_or_writable_reviewer_still_requires_assembly(overrides):
    payload = task_map(audit_changes=overrides)
    assert any("缺 assembly" in e for e in validate_task_map_payload(payload).errors)
    assert assembly_check(payload)["ok"] is False


def test_two_writers_still_require_assembly_with_a_readonly_reviewer():
    payload = task_map(third_writer=True)
    assert any("缺 assembly" in e for e in validate_task_map_payload(payload).errors)
    assert assembly_check(payload)["ok"] is False


def test_explicit_assembly_cannot_reuse_dependency_owner():
    payload = task_map()
    payload["tasks"].append({"task_id":"ASSEMBLY", "owner_role":"audit-team", "root_owner_class":"assembly",
                             "blocked_by":["AUDIT","FEATURE"],"verification":"pytest"})
    assert any("自锁" in e for e in validate_task_map_payload(payload).errors)
    assert assembly_check(payload)["ok"] is False
