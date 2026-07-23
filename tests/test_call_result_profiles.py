from __future__ import annotations

from pathlib import Path

import pytest

from zf.runtime.call_result_adapters import (
    ControlResultAdapterError,
    ControlResultAdapterRegistry,
    call_result_profile_identity,
    hydrate_profiled_control_result_event,
)
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


def test_profile_identity_distinguishes_verify_surfaces() -> None:
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="task-verify",
        payload={},
    ) == ("task-verify", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="candidate-verify",
        payload={},
    ) == ("candidate-verify", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="global-rescan",
        payload={},
    ) == ("global-rescan", "1")
    assert call_result_profile_identity(
        operation_type="fanout_synth",
        stage_id="plan",
        payload={},
    ) == ("plan-synth", "1")


def test_profile_revision_and_event_mapping_fail_closed(tmp_path: Path) -> None:
    registry = ControlResultAdapterRegistry()
    with pytest.raises(ControlResultAdapterError, match="unknown call-result profile"):
        registry.profile("task-verify", "99")
    with pytest.raises(ControlResultAdapterError, match="not allowed"):
        registry.adapt_semantic_result(
            tmp_path,
            profile_id="implementation",
            revision="1",
            event_type="judge.child.completed",
            semantic_result={"task_id": "T1", "target_commit": "abc"},
            identity={"workflow_run_id": "run-1"},
            source_event_id="evt-source",
            actor="dev-1",
            task_id="T1",
            correlation_id="run-1",
        )


def test_ref_backed_event_hydrates_once_and_hash_mismatch_fails(tmp_path: Path) -> None:
    descriptor = write_immutable_json_sidecar(
        tmp_path,
        {
            "schema_version": "implementation-result.v1",
            "task_id": "T1",
            "target_commit": "abc123",
        },
        root="call-results/control/implementation-result.v1",
        kind="call_control_result",
        schema_version="implementation-result.v1",
        created_by="test",
        source_event_id="evt-source",
    )
    event = ZfEvent(
        type="dev.build.done",
        payload={
            "semantic_result_profile": {
                "profile_id": "implementation",
                "revision": "1",
            },
            "control_result_ref": descriptor,
        },
    )
    hydrated = hydrate_profiled_control_result_event(tmp_path, event)
    assert hydrated.payload["implementation_result"]["target_commit"] == "abc123"
    broken = ZfEvent(
        type=event.type,
        payload={
            **event.payload,
            "control_result_ref": {**descriptor, "sha256": "0" * 64},
        },
    )
    with pytest.raises(ControlResultAdapterError, match="hydration failed"):
        hydrate_profiled_control_result_event(tmp_path, broken)
