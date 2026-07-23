from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_runtime import (
    mark_call_operation_started,
    prepare_call_operation,
)
from zf.runtime.result_submit import (
    ResultSubmitError,
    SemanticResultSubmitService,
    provision_role_submit_credential,
)
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.cli.main import build_parser


def _runtime(tmp_path: Path):
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    log = EventLog(state_dir / "events.jsonl")
    return SimpleNamespace(
        project_root=project_root,
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
        config=SimpleNamespace(
            workflow=SimpleNamespace(flow_metadata={"result_protocol": {"mode": "blocking"}})
        ),
    )


def _running_operation(tmp_path: Path):
    runtime = _runtime(tmp_path)
    token_path = provision_role_submit_credential(runtime.state_dir, "dev-1")
    token = token_path.read_text().strip()
    payload = {
        "workflow_run_id": "run-1",
        "role_instance": "dev-1",
        "fanout_id": "fanout-1",
        "stage_id": "impl",
        "child_id": "dev-1-T1",
        "run_id": "attempt-1",
        "task_id": "T1",
        "canonical_success_event": "dev.build.done",
        "canonical_failure_event": "dev.blocked",
    }
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_writer_child",
        operation_key="dev-1-T1",
        stage_id="impl",
        task_id="T1",
        dispatch_id="attempt-1",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="T1",
        dispatch_id="attempt-1",
    )
    service = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    return runtime, prepared, service, token


def _semantic() -> dict:
    return {
        "verdict": "passed",
        "target_commit": "abc123",
        "changed_files": ["result.txt"],
        "evidence_refs": ["receipt:test"],
        "self_check": {"status": "passed"},
        "known_gaps": [],
        "summary": "implemented",
    }


def test_stdin_semantic_submit_fills_identity_and_emits_canonical_event(tmp_path: Path) -> None:
    runtime, prepared, service, token = _running_operation(tmp_path)
    result = service.submit(
        operation_id=prepared.operation_id,
        semantic_result=_semantic(),
        role_instance="dev-1",
        credential=token,
    )
    events = runtime.event_log.read_all()
    canonical = next(event for event in events if event.id == result.canonical_event_id)
    assert result.canonical_event_type == "dev.build.done"
    assert canonical.payload["workflow_run_id"] == "run-1"
    assert canonical.payload["operation_id"] == prepared.operation_id
    assert canonical.payload["source_commit"] == "abc123"
    assert "implementation_result" not in canonical.payload
    hydrated = hydrate_profiled_control_result_event(runtime.state_dir, canonical)
    assert hydrated.payload["implementation_result"]["task_id"] == "T1"
    assert sum(event.type == "workflow.call.result.admitted" for event in events) == 1
    with pytest.raises(ResultSubmitError) as duplicate:
        service.submit(
            operation_id=prepared.operation_id,
            semantic_result=_semantic(),
            role_instance="dev-1",
            credential=token,
        )
    assert duplicate.value.code == "duplicate_submit"


def test_submit_rejects_other_role_and_stale_credential(tmp_path: Path) -> None:
    runtime, prepared, service, token = _running_operation(tmp_path)
    with pytest.raises(ResultSubmitError) as wrong_role:
        service.submit(
            operation_id=prepared.operation_id,
            semantic_result=_semantic(),
            role_instance="verify-1",
            credential=token,
        )
    assert wrong_role.value.code == "role_mismatch"

    new_token_path = provision_role_submit_credential(runtime.state_dir, "dev-1", rotate=True)
    with pytest.raises(ResultSubmitError) as stale:
        service.submit(
            operation_id=prepared.operation_id,
            semantic_result=_semantic(),
            role_instance="dev-1",
            credential=token,
        )
    assert stale.value.code == "capability_invalid"
    result = service.submit(
        operation_id=prepared.operation_id,
        semantic_result=_semantic(),
        role_instance="dev-1",
        credential=new_token_path.read_text().strip(),
    )
    assert result.canonical_event_type == "dev.build.done"


def test_result_file_requires_exact_regular_scratch_path(tmp_path: Path) -> None:
    runtime, prepared, service, token = _running_operation(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_semantic()))
    with pytest.raises(ResultSubmitError) as escaped:
        service.submit(
            operation_id=prepared.operation_id,
            result_file=outside,
            role_instance="dev-1",
            credential=token,
        )
    assert escaped.value.code == "result_file_outside_scratch"

    scratch = runtime.state_dir / prepared.result_scratch_ref
    scratch.parent.mkdir(parents=True)
    scratch.symlink_to(outside)
    with pytest.raises(ResultSubmitError) as symlink:
        service.submit(
            operation_id=prepared.operation_id,
            result_file=scratch,
            role_instance="dev-1",
            credential=token,
        )
    assert symlink.value.code == "result_file_unsafe"


def test_result_submit_cli_requires_one_input_mode() -> None:
    parser = build_parser()
    args = parser.parse_args([
        "result", "submit", "--operation", "op-1", "--stdin",
    ])
    assert args.operation == "op-1"
    assert args.stdin is True
    assert callable(args.func)


def test_signed_regular_result_scratch_is_ingested(tmp_path: Path) -> None:
    runtime, prepared, service, token = _running_operation(tmp_path)
    scratch = runtime.state_dir / prepared.result_scratch_ref
    scratch.parent.mkdir(parents=True)
    scratch.write_text(json.dumps(_semantic()))
    result = service.submit(
        operation_id=prepared.operation_id,
        result_file=scratch,
        role_instance="dev-1",
        credential=token,
    )
    assert result.canonical_event_type == "dev.build.done"
