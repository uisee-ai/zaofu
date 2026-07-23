"""Stable closure identity shared by Issue, PRD, and Refactor flows."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.run_scope import resolve_run_for_event, run_aliases
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


CLOSURE_EVENT_TYPES = frozenset({"flow.goal.closed", "module.parity.closed"})


class GoalClosureIdentityError(ValueError):
    """Goal-closure dispatch snapshots do not bind to one immutable target."""


def build_closure_identity(
    events: list[ZfEvent],
    *,
    source_event: ZfEvent,
    payload: Mapping[str, Any],
    state_dir: Path,
    flow_kind: str,
) -> dict[str, Any]:
    raw_workflow_run_id = str(
        payload.get("workflow_run_id")
        or payload.get("trace_id")
        or resolve_run_for_event(events, source_event)
        or source_event.correlation_id
        or ""
    ).strip()
    goal_id = str(
        payload.get("goal_id")
        or payload.get("feature_id")
        or payload.get("pdd_id")
        or ""
    ).strip()
    workflow_run_id = (
        _pinned_claim_workflow_run_id(
            events,
            workflow_run_id=raw_workflow_run_id,
            goal_id=goal_id,
        )
        or raw_workflow_run_id
    )
    generation = _task_map_generation(events, payload, workflow_run_id, goal_id)
    candidate_head = _candidate_head(events, payload, workflow_run_id, goal_id)
    closure_source = {
        "schema_version": "closure-source.v1",
        "workflow_run_id": workflow_run_id,
        "goal_id": goal_id,
        "flow_kind": flow_kind,
        "task_map_generation": generation,
        "candidate_head_commit": candidate_head,
        "source_event_type": source_event.type,
        "source_payload": _stable_source_payload(payload),
    }
    descriptor = write_immutable_json_sidecar(
        state_dir,
        closure_source,
        root="goal-closure/facts",
        kind="goal_closure_fact",
        schema_version="closure-source.v1",
        created_by="goal-closure-identity",
        source_event_id=source_event.id,
    )
    identity_fields = {
        "workflow_run_id": workflow_run_id,
        "goal_id": goal_id,
        "task_map_generation": generation,
        "candidate_head_commit": candidate_head,
        "closure_fact_digest": str(descriptor.get("sha256") or ""),
    }
    identity = _digest(identity_fields)
    return {
        **identity_fields,
        "closure_fact_ref": str(descriptor.get("ref") or ""),
        "closure_identity": identity,
    }


def current_closure_event(
    events: list[ZfEvent],
    *,
    event_type: str,
    workflow_run_id: str,
    goal_id: str,
) -> ZfEvent | None:
    for event in reversed(events):
        if event.type != event_type or not isinstance(event.payload, dict):
            continue
        if (
            str(event.payload.get("workflow_run_id") or "") == workflow_run_id
            and str(event.payload.get("goal_id") or "") == goal_id
        ):
            return event
    return None


def same_closure_identity(event: ZfEvent | None, identity: Mapping[str, Any]) -> bool:
    payload = event.payload if event is not None and isinstance(event.payload, dict) else {}
    return bool(
        str(payload.get("closure_identity") or "")
        and str(payload.get("closure_identity") or "")
        == str(identity.get("closure_identity") or "")
    )


def closure_is_current(events: list[ZfEvent], identity: Mapping[str, Any]) -> bool:
    workflow_run_id = str(identity.get("workflow_run_id") or "")
    goal_id = str(identity.get("goal_id") or "")
    expected = str(identity.get("closure_identity") or "")
    latest: ZfEvent | None = None
    for event in events:
        if event.type not in CLOSURE_EVENT_TYPES or not isinstance(event.payload, dict):
            continue
        if (
            str(event.payload.get("workflow_run_id") or "") == workflow_run_id
            and str(event.payload.get("goal_id") or "") == goal_id
        ):
            latest = event
    return bool(latest and str((latest.payload or {}).get("closure_identity") or "") == expected)


def validate_goal_closure_dispatch_snapshots(
    state_dir: Path,
    payload: Mapping[str, Any],
) -> None:
    """Validate Thin Judge snapshots without treating them as task contracts."""

    contract = _hydrate_snapshot(
        state_dir,
        payload,
        ref_key="contract_snapshot_ref",
        digest_key="contract_snapshot_digest",
    )
    target = _hydrate_snapshot(
        state_dir,
        payload,
        ref_key="target_snapshot_ref",
        digest_key="target_snapshot_digest",
    )
    if str(contract.get("schema_version") or "") != "goal-closure-contract-snapshot.v1":
        raise GoalClosureIdentityError("unsupported Goal closure contract snapshot schema")
    if str(target.get("schema_version") or "") != "goal-closure-target-snapshot.v1":
        raise GoalClosureIdentityError("unsupported Goal closure target snapshot schema")
    for key in ("workflow_run_id", "goal_id", "task_map_generation"):
        expected = str(payload.get(key) or "").strip()
        if not expected:
            raise GoalClosureIdentityError(f"Goal closure dispatch missing {key}")
        for label, snapshot in (("contract", contract), ("target", target)):
            if str(snapshot.get(key) or "").strip() != expected:
                raise GoalClosureIdentityError(
                    f"Goal closure {label} {key} mismatch"
                )
    target_commit = str(
        payload.get("target_commit")
        or payload.get("candidate_head_commit")
        or ""
    ).strip()
    if not target_commit or str(target.get("target_commit") or "").strip() != target_commit:
        raise GoalClosureIdentityError("Goal closure target target_commit mismatch")
    closure_identity = str(payload.get("closure_identity") or "").strip()
    if (
        not closure_identity
        or str(target.get("closure_identity") or "").strip() != closure_identity
    ):
        raise GoalClosureIdentityError("Goal closure target closure_identity mismatch")


def _hydrate_snapshot(
    state_dir: Path,
    payload: Mapping[str, Any],
    *,
    ref_key: str,
    digest_key: str,
) -> dict[str, Any]:
    ref = str(payload.get(ref_key) or "").strip()
    digest = str(payload.get(digest_key) or "").strip()
    if not ref or not digest:
        raise GoalClosureIdentityError(
            f"Goal closure dispatch missing {ref_key}/{digest_key}"
        )
    try:
        hydrated = hydrate_sidecar_ref(state_dir, {"ref": ref, "sha256": digest})
    except Exception as exc:
        raise GoalClosureIdentityError(str(exc)) from exc
    if not isinstance(hydrated.payload, dict):
        raise GoalClosureIdentityError(f"{ref_key} is not a JSON object")
    return dict(hydrated.payload)


def _task_map_generation(
    events: list[ZfEvent],
    payload: Mapping[str, Any],
    workflow_run_id: str,
    goal_id: str,
) -> str:
    from zf.runtime.goal_claim_set import canonical_task_map_generation

    aliases = run_aliases(events)
    canonical_run_id = aliases.get(workflow_run_id, workflow_run_id)
    direct = canonical_task_map_generation(
        task_map_generation=payload.get("task_map_generation"),
        task_map_digest=payload.get("task_map_digest"),
        task_map_ref=payload.get("task_map_ref"),
    )
    if direct:
        return direct
    for event in reversed(events):
        if event.type not in {"task_map.ready", "task_map.amended", "goal.claim_set.pinned"}:
            continue
        body = event.payload if isinstance(event.payload, dict) else {}
        event_run = str(body.get("workflow_run_id") or body.get("trace_id") or event.correlation_id or "")
        event_goal = str(body.get("goal_id") or body.get("feature_id") or body.get("pdd_id") or "")
        if (
            canonical_run_id
            and event_run
            and aliases.get(event_run, event_run) != canonical_run_id
        ):
            continue
        if goal_id and event_goal and event_goal != goal_id:
            continue
        value = canonical_task_map_generation(
            task_map_generation=body.get("task_map_generation"),
            task_map_digest=body.get("task_map_digest"),
            task_map_ref=body.get("task_map_ref"),
        )
        if value:
            return value
    return ""


def _pinned_claim_workflow_run_id(
    events: list[ZfEvent],
    *,
    workflow_run_id: str,
    goal_id: str,
) -> str:
    """Use the current Claim Set identity without rewriting admitted results."""

    aliases = run_aliases(events)
    canonical_run_id = aliases.get(workflow_run_id, workflow_run_id)
    for event in reversed(events):
        if event.type != "goal.claim_set.pinned" or not isinstance(event.payload, dict):
            continue
        body = event.payload
        event_goal_id = str(body.get("goal_id") or "").strip()
        if goal_id and event_goal_id and event_goal_id != goal_id:
            continue
        event_run_id = str(body.get("workflow_run_id") or "").strip()
        if not event_run_id:
            continue
        if aliases.get(event_run_id, event_run_id) == canonical_run_id:
            return event_run_id
    return ""


def _candidate_head(
    events: list[ZfEvent],
    payload: Mapping[str, Any],
    workflow_run_id: str,
    goal_id: str,
) -> str:
    aliases = run_aliases(events)
    canonical_run_id = aliases.get(workflow_run_id, workflow_run_id)
    direct = str(
        payload.get("candidate_head_commit")
        or payload.get("target_commit")
        or payload.get("source_commit")
        or ""
    ).strip()
    if direct:
        return direct
    for event in reversed(events):
        if event.type not in {"candidate.ready", "candidate.integration.completed", "verify.passed", "test.passed"}:
            continue
        body = event.payload if isinstance(event.payload, dict) else {}
        event_run = str(body.get("workflow_run_id") or body.get("trace_id") or event.correlation_id or "")
        event_goal = str(body.get("goal_id") or body.get("feature_id") or body.get("pdd_id") or "")
        if (
            canonical_run_id
            and event_run
            and aliases.get(event_run, event_run) != canonical_run_id
        ):
            continue
        if goal_id and event_goal and event_goal != goal_id:
            continue
        value = str(body.get("candidate_head_commit") or body.get("target_commit") or body.get("source_commit") or body.get("commit") or "").strip()
        if value:
            return value
    return ""


def _stable_source_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    # Scheduling/execution identity is not product closure identity. Two
    # discovery fanouts can prove the same generation/candidate closure and
    # must not start a second Judge solely because their pane/run ids differ.
    ignored = {
        "source_event_id", "ts", "timestamp", "run_id", "child_id",
        "fanout_id", "stage_id", "stage_slot", "operation_id",
        "request_hash", "attempt_id", "dispatch_id", "role_instance",
        "trace_id", "source",
    }
    return {str(key): value for key, value in payload.items() if str(key) not in ignored}


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CLOSURE_EVENT_TYPES",
    "GoalClosureIdentityError",
    "build_closure_identity",
    "closure_is_current",
    "current_closure_event",
    "same_closure_identity",
    "validate_goal_closure_dispatch_snapshots",
]
