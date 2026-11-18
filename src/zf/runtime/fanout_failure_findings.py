"""Structured failure findings collected from fanout evidence."""

from __future__ import annotations

from typing import Any

from zf.runtime.candidate_rework import candidate_quality_failure_message
from zf.runtime.verification_result import (
    verification_findings_from_payload,
    verification_result_from_payload,
    verification_rework_items_from_payload,
)


def fanout_failure_findings(
    owner: Any,
    manifest: dict[str, Any],
    *,
    extra_payloads: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    payloads = _fanout_failure_payloads(
        owner,
        manifest,
        extra_payloads=extra_payloads,
    )

    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for payload in payloads:
        for finding in findings_from_payload(payload):
            key = (
                str(finding.get("child_id") or ""),
                str(finding.get("task_id") or ""),
                str(finding.get("message") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
    return findings


def fanout_failure_context(
    owner: Any,
    manifest: dict[str, Any],
    *,
    failed: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not failed:
        return [], {}
    return (
        fanout_failure_findings(owner, manifest),
        fanout_failure_recovery(owner, manifest),
    )


def fanout_failure_recovery(
    owner: Any,
    manifest: dict[str, Any],
    *,
    extra_payloads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project admitted dependency blockers into bounded routing facts."""

    task_ids: set[str] = set()
    rework_item_ids: set[str] = set()
    semantic_result_refs: set[str] = set()
    candidate_scope = {}
    candidate_operations = set()
    candidate_unscoped = False
    reader_events = None
    seen_controls = set()
    dependency_blocked = False
    plan_port_rework = False
    payloads = _fanout_failure_payloads(
        owner,
        manifest,
        extra_payloads=extra_payloads,
    )
    typed_handoff = _typed_handoff_recovery(payloads)
    if typed_handoff:
        return typed_handoff
    for payload in payloads:
        result = verification_result_from_payload(payload)
        if not result:
            continue
        control_ref = payload.get("control_result_ref")
        if (
            str(payload.get("control_result_schema") or "")
            != "verification-result.v1"
            or not _complete_sidecar_ref(control_ref)
        ):
            continue
        if str(result.get("failure_class") or "") != "dependency_blocked":
            continue
        dependency_blocked = True
        task_id = str(result.get("task_id") or payload.get("task_id") or "").strip()
        if result.get("output_profile_id") == "candidate-verify":
            from zf.runtime.candidate_plan_recovery import candidate_reader_binding

            key = (control_ref["ref"], control_ref["sha256"])
            if key in seen_controls:
                continue
            seen_controls.add(key)
            if reader_events is None:
                reader_events = owner.event_log.read_all()
            try:
                candidate_scope, admitted = candidate_reader_binding(
                    owner.state_dir, reader_events, fanout_id=manifest["fanout_id"],
                    operation_id=str(payload.get("operation_id") or ""),
                )
                if candidate_scope["control_result_ref"] != control_ref:
                    raise ValueError("candidate recovery descriptor differs from admitted result")
                if admitted != result:
                    raise ValueError("candidate recovery body differs from admitted result")
            except (ValueError, OSError, KeyError):
                return {"failure_class": "dependency_blocked", "recovery_action": "escalate",
                        "rework_scope": "dependency", "recovery_owner": "dependency_owner",
                        "failed_task_ids": [], "semantic_result_refs": [control_ref["ref"]]}
            candidate_operations.add(result["operation_id"])
            candidate_unscoped |= not candidate_scope["failed_task_ids"]
            task_ids.update(candidate_scope["failed_task_ids"])
        elif task_id:
            task_ids.add(task_id)
        semantic_result_refs.add(str(control_ref.get("ref") or ""))
        admitted_ref = payload.get("admitted_call_result_ref")
        if _complete_sidecar_ref(admitted_ref):
            semantic_result_refs.add(str(admitted_ref.get("ref") or ""))
        for item in result.get("rework_items") or []:
            if not isinstance(item, dict):
                continue
            rework_item_id = str(item.get("rework_item_id") or "").strip()
            if rework_item_id:
                rework_item_ids.add(rework_item_id)
            scopes = item.get("allowed_scope")
            if not isinstance(scopes, list):
                continue
            if any(
                str(scope or "").strip().startswith("plan_ports:")
                for scope in scopes
            ):
                plan_port_rework = True
    if not dependency_blocked:
        return {}
    return {
        **({"candidate_event_id": candidate_scope["candidate_event_id"],
            "source_commit": candidate_scope["source_commit"],
            "candidate_verify_operation_ids": sorted(candidate_operations)} if candidate_scope else {}),
        "failure_class": "dependency_blocked",
        "recovery_action": "replan" if plan_port_rework else "escalate",
        "rework_scope": "plan_ports" if plan_port_rework else "dependency",
        "recovery_owner": "planner" if plan_port_rework else "dependency_owner",
        "failed_task_ids": [] if candidate_unscoped else sorted(task_ids),
        "rework_item_ids": sorted(rework_item_ids),
        "semantic_result_refs": sorted(
            ref for ref in semantic_result_refs if ref
        ),
    }


def _typed_handoff_recovery(
    payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    typed = [
        payload
        for payload in payloads
        if str(payload.get("handoff_failure_fingerprint") or "")
    ]
    if not typed:
        return {}
    canonical_plan = any(
        str(payload.get("failure_scope") or "") == "plan_contract"
        for payload in typed
    )
    no_progress = any(bool(payload.get("no_progress")) for payload in typed)
    task_ids = sorted({
        str(payload.get("task_id") or "")
        for payload in typed
        if str(payload.get("task_id") or "")
    })
    fingerprints = sorted({
        str(payload.get("handoff_failure_fingerprint") or "")
        for payload in typed
        if str(payload.get("handoff_failure_fingerprint") or "")
    })
    action = "return_to_plan" if canonical_plan else "result_repair"
    return {
        "failure_class": (
            "canonical_plan_contract_failure"
            if canonical_plan
            else "worker_result_contract_failure"
        ),
        "recovery_action": action,
        "rework_scope": "plan_contract" if canonical_plan else "result_payload",
        "recovery_owner": "planner" if canonical_plan else "implementation_owner",
        "failed_task_ids": task_ids,
        "handoff_failure_fingerprints": fingerprints,
        "redispatch_allowed": not canonical_plan and not no_progress,
        "no_progress": no_progress,
        "bounded_recovery_decision": (
            {
                "status": "safe_halt",
                "recovery_owner": "run_manager",
                "allowed_actions": [action, "operator_review"],
                "max_additional_writer_attempts": 0,
            }
            if no_progress
            else {}
        ),
    }


def _fanout_failure_payloads(
    owner: Any,
    manifest: dict[str, Any],
    *,
    extra_payloads: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    child_payloads = {
        str(payload.get("child_id") or ""): payload
        for payload in owner._fanout_child_payloads(manifest)
        if isinstance(payload, dict)
    }
    payloads: list[dict[str, Any]] = []
    for child in manifest.get("children", []) or []:
        if not isinstance(child, dict) or str(child.get("status") or "") != "failed":
            continue
        child_id = str(child.get("child_id") or "")
        enriched = dict(child)
        enriched.update(child_payloads.get(child_id, {}))
        payloads.append(enriched)
    payloads.extend(
        payload for payload in (extra_payloads or []) if isinstance(payload, dict)
    )
    try:
        events = owner.event_log.read_all()
    except Exception:
        events = []
    fanout_id = str(manifest.get("fanout_id") or "")
    for event in events:
        if event.type != "fanout.child.failed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("fanout_id") or "") == fanout_id:
            payloads.append(payload)
    return payloads


def _complete_sidecar_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and bool(str(value.get("ref") or "").strip())
        and bool(str(value.get("sha256") or "").strip())
    )


def findings_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_findings = payload.get("findings")
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    if not isinstance(raw_findings, list):
        raw_findings = report.get("findings")
    if not isinstance(raw_findings, list):
        raw_findings = payload.get("blocked_rework_findings")
    if not isinstance(raw_findings, list):
        raw_findings = verification_findings_from_payload(payload)
    if not isinstance(raw_findings, list):
        raw_findings = []

    out: list[dict[str, Any]] = []
    child_id = str(payload.get("child_id") or "")
    task_id = str(payload.get("task_id") or "")
    for index, raw in enumerate(raw_findings):
        if isinstance(raw, dict):
            message = str(
                raw.get("message")
                or raw.get("summary")
                or raw.get("title")
                or raw.get("reason")
                or ""
            ).strip()
            if not message:
                continue
            item = dict(raw)
            item.setdefault("finding_id", f"{child_id or task_id or 'child'}-{index + 1}")
            item.setdefault("severity", "high")
            item.setdefault("category", "verification")
            item.setdefault("child_id", child_id)
            item.setdefault("task_id", task_id)
            item["message"] = message
            out.append(item)
            continue
        message = str(raw).strip()
        if message:
            out.append({
                "finding_id": f"{child_id or task_id or 'child'}-{index + 1}",
                "severity": "high",
                "category": "verification",
                "child_id": child_id,
                "task_id": task_id,
                "message": message,
            })
    if out:
        return out
    rework_items = verification_rework_items_from_payload(payload)
    for index, raw in enumerate(rework_items):
        message = str(
            raw.get("required_delta")
            or raw.get("observed")
            or raw.get("expected")
            or raw.get("done_when")
            or ""
        ).strip()
        if not message:
            continue
        out.append({
            "finding_id": str(raw.get("rework_item_id") or "")
            or f"{child_id or task_id or 'child'}-rework-{index + 1}",
            "acceptance_id": str(raw.get("acceptance_id") or ""),
            "severity": "high",
            "category": "verification",
            "child_id": child_id,
            "task_id": task_id,
            "message": message,
            "files_or_scope": list(raw.get("allowed_scope") or []),
        })
    if out:
        return out
    reason = str(
        payload.get("reason")
        or payload.get("failure_reason")
        or payload.get("summary")
        or report.get("summary")
        or ""
    ).strip()
    if not reason:
        return []
    semantic_verdict = str(
        payload.get("semantic_verdict")
        or report.get("verdict")
        or ""
    ).strip()
    return [{
        "finding_id": f"{child_id or task_id or 'child'}-reason",
        "severity": "high",
        "category": (
            "verification"
            if semantic_verdict in {"rejected", "blocked"}
            else "runtime_failure"
        ),
        "child_id": child_id,
        "task_id": task_id,
        "message": reason,
    }]


def candidate_failure_findings(
    candidate_payload: dict[str, Any],
    *,
    status: str,
    failed_children: list[str],
) -> list[dict[str, Any]]:
    if status not in {"conflict", "quality_failed", "stale"} and not failed_children:
        return []
    findings: list[dict[str, Any]] = []
    for item in candidate_payload.get("stale_tasks") or []:
        if not isinstance(item, dict):
            continue
        findings.append({
            "finding_id": f"{item.get('task_id') or 'task'}-stale-task-ref",
            "severity": "high",
            "category": "stale_task_ref",
            "task_id": str(item.get("task_id") or ""),
            "message": (
                "candidate task ref is stale: "
                + str(item.get("reason") or "task_index_mismatch")
            ),
        })
    if status == "conflict":
        findings.append({
            "finding_id": "candidate-conflict",
            "severity": "high",
            "category": "candidate_integration",
            "message": str(candidate_payload.get("error") or "candidate conflict"),
            "files_or_scope": list(candidate_payload.get("conflict_files") or []),
        })
    if status == "quality_failed":
        quality = (
            candidate_payload.get("quality")
            if isinstance(candidate_payload.get("quality"), dict)
            else {}
        )
        findings.append({
            "finding_id": "candidate-quality-failed",
            "severity": "high",
            "category": "candidate_quality",
            "message": candidate_quality_failure_message(quality),
            "verification_command": "; ".join(
                str(command)
                for commands in (quality.get("failure_details") or {}).values()
                for command in (commands if isinstance(commands, list) else [])
            ),
            "evidence_refs": list(quality.get("gates_failed") or []),
        })
    for child in failed_children:
        if child.startswith("candidate:"):
            findings.append({
                "finding_id": "candidate-exception",
                "severity": "high",
                "category": "candidate_integration",
                "message": child,
            })
    return findings


__all__ = [
    "candidate_failure_findings",
    "fanout_failure_context",
    "fanout_failure_findings",
    "fanout_failure_recovery",
    "findings_from_payload",
]
