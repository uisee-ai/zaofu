"""Portable, read-only receipt for one kernel-admitted Goal completion."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.run_contract import stable_json_sha256
from zf.runtime.run_scope import events_for_run, resolve_run_id


SCHEMA_VERSION = "goal-completion-receipt.v1"


class GoalCompletionReceiptError(ValueError):
    """A requested run cannot produce a trustworthy completion receipt."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        diagnostics: Iterable[str] = (),
    ) -> None:
        self.code = code
        self.diagnostics = tuple(dict.fromkeys(str(item) for item in diagnostics if item))
        detail = f": {', '.join(self.diagnostics)}" if self.diagnostics else ""
        super().__init__(f"{message}{detail}")


def build_goal_completion_receipt(
    events: Iterable[ZfEvent],
    *,
    run_id: str,
    generated_at: str,
    project_id: str = "",
) -> dict[str, Any]:
    """Build a fail-closed receipt from the unique successful run terminal."""

    rows = list(events)
    requested_run_id = str(run_id or "").strip()
    if not requested_run_id:
        raise GoalCompletionReceiptError(
            "run_id_required",
            "completion receipt requires an explicit run_id",
        )
    canonical_run_id = resolve_run_id(rows, requested_run_id)
    if not canonical_run_id:
        raise GoalCompletionReceiptError(
            "run_not_found",
            f"unknown run_id {requested_run_id!r}",
        )
    scoped_events = events_for_run(rows, run_id=canonical_run_id)
    completions = [
        event for event in scoped_events if event.type == "run.goal.completed"
    ]
    if not completions:
        raise GoalCompletionReceiptError(
            "completion_not_admitted",
            f"run {canonical_run_id!r} has no kernel-admitted run.goal.completed",
        )
    if len(completions) != 1:
        raise GoalCompletionReceiptError(
            "completion_not_unique",
            f"run {canonical_run_id!r} has {len(completions)} completion events",
        )

    completion = completions[0]
    payload = completion.payload if isinstance(completion.payload, dict) else {}
    event_by_id = {
        event.id: event
        for event in scoped_events
        if str(event.id or "").strip()
    }
    completion_seq = next(
        index for index, event in enumerate(rows) if event is completion
    )
    scoped_ids = {id(event) for event in scoped_events}
    scoped_with_seq = [
        (index, event)
        for index, event in enumerate(rows)
        if id(event) in scoped_ids
    ]
    diagnostics = _completion_diagnostics(
        rows=rows,
        scoped_events=scoped_events,
        event_by_id=event_by_id,
        completion=completion,
        completion_seq=completion_seq,
        canonical_run_id=canonical_run_id,
    )
    if diagnostics:
        raise GoalCompletionReceiptError(
            "completion_evidence_incomplete",
            f"run {canonical_run_id!r} completion evidence is incomplete",
            diagnostics=diagnostics,
        )

    claim_event_id = str(
        payload.get("claim_event_id") or completion.causation_id or ""
    )
    verification_event_id = str(payload.get("verification_event_id") or "")
    candidate_event_id = str(payload.get("candidate_event_id") or "")
    delivery_event_id = str(payload.get("delivery_event_id") or "")
    source_event_id = str(payload.get("source_event_id") or "")
    verification_ref = _artifact_ref(
        payload.get("verification_admitted_call_result_ref")
    )
    closure_ref = _artifact_ref(payload.get("admitted_call_result_ref"))
    evidence_refs = [
        {
            "kind": "goal_claim_set",
            "ref": str(payload.get("goal_claim_set_ref") or ""),
            "sha256": str(payload.get("goal_claim_set_digest") or ""),
        },
        {"kind": "goal_closure_result", **closure_ref},
        {"kind": "candidate_verification_result", **verification_ref},
    ]
    event_refs = {
        "completion": _event_ref(completion),
        "completion_claim": _event_ref(event_by_id[claim_event_id]),
        "goal_closure": _event_ref(event_by_id.get(source_event_id)),
        "verification": _event_ref(event_by_id[verification_event_id]),
        "candidate": _event_ref(event_by_id[candidate_event_id]),
        "delivery": _event_ref(event_by_id.get(delivery_event_id)),
    }
    last_seq, last_event = scoped_with_seq[-1]
    completion_event_sha256 = stable_json_sha256(asdict(completion))
    stable_body = {
        "workflow_run_id": canonical_run_id,
        "goal_id": str(payload.get("goal_id") or ""),
        "completion_event_sha256": completion_event_sha256,
        "event_refs": event_refs,
        "evidence_refs": evidence_refs,
        "target_commit": str(payload.get("target_commit") or ""),
        "verified_target_commit": str(
            payload.get("verified_target_commit") or ""
        ),
        "last_event_id": last_event.id,
        "last_seq": last_seq,
    }
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "is_derived_projection": True,
        "generated_at": generated_at,
        "project_id": project_id,
        "feature_id": str(
            payload.get("feature_id")
            or payload.get("pdd_id")
            or payload.get("goal_id")
            or ""
        ),
        "goal_id": str(payload.get("goal_id") or ""),
        "workflow_run_id": canonical_run_id,
        "requested_run_id": requested_run_id,
        "terminal": {
            "status": "completed",
            "event_id": completion.id,
            "event_type": completion.type,
            "event_seq": completion_seq,
            "event_ts": completion.ts,
            "event_sha256": completion_event_sha256,
            "claim_id": str(payload.get("claim_id") or ""),
        },
        "completion_gate": {
            "status": "passed",
            "claim_event_id": claim_event_id,
            "task_map_generation": str(
                payload.get("task_map_generation") or ""
            ),
            "target_commit": str(payload.get("target_commit") or ""),
            "verified_target_commit": str(
                payload.get("verified_target_commit") or ""
            ),
        },
        "verification": {
            "event_id": verification_event_id,
            "admitted_call_result_ref": verification_ref,
        },
        "goal_closure": {
            "source_event_id": source_event_id,
            "admitted_call_result_ref": closure_ref,
            "goal_claim_set_ref": str(
                payload.get("goal_claim_set_ref") or ""
            ),
            "goal_claim_set_digest": str(
                payload.get("goal_claim_set_digest") or ""
            ),
        },
        "candidate": {
            "event_id": candidate_event_id,
            "ref": str(payload.get("candidate_ref") or ""),
            "base_commit": str(payload.get("candidate_base_commit") or ""),
            "head_commit": str(
                payload.get("candidate_head_commit")
                or payload.get("target_commit")
                or ""
            ),
            "completed_task_ids": [
                str(item)
                for item in payload.get("completed_task_ids") or []
                if str(item).strip()
            ],
        },
        "delivery": {
            "policy": str(payload.get("delivery_policy") or ""),
            "status": str(payload.get("delivery_status") or ""),
            "event_id": delivery_event_id,
        },
        "source_refs": {
            "task_map_ref": str(payload.get("task_map_ref") or ""),
            "source_index_ref": str(payload.get("source_index_ref") or ""),
            "diff_ref": str(payload.get("diff_ref") or ""),
        },
        "event_refs": event_refs,
        "evidence_refs": evidence_refs,
        "cursor": {
            "sequence_base": 0,
            "completion_event_seq": completion_seq,
            "last_seq": last_seq,
            "last_event_id": last_event.id,
            "last_event_at": last_event.ts,
        },
        "source_fingerprint": stable_json_sha256(stable_body),
        "degraded": False,
        "diagnostics": [],
    }
    return redact_obj(receipt)


