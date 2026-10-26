"""Reject unready enriched input before spending an independent review."""

from pathlib import Path

import pytest

from tests.test_plan_candidate_preflight import (
    _matrix_metadata,
    _plan_ports,
    _source_index,
    _task_map,
)
from tests.test_result_submit import (
    _plan_candidate,
    _running_plan_candidate_operation,
)
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.result_submit import ResultSubmitError


@pytest.mark.parametrize("port", ["task_map", "source_inventory"])
@pytest.mark.parametrize(
    ("status", "enrichment", "expected"),
    [
        ("draft", "fulfilled", "matrix_not_ready"),
        ("ready", "pending", "matrix_enrichment_unfulfilled"),
        ("ready", "fulfilled", None),
    ],
)
def test_enriched_port_readiness_before_critic(
    tmp_path: Path,
    port: str,
    status: str,
    enrichment: str,
    expected: str | None,
) -> None:
    task_map = _task_map()
    body = task_map if port == "task_map" else {"sources": []}
    body.update(
        {
            "status": status,
            "metadata": {"enrichment_contract": {"status": enrichment}},
        }
    )
    ports = _plan_ports()
    if port != "task_map":
        ports.append({"logical_name": port, "body": body})
    metadata = _matrix_metadata()
    metadata["artifact_package"]["required_ports"].append(port)
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[
            {
                "report": {
                    "task_map": task_map,
                    "source_index": _source_index(),
                    "plan_ports": ports,
                    "plan_md": "# Plan\nImplement TASK-1.",
                }
            }
        ],
        manifest={
            "trigger_payload": {
                "flow_kind": "prd",
                "prd_ref": "docs/requirement.md",
            }
        },
        metadata=metadata,
    )
    if expected:
        assert result["status"] == "failed"
        assert any(
            row["code"] == expected and row["field"].startswith(f"plan_ports.{port}.")
            for row in result["errors"]
        )
    else:
        assert result["status"] == "passed", result["errors"]


def test_draft_task_map_can_be_repaired_in_same_submit_attempt(tmp_path: Path) -> None:
    runtime, prepared, service, token = _running_plan_candidate_operation(tmp_path)
    semantic = _plan_candidate()
    semantic["task_map"].update(
        {
            "status": "draft",
            "metadata": {"enrichment_contract": {"status": "fulfilled"}},
        }
    )
    with pytest.raises(ResultSubmitError) as rejected:
        service.submit(
            operation_id=prepared.operation_id,
            semantic_result=semantic,
            role_instance="planner",
            credential=token,
        )
    assert rejected.value.code == "plan_candidate_not_admitted"
    assert not any(
        event.type == "workflow.call.result.admitted"
        for event in runtime.event_log.read_all()
    )
    semantic["task_map"]["status"] = "ready"
    admitted = service.submit(
        operation_id=prepared.operation_id,
        semantic_result=semantic,
        role_instance="planner",
        credential=token,
    )
    assert admitted.canonical_event_type == "issue.triage.child.completed"
