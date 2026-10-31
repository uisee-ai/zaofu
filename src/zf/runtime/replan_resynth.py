"""Build deterministic synth-retry events for candidate-level replans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.locks import locked_path
from zf.runtime.candidate_rework_identity import (
    _CANDIDATE_REWORK_IDENTITY_KEYS,
)
from zf.runtime.candidate_rework_evidence import (
    plan_rejection_feedback,
    plan_rejection_required_actions,
)
from zf.runtime.plan_artifact_package import (
    plan_artifact_package_binding,
    reduce_plan_artifact_packages,
)


_PLAN_PACKAGE_PAYLOAD_KEYS = (
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
)
_GOAL_CLAIM_SET_IDENTITY_KEYS = (
    "goal_claim_set_ref",
    "goal_claim_set_digest",
)
_REPLAN_SEMANTIC_PASSTHROUGH_KEYS = (
    "source_commit",
    "candidate_base_commit",
    "previous_plan_candidate_refs",
    "required_actions",
    "orchestration_delta",
    "orchestration_delta_ref",
    "orchestration_delta_digest",
    "reason_codes",
    "operator_override",
    "owner_authorization",
)


def replan_semantic_context(payload: Mapping) -> dict:
    """Keep the action marker and immediate/recovered trigger on one context."""
    return {
        key: payload[key]
        for key in _REPLAN_SEMANTIC_PASSTHROUGH_KEYS
        if payload.get(key) not in (None, "", [], {})
    }


def append_replan_resynth_once(
    writer: EventWriter, state_dir: Path, event: ZfEvent,
) -> ZfEvent:
    """Serialize action/sweep follow-through against the current ledger."""
    def identity(item: ZfEvent) -> tuple:
        payload = item.payload
        return (
            item.type,
            str(payload.get("workflow_run_id") or payload.get("trace_id")
                or item.correlation_id or ""),
            str(payload.get("pdd_id") or payload.get("feature_id") or ""),
            str(payload.get("rework_of") or ""),
            int(payload.get("rework_attempt") or 0),
        )

    key = identity(event)
    if not key[3]:
        return writer.append(event)
    with locked_path(state_dir / "replan-resynth"):
        for existing in reversed(writer.event_log.read_all()):
            if existing.type == event.type and identity(existing) == key:
                return existing
        return writer.append(event)


def build_replan_resynth_event(
    *,
    plan: object,
    events: Sequence[ZfEvent],
    config: object,
) -> ZfEvent | None:
    """Return the plan-synth trigger for a candidate-level replan."""

    workflow = getattr(config, "workflow", None)
    replan_cfg = getattr(workflow, "admission_replan", None)
    trigger = _resynth_trigger(
        config,
        flow_kind=str(getattr(plan, "flow_kind", "") or ""),
    )
    if not getattr(replan_cfg, "enabled", False) or not trigger:
        return None

    base_payload = _latest_trigger_payload(
        events,
        trigger=trigger,
        trace_id=str(getattr(plan, "trace_id", "") or ""),
    )
    payload = dict(base_payload)
    source_event = _event_by_id(
        events,
        str(getattr(plan, "source_event_id", "") or ""),
    )
    source_payload = _event_payload(source_event)
    feedback = list(getattr(plan, "feedback", ()) or ())
    source_type = str(
        getattr(source_event, "type", "")
        if source_event is not None and not isinstance(source_event, Mapping)
        else (source_event or {}).get("type", "")
    ).strip()
    if source_type == "plan.rejected" or str(
        getattr(plan, "source_event_type", "") or ""
    ) == "plan.rejected":
        feedback = list(dict.fromkeys([
            *plan_rejection_feedback(source_payload),
            *feedback,
        ]))
        required_actions = [
            action
            for _directive_id, action in plan_rejection_required_actions(source_payload)
        ]
        if required_actions:
            payload["required_actions"] = required_actions
        for key in (
            "orchestration_delta",
            "orchestration_delta_ref",
            "orchestration_delta_digest",
            "reason_codes",
            "operator_override",
            "owner_authorization",
        ):
            value = source_payload.get(key)
            if value not in (None, "", [], {}):
                payload[key] = value
    payload.update({
        "pdd_id": getattr(plan, "pdd_id", ""),
        "trace_id": getattr(plan, "trace_id", ""),
        "target_ref": getattr(plan, "target_ref", "") or payload.get("target_ref", ""),
        "rework_of": getattr(plan, "source_event_id", ""),
        "rework_attempt": getattr(plan, "attempt", 0),
        "rework_source": getattr(plan, "source_event_type", ""),
        "rework_feedback": feedback,
        "rework_categories": list(getattr(plan, "failure_categories", ()) or ()),
        "rework_summary": dict(getattr(plan, "rework_summary", {}) or {}),
        "replan_classification": getattr(plan, "classification", ""),
    })
    human_resolution = getattr(plan, "human_resolution", None)
    if isinstance(human_resolution, Mapping) and human_resolution:
        payload["human_resolution"] = dict(human_resolution)
    for key in _REPLAN_SEMANTIC_PASSTHROUGH_KEYS:
        value = getattr(plan, key, None)
        if value not in (None, "", [], {}):
            payload[key] = value
    for key in _CANDIDATE_REWORK_IDENTITY_KEYS:
        value = getattr(plan, key, None)
        if value not in (None, "", [], {}):
            payload[key] = value
    _bind_current_plan_package(payload, plan=plan, events=events)
    # Owner-approved requirement amendments outrank the historical launch input.
    # These values come from a controlled Plan decision, not a worker result.
    if source_type == "plan.rejected" and source_payload.get("requirement_amendment_ref"):
        for key in ("requirement_amendment_ref", "requirement_spec_ref", "requirement_spec_digest"):
            payload[key] = source_payload[key]
    for key in (
        "failed_task_ids",
        "task_ids",
        "downstream_task_ids",
        "resume_scope",
    ):
        value = getattr(plan, key, None)
        if value not in (None, "", (), []):
            payload[key] = list(value) if isinstance(value, tuple) else value
    return ZfEvent(
        type=trigger,
        actor="zf-cli",
        payload=payload,
        correlation_id=str(getattr(plan, "trace_id", "") or ""),
    )


def _event_by_id(events: Sequence[ZfEvent], event_id: str) -> object | None:
    if not event_id:
        return None
    for event in reversed(events):
        if str(
            event.get("id", "") if isinstance(event, Mapping) else getattr(event, "id", "")
        ) == event_id:
            return event
    return None


def _event_payload(event: object | None) -> dict:
    if event is None:
        return {}
    payload = event.get("payload", {}) if isinstance(event, Mapping) else getattr(
        event,
        "payload",
        {},
    )
    return dict(payload) if isinstance(payload, Mapping) else {}


def _bind_current_plan_package(
    payload: dict,
    *,
    plan: object,
    events: Sequence[ZfEvent],
) -> None:
    """Replace inherited identity with an admitted package for this run."""

    workflow_run_id = str(
        getattr(plan, "workflow_run_id", "")
        or payload.get("workflow_run_id")
        or getattr(plan, "trace_id", "")
        or payload.get("trace_id")
        or ""
    ).strip()
    if not workflow_run_id:
        return

    package_events = [
        event
        if isinstance(event, (ZfEvent, Mapping))
        else {
            "type": str(getattr(event, "type", "") or ""),
            "id": str(getattr(event, "id", "") or ""),
            "payload": dict(getattr(event, "payload", {}) or {}),
        }
        for event in events
    ]
    projection = reduce_plan_artifact_packages(
        package_events,
        workflow_run_id=workflow_run_id,
    )
    current = projection.get("current")
    if not isinstance(current, dict) or not current:
        if str(getattr(plan, "source_event_type", "") or "") != "plan.rejected":
            return
        for key in (*_PLAN_PACKAGE_PAYLOAD_KEYS, *_GOAL_CLAIM_SET_IDENTITY_KEYS):
            payload.pop(key, None)
        return
    for key in _GOAL_CLAIM_SET_IDENTITY_KEYS:
        payload.pop(key, None)
    payload.update(plan_artifact_package_binding(package_events, current))


def _resynth_trigger(config: object, *, flow_kind: str) -> str:
    workflow = getattr(config, "workflow", None)
    replan_cfg = getattr(workflow, "admission_replan", None)
    fallback = str(getattr(replan_cfg, "resynth_trigger", "") or "").strip()
    scope = str(flow_kind or "").strip().lower()
    if not scope:
        return fallback
    for stage in list(getattr(workflow, "stages", []) or []):
        if str(getattr(stage, "flow_kind", "") or "").strip().lower() != scope:
            continue
        if str(getattr(stage, "topology", "") or "") != "fanout_reader":
            continue
        aggregate = getattr(stage, "aggregate", None)
        success_event = str(
            getattr(stage, "success_event", "")
            or getattr(aggregate, "success_event", "")
            or ""
        ).strip()
        if success_event == "task_map.ready":
            trigger = str(getattr(stage, "trigger", "") or "").strip()
            if trigger:
                return trigger
    return fallback


def plan_missing_replan_resynth_events(
    events: Sequence[ZfEvent],
    *,
    config: object,
) -> list[ZfEvent]:
    """Repair a replan marker whose follow-through used another Flow trigger."""

    latest_by_scope: dict[tuple[str, str], tuple[int, ZfEvent]] = {}
    for index, event in enumerate(events):
        if event.type != "orchestrator.replan_requested":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        flow_kind = str(payload.get("flow_kind") or "").strip()
        if not flow_kind:
            continue
        trace_id = str(
            payload.get("workflow_run_id")
            or payload.get("trace_id")
            or event.correlation_id
            or ""
        ).strip()
        pdd_id = str(payload.get("pdd_id") or payload.get("feature_id") or "").strip()
        latest_by_scope[(trace_id, pdd_id)] = (index, event)

    repairs: list[ZfEvent] = []
    for (trace_id, pdd_id), (source_index, marker) in latest_by_scope.items():
        payload = marker.payload if isinstance(marker.payload, dict) else {}
        plan_data = {
            "pdd_id": pdd_id,
            "trace_id": trace_id,
            "target_ref": str(payload.get("target_ref") or ""),
            "source_event_id": str(payload.get("rework_of") or ""),
            "attempt": int(payload.get("rework_attempt") or 0),
            "source_event_type": str(payload.get("rework_source") or ""),
            "feedback": tuple(_string_list(payload.get("rework_feedback"))),
            "failure_categories": tuple(
                _string_list(payload.get("rework_categories"))
            ),
            "rework_summary": (
                payload.get("rework_summary")
                if isinstance(payload.get("rework_summary"), dict)
                else {}
            ),
            "classification": str(payload.get("classification") or ""),
            "failed_task_ids": tuple(_string_list(payload.get("failed_task_ids"))),
            "task_ids": tuple(_string_list(payload.get("task_ids"))),
            "downstream_task_ids": tuple(
                _string_list(payload.get("downstream_task_ids"))
            ),
            "resume_scope": str(payload.get("resume_scope") or ""),
        }
        human_resolution = _causal_human_resolution(
            events,
            marker=marker,
        )
        if human_resolution:
            plan_data["human_resolution"] = human_resolution
        for key in _REPLAN_SEMANTIC_PASSTHROUGH_KEYS:
            value = payload.get(key)
            if value not in (None, "", [], {}):
                plan_data[key] = value
        for key in _CANDIDATE_REWORK_IDENTITY_KEYS:
            value = payload.get(key)
            if value not in (None, "", [], {}):
                plan_data[key] = value
        desired = build_replan_resynth_event(
            plan=SimpleNamespace(**plan_data),
            events=events,
            config=config,
        )
        if desired is None:
            continue
        desired_rework_of = str(desired.payload.get("rework_of") or "")
        advanced = False
        for later in events[source_index + 1:]:
            later_payload = later.payload if isinstance(later.payload, dict) else {}
            later_trace = str(
                later_payload.get("workflow_run_id")
                or later_payload.get("trace_id")
                or later.correlation_id
                or ""
            ).strip()
            later_pdd = str(
                later_payload.get("pdd_id")
                or later_payload.get("feature_id")
                or ""
            ).strip()
            if later_trace and trace_id and later_trace != trace_id:
                continue
            if later_pdd and pdd_id and later_pdd != pdd_id:
                continue
            if (
                later.type == desired.type
                and str(later_payload.get("rework_of") or "") == desired_rework_of
            ):
                advanced = True
                break
            if later.type in {"task_map.ready", "candidate.ready"}:
                advanced = True
                break
        if not advanced:
            repairs.append(desired)
    return repairs


def _causal_human_resolution(
    events: Sequence[ZfEvent],
    *,
    marker: object,
) -> dict[str, object]:
    """Bind an operator's exact new-generation decision to its replan."""

    marker_payload = _event_payload(marker)
    causation_id = str(
        marker.get("causation_id", "")
        if isinstance(marker, Mapping)
        else getattr(marker, "causation_id", "")
    ).strip()
    resolved = _event_by_id(events, causation_id)
    resolved_type = str(
        resolved.get("type", "")
        if isinstance(resolved, Mapping)
        else getattr(resolved, "type", "")
    ).strip()
    if resolved_type != "human.resolved":
        return {}

    resolved_payload = _event_payload(resolved)
    action = str(resolved_payload.get("action") or "").strip()
    response = str(resolved_payload.get("response") or "").strip()
    source_failure_event_id = str(
        resolved_payload.get("source_failure_event_id") or ""
    ).strip()
    rework_of = str(marker_payload.get("rework_of") or "").strip()
    if (
        action != "start_new_generation"
        or not response
        or (
            source_failure_event_id
            and rework_of
            and source_failure_event_id != rework_of
        )
    ):
        return {}

    event_id = str(
        resolved.get("id", "")
        if isinstance(resolved, Mapping)
        else getattr(resolved, "id", "")
    ).strip()
    actor = str(
        resolved.get("actor", "")
        if isinstance(resolved, Mapping)
        else getattr(resolved, "actor", "")
    ).strip()
    evidence_refs = [
        str(ref).strip()
        for ref in resolved_payload.get("contract_evidence_refs", [])
        if str(ref).strip()
    ] if isinstance(resolved_payload.get("contract_evidence_refs"), list) else []
    return {
        "schema_version": str(
            resolved_payload.get("schema_version") or "human-resolution.v1"
        ),
        "source_event_id": event_id,
        "source_ref": f"events.jsonl#{event_id}",
        "actor": actor,
        "resolved_event_id": str(
            resolved_payload.get("resolved_event_id") or ""
        ).strip(),
        "source_failure_event_id": source_failure_event_id,
        "action": action,
        "response": response,
        "contract_evidence_refs": evidence_refs,
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _latest_trigger_payload(
    events: Sequence[ZfEvent],
    *,
    trigger: str,
    trace_id: str,
) -> dict:
    payload: dict = {}
    for event in events:
        if event.type != trigger:
            continue
        candidate = event.payload if isinstance(event.payload, dict) else {}
        event_trace = str(candidate.get("trace_id") or event.correlation_id or "")
        if trace_id and event_trace and event_trace != trace_id:
            continue
        payload = dict(candidate)
    return payload