def _completion_diagnostics(
    *,
    rows: list[ZfEvent],
    scoped_events: list[ZfEvent],
    event_by_id: Mapping[str, ZfEvent],
    completion: ZfEvent,
    completion_seq: int,
    canonical_run_id: str,
) -> list[str]:
    payload = completion.payload if isinstance(completion.payload, dict) else {}
    diagnostics: list[str] = []
    required_text = (
        "goal_id",
        "claim_id",
        "task_map_generation",
        "target_commit",
        "verified_target_commit",
        "verification_event_id",
        "candidate_event_id",
        "candidate_ref",
        "goal_claim_set_ref",
        "goal_claim_set_digest",
        "delivery_policy",
        "delivery_status",
    )
    for key in required_text:
        if not str(payload.get(key) or "").strip():
            diagnostics.append(f"missing:{key}")

    payload_run_id = str(
        payload.get("workflow_run_id") or payload.get("run_id") or ""
    )
    if resolve_run_id(rows, payload_run_id) != canonical_run_id:
        diagnostics.append("mismatch:workflow_run_id")
    if str(payload.get("target_commit") or "") != str(
        payload.get("verified_target_commit") or ""
    ):
        diagnostics.append("mismatch:verified_target_commit")
    for key in (
        "verification_admitted_call_result_ref",
        "admitted_call_result_ref",
    ):
        value = payload.get(key)
        artifact_ref = _artifact_ref(value)
        if not artifact_ref.get("ref"):
            diagnostics.append(f"missing:{key}.ref")
        if not artifact_ref.get("sha256"):
            diagnostics.append(f"missing:{key}.sha256")

    claim_event_id = str(
        payload.get("claim_event_id") or completion.causation_id or ""
    )
    _require_event(
        diagnostics,
        event_by_id,
        claim_event_id,
        field="claim_event_id",
        event_type="run.goal.completion.claimed",
    )
    claim_event = event_by_id.get(claim_event_id)
    claim_payload = (
        claim_event.payload
        if claim_event is not None and isinstance(claim_event.payload, dict)
        else {}
    )
    for key in ("claim_id", "task_map_generation", "target_commit"):
        if str(claim_payload.get(key) or "") != str(payload.get(key) or ""):
            diagnostics.append(f"mismatch:completion_claim.{key}")
    if str(claim_payload.get("claim_type") or "") != (
        "admitted_goal_closure_result"
    ):
        diagnostics.append("mismatch:completion_claim.claim_type")

    verification_event_id = str(payload.get("verification_event_id") or "")
    _require_event(
        diagnostics,
        event_by_id,
        verification_event_id,
        field="verification_event_id",
        event_type="fanout.child.completed",
    )
    verification_event = event_by_id.get(verification_event_id)
    verification_payload = (
        verification_event.payload
        if verification_event is not None
        and isinstance(verification_event.payload, dict)
        else {}
    )
    if _artifact_ref(
        verification_payload.get("admitted_call_result_ref")
    ) != _artifact_ref(payload.get("verification_admitted_call_result_ref")):
        diagnostics.append("mismatch:verification_admitted_call_result_ref")

    candidate_event_id = str(payload.get("candidate_event_id") or "")
    _require_event(
        diagnostics,
        event_by_id,
        candidate_event_id,
        field="candidate_event_id",
        event_type="candidate.ready",
    )
    candidate_event = event_by_id.get(candidate_event_id)
    candidate_payload = (
        candidate_event.payload
        if candidate_event is not None and isinstance(candidate_event.payload, dict)
        else {}
    )
    if str(candidate_payload.get("candidate_ref") or "") != str(
        payload.get("candidate_ref") or ""
    ):
        diagnostics.append("mismatch:candidate_ref")
    if str(candidate_payload.get("candidate_head_commit") or "") != str(
        payload.get("target_commit") or ""
    ):
        diagnostics.append("mismatch:candidate_head_commit")

    source_event_id = str(payload.get("source_event_id") or "")
    _require_event(
        diagnostics,
        event_by_id,
        source_event_id,
        field="source_event_id",
        event_type="goal.closure.synthesized",
    )
    source_event = event_by_id.get(source_event_id)
    source_payload = (
        source_event.payload
        if source_event is not None and isinstance(source_event.payload, dict)
        else {}
    )
    if _artifact_ref(source_payload.get("admitted_call_result_ref")) != (
        _artifact_ref(payload.get("admitted_call_result_ref"))
    ):
        diagnostics.append("mismatch:goal_closure.admitted_call_result_ref")

    delivery_status = str(payload.get("delivery_status") or "")
    delivery_event_id = str(payload.get("delivery_event_id") or "")
    if delivery_status not in {"not_required", "settled"}:
        diagnostics.append("invalid:delivery_status")
    if delivery_status == "settled":
        _require_event(
            diagnostics,
            event_by_id,
            delivery_event_id,
            field="delivery_event_id",
            event_type="run.delivery.settled",
        )
        delivery_event = event_by_id.get(delivery_event_id)
        delivery_payload = (
            delivery_event.payload
            if delivery_event is not None
            and isinstance(delivery_event.payload, dict)
            else {}
        )
        if str(delivery_payload.get("claim_id") or "") != str(
            payload.get("claim_id") or ""
        ):
            diagnostics.append("mismatch:delivery_event_id.claim_id")
    elif delivery_event_id:
        diagnostics.append("invalid:delivery_event_id_not_required")

    scoped_event_ids = {id(event) for event in scoped_events}
    for index, event in enumerate(rows):
        if index <= completion_seq or id(event) not in scoped_event_ids:
            continue
        event_payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in {
            "run.goal.started",
            "run.goal.blocked",
            "run.goal.completion.blocked",
            "run.goal.completion.rejected",
        }:
            diagnostics.append(f"stale:later_{event.type}")
        elif (
            event.type == "run.goal.updated"
            and str(event_payload.get("status") or "") not in {"", "complete"}
        ):
            diagnostics.append("stale:later_run.goal.updated")
    return diagnostics


def _require_event(
    diagnostics: list[str],
    event_by_id: Mapping[str, ZfEvent],
    event_id: str,
    *,
    field: str,
    event_type: str = "",
) -> None:
    if not event_id:
        diagnostics.append(f"missing:{field}")
        return
    event = event_by_id.get(event_id)
    if event is None:
        diagnostics.append(f"missing_event:{field}")
    elif event_type and event.type != event_type:
        diagnostics.append(f"mismatch:{field}.event_type")


def _artifact_ref(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {"ref": "", "sha256": ""}
    return {
        "ref": str(value.get("ref") or ""),
        "sha256": str(value.get("sha256") or ""),
    }


def _event_ref(event: ZfEvent | None) -> dict[str, str]:
    if event is None:
        return {}
    return {
        "event_id": event.id,
        "event_type": event.type,
        "event_ts": event.ts,
        "actor": str(event.actor or ""),
        "origin": str(event.origin or ""),
    }


__all__ = [
    "GoalCompletionReceiptError",
    "SCHEMA_VERSION",
    "build_goal_completion_receipt",
]
