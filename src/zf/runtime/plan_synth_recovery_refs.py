"""Recover the exact candidate inputs bound to a failed Plan Critic."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


def failed_critic_candidate_refs(
    state_dir: Path, events: Sequence[ZfEvent], payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return only child sources pinned by this Critic's contract and manifest.

    Missing, ambiguous, tampered or mismatched evidence is not a reason to
    substitute a candidate from an earlier replan.
    """
    semantic_rejection = (
        payload.get("execution_status") == "completed"
        and payload.get("verdict") == "rejected"
    )
    if payload.get("failure_class") != "reader_execution_failure" and not semantic_rejection:
        return []
    operation_id = str(payload.get("operation_id") or "")
    attempt_id = str(payload.get("attempt_id") or "")
    fanout_id = str(payload.get("fanout_id") or "")
    digest = str(payload.get("plan_synth_contract_digest") or "")
    ref = str(payload.get("plan_synth_contract_ref") or "")
    if not all((operation_id, attempt_id, fanout_id, digest, ref)):
        return []
    if payload.get("reviewed_plan_candidate_digest") != digest:
        return []
    matches = [event for event in events if event.type == "fanout.synth.dispatched"
               and event.payload.get("operation_id") == operation_id
               and event.payload.get("attempt_id") == attempt_id
               and event.payload.get("fanout_id") == fanout_id]
    if not matches:
        return []
    # A transport retry may redispatch the same frozen request. It is not a
    # second candidate; unlinked or differently bound dispatches stay ambiguous.
    first = matches[0]
    seen = {first.id}
    identity_keys = (
        "request_hash", "run_id", "workflow_run_id", "plan_revision",
        "plan_synth_contract_ref", "plan_synth_contract_digest",
        "reviewed_plan_candidate_digest", "attempt_source_manifest",
    )
    for retry in matches[1:]:
        if (retry.payload.get("retry_of_event_id") not in seen
                or retry.payload.get("semantic_attempt_consumed") is not False
                or not first.payload.get("request_hash")
                or any(retry.payload.get(key) != first.payload.get(key) for key in identity_keys)):
            return []
        seen.add(retry.id)
    descriptor = matches[0].payload.get("attempt_source_manifest")
    if not isinstance(descriptor, dict):
        return []
    try:
        contract = hydrate_sidecar_ref(state_dir, {"ref": ref, "sha256": digest}).payload
        manifest = hydrate_sidecar_ref(state_dir, descriptor).payload
    except (SidecarRefError, ValueError):
        return []
    if not isinstance(contract, dict) or not isinstance(manifest, dict):
        return []
    if contract.get("schema_version") != "plan-synth-contract.v1":
        return []
    for key in ("workflow_run_id", "fanout_id", "stage_id", "plan_revision"):
        if not payload.get(key) or contract.get(key) != payload.get(key):
            return []
    if (manifest.get("schema_version") != "attempt-source-manifest.v1"
            or manifest.get("attempt_id") != attempt_id
            or manifest.get("workflow_run_id") != payload.get("workflow_run_id")
            or manifest.get("dispatch_id") != matches[0].payload.get("run_id")):
        return []
    children = contract.get("child_bindings")
    bindings = contract.get("source_bindings")
    sources = manifest.get("sources")
    if not all(isinstance(value, list) and value for value in (children, bindings, sources)):
        return []
    child_ids = {str(row.get("child_id") or "") for row in children if isinstance(row, dict)}
    if not child_ids or "" in child_ids or len(child_ids) != len(children):
        return []
    result: list[dict[str, Any]] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            return []
        source_id = str(binding.get("source_id") or "")
        if not any(source_id == f"child-result-{child}" or source_id.startswith(
            (f"child-{child}-", f"child-artifact-{child}-")
        ) for child in child_ids):
            continue
        candidates = [source for source in sources if isinstance(source, dict)
                      and source.get("source_id") == source_id
                      and all(binding.get(key) and source.get(key) == binding.get(key)
                              for key in ("artifact_id", "ref", "sha256"))]
        if len(candidates) != 1:
            return []
        result.append(dict(candidates[0]))
    for child in children:
        if not any(row.get("source_id") == child.get("source_id")
                   and row.get("artifact_id") == child.get("artifact_id")
                   and row.get("sha256") == child.get("sha256") for row in result):
            return []
    return result


def resume_failed_critic_candidate(
    state_dir: Path, events: Sequence[ZfEvent], failure: ZfEvent, synth_id: str,
) -> list[dict[str, Any]]:
    """Recover the rejected candidate, or a preceding failed reader checkpoint."""
    by_id = {event.id: event for event in events}
    synth = by_id.get(synth_id)
    previous = by_id.get(str(failure.payload.get("rework_of") or ""))
    direct_rejection = failure.causation_id == synth_id
    if direct_rejection:
        previous = failure
    if (synth is None or synth.type != "fanout.synth.completed" or previous is None
            or previous.type != failure.type or previous.causation_id != synth.id):
        raise ValueError("candidate checkpoint is not the preceding failed Critic")
    run = failure.payload.get("workflow_run_id")
    stage = failure.payload.get("stage_id")
    if not run or not stage or any(event.payload.get("workflow_run_id") != run
            or event.payload.get("stage_id") != stage for event in (previous, synth)):
        raise ValueError("candidate checkpoint belongs to a different Run or stage")
    begin, end = events.index(synth), events.index(failure)
    if begin >= end or any(event.type in {"prd.plan.child.completed", "task_map.ready"}
            and event.payload.get("workflow_run_id") == run
            for event in events[begin + 1:end]):
        raise ValueError("candidate checkpoint was superseded")
    admissions = [event for event in events if event.type == "workflow.call.result.admitted"
                  and event.payload.get("source_event_id") == synth.id
                  and event.payload.get("workflow_run_id") == run
                  and event.payload.get("operation_id") == synth.payload.get("operation_id")]
    if len(admissions) != 1:
        raise ValueError("candidate checkpoint has no unique admitted result")
    admission = admissions[0].payload
    if admission.get("control_result_ref") != synth.payload.get("control_result_ref"):
        raise ValueError("candidate checkpoint control result differs from admission")
    try:
        from zf.runtime.call_result_envelope import hydrate_call_result_envelope
        from zf.runtime.call_result_runtime import hydrate_admitted_control_result
        envelope = hydrate_call_result_envelope(state_dir, admission.get("envelope_ref") or {})
        envelope_control = envelope.get("control_result") or envelope.get("control_result_ref")
        admitted_control = admission.get("control_result_ref")
        if (not isinstance(envelope_control, Mapping) or not isinstance(admitted_control, Mapping)
                or any(not envelope_control.get(key) or envelope_control.get(key) != admitted_control.get(key)
                       for key in ("ref", "sha256", "schema_version"))):
            raise ValueError("candidate checkpoint envelope differs from admission")
        control = hydrate_admitted_control_result(state_dir, admission.get("envelope_ref") or {})
    except (ValueError, SidecarRefError) as exc:
        raise ValueError("candidate checkpoint admitted result cannot be read") from exc
    if direct_rejection:
        if control.get("execution_status") != "completed" or control.get("verdict") != "rejected":
            raise ValueError("candidate checkpoint is not an admitted semantic rejection")
    elif control.get("execution_status") != "failed" or control.get("failure_class") != "reader_execution_failure":
        raise ValueError("candidate checkpoint is not an admitted reader failure")
    refs = failed_critic_candidate_refs(state_dir, events, {**synth.payload, **control})
    if not refs:
        raise ValueError("candidate checkpoint has no verified candidate inputs")
    return refs
