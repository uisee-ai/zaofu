"""A declared task partition format cannot silently degrade into whole reads."""

import hashlib
import json

import pytest

from zf.runtime.plan_candidate_ports import plan_port_bodies
from zf.runtime.task_scoped_plan_inputs import task_input_paths


@pytest.mark.parametrize("by_ref", [False, True])
@pytest.mark.parametrize(
    "body",
    [
        {"shared": {}, "tasks": {"T1": {"behavior": "save"}}},
        {"schema_version": "legacy.v1", "shared": {}, "tasks": []},
    ],
)
def test_declared_task_schema_must_match_body(tmp_path, body, by_ref):
    state = tmp_path / ".ui-state"
    state.mkdir()
    descriptor = {
        "logical_name": "execution_spec",
        "schema_version": "task-scoped-plan-input.v1",
    }
    if by_ref:
        raw = json.dumps(body).encode()
        path = state / "artifacts/spec.json"
        path.parent.mkdir()
        path.write_bytes(raw)
        descriptor.update(
            ref="artifacts/spec.json", sha256=hashlib.sha256(raw).hexdigest()
        )
    else:
        descriptor["body"] = body
    errors = []
    plan_port_bodies(
        [{"plan_ports": [descriptor]}],
        trigger={},
        state_dir=state,
        project_root=tmp_path,
        errors=errors,
    )
    assert any(e["code"] == "plan_port_schema_mismatch" for e in errors)


def test_valid_partition_and_undeclared_legacy_remain_readable(tmp_path):
    body = {
        "schema_version": "task-scoped-plan-input.v1",
        "shared": {"rule": "R1"},
        "tasks": [
            {
                "task_id": "T1",
                "body": {
                    "human_review": {"module_spec_markdown": "## 保存\n失败保留草稿。"}
                },
            }
        ],
    }
    errors = []
    ports = plan_port_bodies(
        [
            {
                "plan_ports": [
                    {
                        "logical_name": "execution_spec",
                        "schema_version": body["schema_version"],
                        "body": body,
                    },
                    {
                        "logical_name": "legacy_input",
                        "body": {"behavior": "legacy whole input"},
                    },
                ]
            }
        ],
        trigger={},
        state_dir=tmp_path / ".ui-state",
        project_root=tmp_path,
        errors=errors,
    )
    assert errors == []
    assert task_input_paths(ports["execution_spec"], task_id="T1") == [
        "$.shared",
        "$.tasks[0]",
    ]
    assert task_input_paths(ports["legacy_input"], task_id="T1") == ["$"]
