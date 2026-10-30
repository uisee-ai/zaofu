"""Immutable worker execution contract for blocking Task Pipeline dispatch.

The envelope captures routing and lifecycle facts at dispatch time.  It is
deliberately provider-neutral: raw model reasoning remains in provider
sidecars, while the envelope is safe to persist in the EventLog and briefing.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


SCHEMA_VERSION = "worker-execution-envelope.v1"
_DIGEST_KEYS = (
    "workflow_run_id",
    "task_id",
    "task_pipeline_stage",
    "operation_id",
    "dispatch_id",
    "attempt_id",
    "task_map_generation",
    "operation_generation",
    "placement_epoch",
)
_REQUIRED_SECTIONS = ("identity", "routing", "workspace", "result_contract", "lifecycle", "wake_contract")


class WorkerExecutionEnvelopeError(ValueError):
    """Raised when a worker execution envelope cannot be admitted."""


def build_worker_execution_envelope(
    *,
    payload: Mapping[str, Any],
    role: Any,
    execution_profile: Any,
) -> dict[str, Any]:
    """Build the dispatch snapshot consumed by the transport and worker."""

    success_event = str(payload.get("canonical_success_event") or "").strip()
    failure_event = str(payload.get("canonical_failure_event") or "").strip()
    if not success_event or not failure_event:
        raise WorkerExecutionEnvelopeError(
            "worker envelope requires canonical success and failure events"
        )

    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "identity": {
            key: _required_payload_string(payload, key)
            for key in _DIGEST_KEYS
        },
        "routing": {
            "role_instance": str(getattr(role, "instance_id", "") or "").strip(),
            "role_name": str(getattr(role, "name", "") or "").strip(),
            "backend": str(getattr(role, "backend", "") or "").strip(),
            "model": str(getattr(role, "model", "") or "").strip(),
            "thinking_effort": str(
                getattr(role, "model_reasoning_effort", "") or ""
            ).strip(),
            "execution_profile_id": str(
                getattr(execution_profile, "profile_id", "") or ""
            ).strip(),
            "execution_profile_digest": str(
                getattr(execution_profile, "profile_digest", "") or ""
            ).strip(),
            "frontier_owner": str(payload.get("frontier_owner") or "run_manager").strip(),
            "skills": sorted({
                str(skill).strip()
                for skill in (getattr(role, "skills", ()) or ())
                if str(skill).strip()
            }),
        },
        "workspace": {
            "workdir": _required_payload_string(payload, "workdir"),
            "branch": _required_payload_string(payload, "source_branch"),
            "base_commit": _required_payload_string(payload, "base_commit"),
            "target_ref": _required_payload_string(payload, "target_ref"),
            "workspace_generation": _required_payload_string(
                payload, "workspace_generation"
            ),
        },
        "result_contract": {
            "output_profile_id": _required_payload_string(
                payload, "output_profile_id"
            ),
            "output_profile_revision": _required_payload_string(
                payload, "output_profile_revision"
            ),
            "success_event": success_event,
            "failure_event": failure_event,
            "evidence_required": True,
        },
        "lifecycle": {
            "resume_allowed": True,
            "unknown_is_not_idle": True,
        },
        "wake_contract": {
            "completion_events": [success_event, failure_event],
            "recovery_events": ["worker.stuck", "worker.respawn.failed"],
            "idle_probe_event": "worker.probe.idle",
            "unknown_state": "hold",
        },
    }
    normalized = normalize_worker_execution_envelope(body, verify_digest=False)
    normalized["envelope_digest"] = envelope_digest(normalized)
    return normalized


def normalize_worker_execution_envelope(
    value: Mapping[str, Any],
    *,
    verify_digest: bool = True,
) -> dict[str, Any]:
    """Validate and copy an envelope without making semantic decisions."""

    if not isinstance(value, Mapping):
        raise WorkerExecutionEnvelopeError("worker envelope must be an object")
    body = copy.deepcopy(dict(value))
    if body.get("schema_version") != SCHEMA_VERSION:
        raise WorkerExecutionEnvelopeError(
            f"worker envelope schema must be {SCHEMA_VERSION!r}"
        )
    missing = [key for key in _REQUIRED_SECTIONS if not isinstance(body.get(key), Mapping)]
    if missing:
        raise WorkerExecutionEnvelopeError(
            "worker envelope missing sections: " + ", ".join(missing)
        )

    identity = body["identity"]
    for key in _DIGEST_KEYS:
        _required_string(identity, key, f"identity.{key}")

    routing = body["routing"]
    for key in (
        "role_instance",
        "role_name",
        "backend",
        "execution_profile_id",
        "execution_profile_digest",
    ):
        _required_string(routing, key, f"routing.{key}")
    if routing.get("frontier_owner") not in {"run_manager", "wrc"}:
        raise WorkerExecutionEnvelopeError(
            "routing.frontier_owner must be 'run_manager' or 'wrc'"
        )
    if not isinstance(routing.get("skills"), list) or any(
        not isinstance(skill, str) or not skill.strip()
        for skill in routing["skills"]
    ):
        raise WorkerExecutionEnvelopeError("routing.skills must be a list of strings")

    workspace = body["workspace"]
    for key in ("workdir", "branch", "base_commit", "target_ref", "workspace_generation"):
        _required_string(workspace, key, f"workspace.{key}")

    result_contract = body["result_contract"]
    for key in (
        "output_profile_id",
        "output_profile_revision",
        "success_event",
        "failure_event",
    ):
        _required_string(result_contract, key, f"result_contract.{key}")
    if result_contract.get("evidence_required") is not True:
        raise WorkerExecutionEnvelopeError(
            "result_contract.evidence_required must be true"
        )

    lifecycle = body["lifecycle"]
    if lifecycle.get("resume_allowed") is not True:
        raise WorkerExecutionEnvelopeError("lifecycle.resume_allowed must be true")
    if lifecycle.get("unknown_is_not_idle") is not True:
        raise WorkerExecutionEnvelopeError(
            "lifecycle.unknown_is_not_idle must be true"
        )

    wake = body["wake_contract"]
    completion_events = wake.get("completion_events")
    recovery_events = wake.get("recovery_events")
    if (
        not isinstance(completion_events, list)
        or len(completion_events) != 2
        or any(not isinstance(event, str) or not event.strip() for event in completion_events)
    ):
        raise WorkerExecutionEnvelopeError(
            "wake_contract.completion_events must contain success and failure events"
        )
    if (
        not isinstance(recovery_events, list)
        or any(not isinstance(event, str) or not event.strip() for event in recovery_events)
    ):
        raise WorkerExecutionEnvelopeError(
            "wake_contract.recovery_events must be a list of event names"
        )
    _required_string(wake, "idle_probe_event", "wake_contract.idle_probe_event")
    if wake.get("unknown_state") != "hold":
        raise WorkerExecutionEnvelopeError(
            "wake_contract.unknown_state must be 'hold'"
        )

    digest = body.get("envelope_digest")
    if digest is not None:
        _required_string(body, "envelope_digest", "envelope_digest")
        if verify_digest and digest != envelope_digest(body):
            raise WorkerExecutionEnvelopeError("worker envelope digest mismatch")
    return body


def envelope_digest(value: Mapping[str, Any]) -> str:
    """Return the stable digest of an envelope excluding its own digest."""

    body = {
        str(key): copy.deepcopy(raw)
        for key, raw in value.items()
        if key != "envelope_digest"
    }
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_payload_string(payload: Mapping[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise WorkerExecutionEnvelopeError(f"worker envelope payload missing {key}")
    return value


def _required_string(value: Mapping[str, Any], key: str, label: str) -> str:
    raw = str(value.get(key) or "").strip()
    if not raw:
        raise WorkerExecutionEnvelopeError(f"worker envelope missing {label}")
    return raw


__all__ = [
    "SCHEMA_VERSION",
    "WorkerExecutionEnvelopeError",
    "build_worker_execution_envelope",
    "envelope_digest",
    "normalize_worker_execution_envelope",
]
