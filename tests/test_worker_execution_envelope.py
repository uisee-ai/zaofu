from types import SimpleNamespace

import pytest

from zf.runtime.worker_execution_envelope import (
    SCHEMA_VERSION,
    WorkerExecutionEnvelopeError,
    build_worker_execution_envelope,
    envelope_digest,
    normalize_worker_execution_envelope,
)


def _payload() -> dict[str, object]:
    return {
        "workflow_run_id": "run-1",
        "task_id": "TASK-1",
        "task_pipeline_stage": "impl",
        "operation_id": "op-1",
        "dispatch_id": "dispatch-1",
        "attempt_id": "attempt-1",
        "task_map_generation": "map-1",
        "operation_generation": 1,
        "placement_epoch": 1,
        "workdir": "/tmp/project",
        "source_branch": "task/TASK-1",
        "base_commit": "a" * 40,
        "target_ref": "a" * 40,
        "workspace_generation": 1,
        "output_profile_id": "implementation",
        "output_profile_revision": "1",
        "canonical_success_event": "dev.build.done",
        "canonical_failure_event": "dev.blocked",
    }


def _role() -> SimpleNamespace:
    return SimpleNamespace(
        instance_id="dev-0",
        name="dev",
        backend="codex",
        model="model-a",
        model_reasoning_effort="high",
        skills=["worker", "worker"],
    )


def _profile() -> SimpleNamespace:
    return SimpleNamespace(profile_id="bounded-direct-v1", profile_digest="b" * 64)


def test_build_and_normalize_worker_execution_envelope_is_replayable() -> None:
    envelope = build_worker_execution_envelope(
        payload=_payload(), role=_role(), execution_profile=_profile()
    )
    assert envelope["schema_version"] == SCHEMA_VERSION
    assert envelope["routing"]["skills"] == ["worker"]
    assert envelope["routing"]["frontier_owner"] == "run_manager"
    assert envelope["lifecycle"]["unknown_is_not_idle"] is True
    assert envelope["wake_contract"]["idle_probe_event"] == "worker.probe.idle"
    assert envelope["envelope_digest"] == envelope_digest(envelope)
    assert normalize_worker_execution_envelope(envelope) == envelope


def test_worker_execution_envelope_preserves_wrc_frontier_owner() -> None:
    payload = _payload()
    payload["frontier_owner"] = "wrc"
    envelope = build_worker_execution_envelope(
        payload=payload, role=_role(), execution_profile=_profile()
    )

    assert envelope["routing"]["frontier_owner"] == "wrc"
    assert normalize_worker_execution_envelope(envelope) == envelope


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("identity", "task_id"), ""),
        (("result_contract", "evidence_required"), False),
        (("lifecycle", "unknown_is_not_idle"), False),
        (("wake_contract", "unknown_state"), "idle"),
    ],
)
def test_worker_execution_envelope_fails_closed(path, value) -> None:
    envelope = build_worker_execution_envelope(
        payload=_payload(), role=_role(), execution_profile=_profile()
    )
    envelope[path[0]][path[1]] = value
    with pytest.raises(WorkerExecutionEnvelopeError):
        normalize_worker_execution_envelope(envelope, verify_digest=False)


def test_worker_execution_envelope_rejects_digest_mutation() -> None:
    envelope = build_worker_execution_envelope(
        payload=_payload(), role=_role(), execution_profile=_profile()
    )
    envelope["workspace"]["target_ref"] = "c" * 40
    with pytest.raises(WorkerExecutionEnvelopeError, match="digest"):
        normalize_worker_execution_envelope(envelope)


def test_worker_wake_contract_uses_registered_wake_surface() -> None:
    from zf.core.events.known_types import KNOWN_EVENT_TYPES
    from zf.runtime.wake_patterns import WAKE_PATTERNS

    envelope = build_worker_execution_envelope(
        payload=_payload(), role=_role(), execution_profile=_profile()
    )
    wake = envelope["wake_contract"]
    for event_type in [
        *wake["completion_events"],
        *wake["recovery_events"],
        wake["idle_probe_event"],
    ]:
        assert event_type in KNOWN_EVENT_TYPES
        assert event_type in WAKE_PATTERNS
