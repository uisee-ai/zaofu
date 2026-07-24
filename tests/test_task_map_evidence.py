from __future__ import annotations

from zf.runtime.task_map import validate_task_map_payload
from zf.runtime.task_map_evidence import stable_topological_order


def _task(task_id, *, blocked_by=(), owner="dev", paths=()):
    return {
        "task_id": task_id,
        "title": task_id,
        "owner_role": owner,
        "blocked_by": list(blocked_by),
        "allowed_paths": list(paths),
        "allowed_paths_reason": "task contract",
        "acceptance": ["done"],
    }


def test_stable_topology_accepts_same_wave_forward_reference():
    task_map = {
        "schema_version": "task-map.v1",
        "tasks": [
            _task("verify", blocked_by=["producer"]),
            _task("producer"),
        ],
    }

    order, errors = stable_topological_order(task_map)

    assert errors == []
    assert order == ["producer", "verify"]


def test_task_map_rejects_self_and_full_cycle_before_materialization():
    self_cycle = {
        "schema_version": "task-map.v1",
        "tasks": [_task("a", blocked_by=["a"])],
    }
    full_cycle = {
        "schema_version": "task-map.v1",
        "tasks": [
            _task("a", blocked_by=["b"]),
            _task("b", blocked_by=["a"]),
        ],
    }

    assert any("self-cycle" in item for item in validate_task_map_payload(self_cycle).errors)
    assert any("dependency cycle" in item for item in validate_task_map_payload(full_cycle).errors)


def test_evidence_producer_must_own_path_and_be_dependency():
    consumer = _task("verify", owner="verify")
    consumer["verification_commands"] = [
        {"command_id": "pw", "command": "npm test"},
    ]
    consumer["acceptance_criteria"] = [{
        "acceptance_id": "AC1",
        "text": "video exists",
        "verification_owner": "verify",
        "verification_command_ids": ["pw"],
        "evidence_requirements": [{
            "artifact_kind": "playwright_video",
            "producer_task_id": "producer",
            "producer_paths": ["artifacts/video/out.webm"],
            "independence_required": True,
        }],
    }]
    task_map = {
        "schema_version": "task-map.v1",
        "tasks": [
            consumer,
            _task("producer", owner="dev", paths=["src/**"]),
        ],
    }

    errors = validate_task_map_payload(task_map).errors

    assert any("does not own evidence path" in item for item in errors)
    assert any("does not depend on evidence producer" in item for item in errors)
