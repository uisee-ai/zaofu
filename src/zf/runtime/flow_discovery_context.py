"""Candidate and immutable identity context for flow discovery dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent


@dataclass(frozen=True)
class FlowDiscoveryContext:
    candidate_ref: str
    candidate_head_commit: str
    request_payload: dict[str, Any]


def build_flow_discovery_context(
    events: Sequence[ZfEvent],
    *,
    event: ZfEvent,
    payload: dict[str, Any],
    fallback: dict[str, Any],
    metadata: Mapping[str, Any],
    pdd_id: str,
    feature_id: str,
    trace_id: str,
    flow_kind: str,
    discovery_profile: str,
) -> FlowDiscoveryContext:
    candidate = _latest_flow_candidate_context(events, payload)
    candidate_ref = (
        _first_text(
            payload,
            candidate,
            "candidate_ref",
            "target_ref",
            "branch",
        )
        or _first_text(
            {},
            fallback,
            "candidate_ref",
            "target_ref",
            "branch",
        )
    )
    candidate_head_commit = _first_text(
        payload,
        candidate,
        "candidate_head_commit",
        "commit",
    )
    artifact_refs = payload.get("artifact_refs")
    if not isinstance(artifact_refs, list):
        artifact_refs = []
    request_payload: dict[str, Any] = {
        "schema_version": "flow-discovery-request.v1",
        "workflow_run_id": (
            _first_text(
                payload,
                fallback,
                "workflow_run_id",
                "run_id",
            )
            or trace_id
        ),
        "pdd_id": pdd_id,
        "feature_id": feature_id,
        "goal_id": _first_text(
            payload,
            fallback,
            "goal_id",
            "feature_id",
            "pdd_id",
        ) or feature_id or pdd_id,
        "trace_id": trace_id,
        "flow_kind": flow_kind,
        "discovery_profile": discovery_profile,
        "quality_floor": str(metadata.get("quality_floor") or ""),
        "evidence_policy": str(metadata.get("evidence_policy") or ""),
        "environment_policy": str(metadata.get("environment_policy") or ""),
        "projection_policy": str(metadata.get("projection_policy") or ""),
        "task_map_ref": _first_text(
            payload,
            fallback,
            "task_map_ref",
            "base_task_map_ref",
            "supersedes_task_map_ref",
        ),
        "candidate_ref": candidate_ref,
        "target_ref": candidate_ref,
        "candidate_head_commit": candidate_head_commit,
        "artifact_refs": [
            str(item) for item in artifact_refs if str(item).strip()
        ],
        "source_event_id": event.id,
        "source": (
            "candidate_ready_flow_discovery_bridge"
            if event.type == "candidate.ready"
            else "post_verify_flow_discovery_bridge"
        ),
    }
    task_pipeline = metadata.get("task_pipeline")
    task_pipeline = task_pipeline if isinstance(task_pipeline, Mapping) else {}
    candidate = task_pipeline.get("candidate")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    rolling_smoke = str(candidate.get("rolling_smoke") or "").strip()
    if rolling_smoke:
        request_payload["candidate_policy"] = {
            "rolling_smoke": rolling_smoke,
        }
    for key in (
        "requirement_spec_ref",
        "requirement_spec_digest",
        "task_map_generation",
        "task_map_digest",
        "plan_artifact_package_id",
        "plan_artifact_package_ref",
        "plan_artifact_package_digest",
        "goal_claim_set_ref",
        "goal_claim_set_digest",
    ):
        value = _first_text(payload, fallback, key)
        if value:
            request_payload[key] = value
    for key in (
        "request_revision", "effective_config_ref", "effective_config_digest",
        "run_contract_ref", "run_contract_digest",
        "workflow_proposal_ref", "workflow_proposal_digest",
    ):
        value = payload.get(key) or fallback.get(key)
        if value not in (None, "", {}):
            request_payload[key] = value
    if event.type == "test.passed":
        request_payload["result_refs"] = _admitted_verify_results(
            events, payload=request_payload, fanout_id=str(payload.get("fanout_id") or ""),
        )
    return FlowDiscoveryContext(
        candidate_ref=candidate_ref,
        candidate_head_commit=candidate_head_commit,
        request_payload=request_payload,
    )


def _admitted_verify_results(
    events: Sequence[ZfEvent], *, payload: Mapping[str, Any], fanout_id: str,
) -> list[dict[str, Any]]:
    """Carry original admitted receipts from this exact completed fanout."""

    if not fanout_id:
        return []
    admissions = {
        str(item.payload.get("source_event_id")): item.payload
        for item in events
        if item.type == "workflow.call.result.admitted"
        and item.payload.get("control_result_schema") == "verification-result.v1"
    }
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in events:
        body = item.payload
        if item.type != "verify.child.completed" or body.get("fanout_id") != fanout_id:
            continue
        if any(
            str(body.get(field) or "") != str(payload.get(field) or "")
            for field in ("workflow_run_id", "task_map_generation")
        ) or body.get("target_commit") != payload.get("candidate_head_commit"):
            continue
        admission = admissions.get(item.id, {})
        if admission.get("workflow_run_id") != payload.get("workflow_run_id"):
            continue
        for field in ("control_result_ref", "envelope_ref"):
            descriptor = admission.get(field)
            if not isinstance(descriptor, Mapping):
                continue
            ref = str(descriptor.get("ref") or "")
            if not ref or not descriptor.get("sha256") or ref in seen:
                continue
            seen.add(ref)
            refs.append(dict(descriptor))
    return refs


def _latest_flow_candidate_context(
    events: Sequence[ZfEvent],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    workflow_run_id = str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or payload.get("trace_id")
        or ""
    ).strip()
    pdd_id = str(
        payload.get("pdd_id")
        or payload.get("feature_id")
        or ""
    ).strip()
    for event in reversed(events):
        if event.type not in {
            "candidate.ready",
            "candidate.integration.completed",
        }:
            continue
        candidate = event.payload if isinstance(event.payload, dict) else {}
        candidate_run_id = str(
            candidate.get("workflow_run_id")
            or candidate.get("run_id")
            or candidate.get("trace_id")
            or event.correlation_id
            or ""
        ).strip()
        candidate_pdd_id = str(
            candidate.get("pdd_id")
            or candidate.get("feature_id")
            or ""
        ).strip()
        if workflow_run_id:
            if candidate_run_id != workflow_run_id:
                continue
        elif pdd_id and candidate_pdd_id != pdd_id:
            continue
        if str(candidate.get("status") or "completed") not in {
            "completed",
            "frozen",
            "passed",
        }:
            continue
        return candidate
    return {}


def _first_text(
    primary: Mapping[str, Any],
    fallback: Mapping[str, Any],
    *keys: str,
) -> str:
    for key in keys:
        for source in (primary, fallback):
            text = str(source.get(key) or "").strip()
            if text:
                return text
    return ""
