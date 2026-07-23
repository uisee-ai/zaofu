"""Claim-centered, read-only delivery graph projection (design 149)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.goal_closure_result import (
    GoalClosureResultError,
    validate_goal_closure_result,
)
from zf.runtime.goal_claim_set import (
    build_goal_claim_set,
    canonical_task_map_generation,
)
from zf.runtime.verification_result import (
    VerificationResultError,
    validate_verification_result,
)


SCHEMA_VERSION = "goal-coverage-graph.v1"
EventSlice = Sequence[tuple[int, ZfEvent]]


def build_goal_coverage_graph(
    *,
    task_map: Mapping[str, Any] | None,
    tasks: Mapping[str, Task | Mapping[str, Any]],
    events: EventSlice,
    project_id: str,
    feature_id: str,
    task_map_ref: str = "",
) -> dict[str, Any]:
    """Build the current Goal Coverage projection without writing state."""

    source = dict(task_map or {})
    goal_id = str(source.get("goal_id") or source.get("feature_id") or feature_id).strip()
    closure_candidates = _goal_closure_results(events, goal_id=goal_id)
    latest_closure = closure_candidates[-1] if closure_candidates else {}
    workflow_run_id = _first_nonempty(
        source.get("workflow_run_id"),
        source.get("run_id"),
        latest_closure.get("workflow_run_id"),
        _latest_event_value(events, "workflow_run_id", "run_id"),
        f"run:{goal_id or 'unknown'}",
    )
    task_map_generation = canonical_task_map_generation(
        task_map_generation=source.get("task_map_generation"),
        task_map_digest=source.get("task_map_digest"),
        task_map_ref=task_map_ref,
    )
    target_commit = _first_nonempty(
        source.get("target_commit"),
        latest_closure.get("target_commit"),
        _latest_event_value(events, "target_commit"),
    )
    claim_set = build_goal_claim_set(
        source,
        workflow_run_id=workflow_run_id,
        goal_id=goal_id,
        task_map_generation=task_map_generation,
        objective_ref=str(source.get("objective_ref") or ""),
    )
    claim_set_ref, claim_set_digest = _goal_claim_set_binding(
        events,
        source=source,
        workflow_run_id=workflow_run_id,
        goal_id=goal_id,
        task_map_generation=task_map_generation,
        content_digest=str(claim_set.get("claim_set_digest") or ""),
    )
    coverage_mode = (
        "explicit"
        if isinstance(source.get("goal_claims"), list) and source.get("goal_claims")
        else "legacy_derived"
    )
    diagnostics: list[dict[str, Any]] = []
    current_closure, closure_diagnostics = _select_goal_closure_result(
        closure_candidates,
        workflow_run_id=workflow_run_id,
        task_map_generation=task_map_generation,
        target_commit=target_commit,
        goal_claim_set_digest=claim_set_digest,
    )
    diagnostics.extend(closure_diagnostics)
    identity = {
        "project_id": project_id,
        "workflow_run_id": workflow_run_id,
        "goal_id": goal_id,
        "task_map_generation": task_map_generation,
        "task_map_ref": task_map_ref,
        "task_map_digest": str(source.get("task_map_digest") or ""),
        "goal_claim_set_ref": str(
            current_closure.get("goal_claim_set_ref")
            or claim_set_ref
            or ""
        ),
        "goal_claim_set_digest": claim_set_digest,
        "target_commit": target_commit,
    }

    task_rows = _task_rows(source, tasks)
    verification_by_task, verification_diagnostics = _verification_results(
        events,
        task_rows=task_rows,
        workflow_run_id=workflow_run_id,
        task_map_generation=task_map_generation,
        target_commit=target_commit,
    )
    diagnostics.extend(verification_diagnostics)
    closure_rows = {
        str(item.get("goal_claim_id") or ""): dict(item)
        for item in current_closure.get("goal_coverage", [])
        if isinstance(item, Mapping) and str(item.get("goal_claim_id") or "")
    }

    nodes: list[dict[str, Any]] = [{
        "node_id": f"goal:{goal_id}",
        "kind": "goal",
        "title": _goal_title(source, goal_id),
        "goal_id": goal_id,
        "status": str(current_closure.get("verdict") or "unknown"),
    }]
    edges: list[dict[str, str]] = []
    claim_nodes: list[dict[str, Any]] = []
    result_node_ids: dict[str, str] = {}

    for task_id, row in task_rows.items():
        nodes.append({
            "node_id": f"task:{task_id}",
            "kind": "task",
            "task_id": task_id,
            "title": row["title"],
            "status": row["status"],
            "owner": row["owner"],
            "contract_revision": row["contract_revision"],
            "goal_claim_ids": row["goal_claim_ids"],
        })
        for dependency in row["blocked_by"]:
            if dependency in task_rows:
                edges.append({
                    "from": f"task:{task_id}",
                    "to": f"task:{dependency}",
                    "kind": "depends_on",
                })
        result = verification_by_task.get(task_id)
        if result:
            node_id = f"result:{result['result_ref']}"
            result_node_ids[result["result_ref"]] = node_id
            nodes.append({
                "node_id": node_id,
                "kind": "verification_result",
                "task_id": task_id,
                "title": str(result.get("summary") or f"Verification for {task_id}"),
                "status": result["status"],
                "result_ref": result["result_ref"],
                "evidence_refs": result["evidence_refs"],
                "current": result["current"],
                "stale_reasons": result["stale_reasons"],
            })
            edges.append({
                "from": node_id,
                "to": f"task:{task_id}",
                "kind": "verifies",
            })

    for claim in claim_set.get("claims", []):
        if not isinstance(claim, Mapping):
            continue
        claim_id = str(claim.get("goal_claim_id") or "")
        covering = [
            task_id for task_id, row in task_rows.items()
            if claim_id in row["goal_claim_ids"]
            or (
                coverage_mode == "legacy_derived"
                and str(claim.get("source_ref") or "") == f"task-map:{task_id}"
            )
        ]
        for task_id in covering:
            edges.append({
                "from": f"task:{task_id}",
                "to": f"claim:{claim_id}",
                "kind": "covers",
            })
        closure_row = closure_rows.get(claim_id, {})
        supporting_refs = _strings(closure_row.get("supporting_result_refs"))
        gap_refs = _claim_gap_refs(closure_row, current_closure)
        node = {
            "node_id": f"claim:{claim_id}",
            "kind": "goal_claim",
            "goal_claim_id": claim_id,
            "title": str(claim.get("text") or claim_id),
            "mandatory": bool(claim.get("mandatory", True)),
            "source_ref": str(claim.get("source_ref") or ""),
            "plan_coverage": "covered" if covering else "uncovered",
            "execution": _claim_execution(covering, task_rows),
            "task_verification": _claim_verification(covering, verification_by_task),
            "closure": str(closure_row.get("status") or "unknown"),
            "task_ids": covering,
            "supporting_result_refs": supporting_refs,
            "gap_refs": gap_refs,
        }
        claim_nodes.append(node)
        edges.append({
            "from": f"goal:{goal_id}",
            "to": f"claim:{claim_id}",
            "kind": "contains",
        })
        if node["mandatory"] and not covering:
            diagnostics.append({
                "code": "mandatory_claim_uncovered",
                "goal_claim_id": claim_id,
                "message": "mandatory claim has no covering task",
            })
        for result_ref in supporting_refs:
            result_node_id = result_node_ids.get(result_ref)
            if result_node_id is None:
                result_node_id = f"result:{result_ref}"
                result_node_ids[result_ref] = result_node_id
                nodes.append({
                    "node_id": result_node_id,
                    "kind": "verification_result",
                    "task_id": "",
                    "title": result_ref,
                    "status": "admitted",
                    "result_ref": result_ref,
                    "evidence_refs": [],
                    "current": True,
                })
            edges.append({
                "from": result_node_id,
                "to": f"claim:{claim_id}",
                "kind": "supports",
            })

    nodes[1:1] = claim_nodes
    closure_node_id = ""
    if current_closure:
        closure_ref = _first_nonempty(
            current_closure.get("closure_fact_ref"),
            current_closure.get("goal_claim_set_ref"),
            "current",
        )
        closure_node_id = f"closure:{closure_ref}"
        nodes.append({
            "node_id": closure_node_id,
            "kind": "goal_closure",
            "title": str(current_closure.get("summary") or "Goal closure"),
            "status": str(current_closure.get("verdict") or "unknown"),
            "result_ref": closure_ref,
        })
        for claim_id in closure_rows:
            edges.append({
                "from": closure_node_id,
                "to": f"claim:{claim_id}",
                "kind": "reports",
            })

    gap_refs = _strings(current_closure.get("open_gap_refs"))
    for gap_ref in gap_refs:
        gap_node_id = f"gap:{gap_ref}"
        nodes.append({
            "node_id": gap_node_id,
            "kind": "gap",
            "title": gap_ref,
            "status": "open",
            "gap_ref": gap_ref,
        })
        if closure_node_id:
            edges.append({"from": closure_node_id, "to": gap_node_id, "kind": "opens"})

    mandatory_claims = sum(1 for node in claim_nodes if node["mandatory"])
    planned_mandatory_claims = sum(
        1 for node in claim_nodes
        if node["mandatory"] and node["plan_coverage"] == "covered"
    )
    current_result_claims = sum(
        1 for node in claim_nodes
        if node["task_verification"] in {"passed", "rejected", "blocked"}
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "coverage_mode": coverage_mode,
        "identity": identity,
        "currentness": {
            "is_current_generation": True,
            "superseded_by": "",
            "stale_reasons": [],
        },
        "summary": {
            "mandatory_claims": mandatory_claims,
            "planned_claims": planned_mandatory_claims,
            "claims_with_current_results": current_result_claims,
            "closed_claims": sum(1 for node in claim_nodes if node["closure"] == "closed"),
            "open_gaps": len(gap_refs),
        },
        "nodes": nodes,
        "edges": edges,
        "diagnostics": diagnostics,
    }


def degraded_goal_coverage_graph(
    *, project_id: str, feature_id: str, reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "coverage_mode": "unmapped",
        "identity": {"project_id": project_id, "goal_id": feature_id},
        "currentness": {
            "is_current_generation": False,
            "superseded_by": "",
            "stale_reasons": ["projection_error"],
        },
        "summary": {
            "mandatory_claims": 0,
            "planned_claims": 0,
            "claims_with_current_results": 0,
            "closed_claims": 0,
            "open_gaps": 0,
        },
        "nodes": [],
        "edges": [],
        "diagnostics": [{"code": "projection_error", "message": reason}],
    }


def _task_rows(
    task_map: Mapping[str, Any],
    tasks: Mapping[str, Task | Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    planned = task_map.get("tasks") if isinstance(task_map.get("tasks"), list) else []
    planned_by_id = {
        str(item.get("task_id") or item.get("id") or ""): item
        for item in planned
        if isinstance(item, Mapping) and str(item.get("task_id") or item.get("id") or "")
    }
    ordered_ids = list(planned_by_id)
    ordered_ids.extend(sorted(task_id for task_id in tasks if task_id not in planned_by_id))
    rows: dict[str, dict[str, Any]] = {}
    for task_id in ordered_ids:
        planned_row = planned_by_id.get(task_id, {})
        task = tasks.get(task_id)
        task_data = _object_dict(task)
        contract = _object_dict(getattr(task, "contract", None))
        if not contract and isinstance(task_data.get("contract"), Mapping):
            contract = dict(task_data["contract"])
        goal_claim_ids = _strings(
            planned_row.get("goal_claim_ids") or contract.get("goal_claim_ids")
        )
        rows[task_id] = {
            "title": str(planned_row.get("title") or task_data.get("title") or task_id),
            "status": str(task_data.get("status") or "waiting"),
            "owner": str(task_data.get("assigned_to") or contract.get("owner_instance") or contract.get("owner_role") or planned_row.get("owner_role") or ""),
            "contract_revision": str(contract.get("contract_revision") or ""),
            "goal_claim_ids": goal_claim_ids,
            "blocked_by": _strings(planned_row.get("blocked_by") or task_data.get("blocked_by")),
        }
    return rows


def _verification_results(
    events: EventSlice,
    *,
    task_rows: Mapping[str, Mapping[str, Any]],
    workflow_run_id: str,
    task_map_generation: str,
    target_commit: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    out: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    admissions = _admitted_results_by_source(events)
    for _seq, event in events:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        raw = payload.get("verification_result")
        if not isinstance(raw, Mapping):
            report = payload.get("report") if isinstance(payload.get("report"), Mapping) else {}
            raw = report.get("verification_result")
        if not isinstance(raw, Mapping):
            continue
        result = dict(raw)
        if str(result.get("schema_version") or "") != "verification-result.v1":
            continue
        task_id = str(result.get("task_id") or event.task_id or "")
        if task_id not in task_rows:
            continue
        admitted_ref = _admitted_result_ref(
            payload,
            admissions.get(event.id, {}),
            expected_schema="verification-result.v1",
        )
        stale_reasons = _verification_stale_reasons(
            result,
            admitted=bool(admitted_ref),
            task_id=task_id,
            workflow_run_id=workflow_run_id,
            task_map_generation=task_map_generation,
            target_commit=target_commit,
            contract_revision=str(task_rows[task_id].get("contract_revision") or ""),
        )
        current = not stale_reasons
        verdict = str(result.get("verdict") or "unverified")
        status = verdict if current else "stale"
        if str(result.get("execution_status") or "") == "failed" and current:
            status = "blocked"
        result_payload = dict(payload)
        if admitted_ref:
            result_payload["admitted_call_result_ref"] = admitted_ref
        ref = _result_ref(result_payload, result, event)
        candidate = {
            "status": status,
            "current": current,
            "result_ref": ref,
            "summary": str(result.get("summary") or ""),
            "evidence_refs": _strings(result.get("evidence_refs")),
            "stale_reasons": stale_reasons,
        }
        existing = out.get(task_id)
        if existing is None or current or not bool(existing.get("current")):
            out[task_id] = candidate
        if stale_reasons:
            diagnostics.append({
                "code": "stale_verification_result",
                "task_id": task_id,
                "result_ref": ref,
                "stale_reasons": stale_reasons,
                "message": "verification result is not current for this delivery",
            })
    return out, diagnostics


def _goal_closure_results(events: EventSlice, *, goal_id: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    admissions = _admitted_results_by_source(events)
    for _seq, event in events:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        raw = payload.get("goal_closure_result")
        if not isinstance(raw, Mapping):
            report = payload.get("report") if isinstance(payload.get("report"), Mapping) else {}
            raw = report.get("goal_closure_result")
        if not isinstance(raw, Mapping):
            continue
        result = dict(raw)
        if str(result.get("schema_version") or "") != "goal-closure-result.v1":
            continue
        result_goal_id = str(result.get("goal_id") or "")
        if goal_id and result_goal_id and result_goal_id != goal_id:
            continue
        admitted_ref = _admitted_result_ref(
            payload,
            admissions.get(event.id, {}),
            expected_schema="goal-closure-result.v1",
        )
        result["_projection_admitted_ref"] = admitted_ref
        results.append(result)
    return results


def _select_goal_closure_result(
    candidates: Sequence[Mapping[str, Any]],
    *,
    workflow_run_id: str,
    task_map_generation: str,
    target_commit: str,
    goal_claim_set_digest: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    current: dict[str, Any] = {}
    diagnostics: list[dict[str, Any]] = []
    for raw in reversed(candidates):
        result = dict(raw)
        stale_reasons = _goal_closure_stale_reasons(
            result,
            workflow_run_id=workflow_run_id,
            task_map_generation=task_map_generation,
            target_commit=target_commit,
            goal_claim_set_digest=goal_claim_set_digest,
        )
        if not stale_reasons and not current:
            current = result
            continue
        if stale_reasons:
            diagnostics.append({
                "code": "stale_goal_closure_result",
                "result_ref": _first_nonempty(
                    result.get("closure_fact_ref"),
                    result.get("goal_claim_set_ref"),
                ),
                "stale_reasons": stale_reasons,
                "message": "goal closure result is not current for this delivery",
            })
    diagnostics.reverse()
    return current, diagnostics


def _claim_execution(
    task_ids: list[str], task_rows: Mapping[str, Mapping[str, Any]],
) -> str:
    if not task_ids:
        return "pending"
    statuses = {str(task_rows[task_id].get("status") or "waiting") for task_id in task_ids}
    if statuses & {"blocked"}:
        return "blocked"
    if statuses & {"failed", "cancelled"}:
        return "failed"
    if statuses <= {"done", "completed", "shipped"}:
        return "done"
    if statuses & {"in_progress", "review", "test", "judge", "dispatched"}:
        return "running"
    return "pending"


def _claim_verification(
    task_ids: list[str], results: Mapping[str, Mapping[str, Any]],
) -> str:
    if not task_ids:
        return "unverified"
    statuses = [str(results[task_id].get("status") or "") for task_id in task_ids if task_id in results]
    if "rejected" in statuses:
        return "rejected"
    if "blocked" in statuses:
        return "blocked"
    if len(statuses) == len(task_ids) and statuses and set(statuses) == {"passed"}:
        return "passed"
    if "stale" in statuses and not set(statuses) & {"passed", "rejected", "blocked"}:
        return "stale"
    return "unverified"


def _verification_stale_reasons(
    result: Mapping[str, Any],
    *,
    admitted: bool,
    task_id: str,
    workflow_run_id: str,
    task_map_generation: str,
    target_commit: str,
    contract_revision: str = "",
) -> list[str]:
    reasons: list[str] = []
    if not admitted:
        reasons.append("not_admitted")
    try:
        validate_verification_result(result, strict=False)
    except VerificationResultError as exc:
        reasons.append(f"schema_invalid:{exc}")
    reasons.extend(_identity_stale_reasons(
        result,
        (
            ("workflow_run_id", workflow_run_id),
            ("task_id", task_id),
            ("task_map_generation", task_map_generation),
            ("target_commit", target_commit),
            ("contract_revision", contract_revision),
        ),
    ))
    return list(dict.fromkeys(reasons))


def _goal_closure_stale_reasons(
    result: Mapping[str, Any],
    *,
    workflow_run_id: str,
    task_map_generation: str,
    target_commit: str,
    goal_claim_set_digest: str,
) -> list[str]:
    reasons: list[str] = []
    if not result.get("_projection_admitted_ref"):
        reasons.append("not_admitted")
    try:
        validate_goal_closure_result(result)
    except GoalClosureResultError as exc:
        reasons.append(f"schema_invalid:{exc}")
    reasons.extend(_identity_stale_reasons(
        result,
        (
            ("workflow_run_id", workflow_run_id),
            ("task_map_generation", task_map_generation),
            ("target_commit", target_commit),
            ("goal_claim_set_digest", goal_claim_set_digest),
        ),
    ))
    return list(dict.fromkeys(reasons))


def _identity_stale_reasons(
    result: Mapping[str, Any],
    checks: Sequence[tuple[str, str]],
) -> list[str]:
    reasons: list[str] = []
    for field, expected in checks:
        if not expected:
            continue
        actual = str(result.get(field) or "").strip()
        if not actual:
            reasons.append(f"missing:{field}")
        elif actual != expected:
            reasons.append(f"mismatch:{field}")
    return reasons


def _admitted_results_by_source(events: EventSlice) -> dict[str, dict[str, Any]]:
    admissions: dict[str, dict[str, Any]] = {}
    for _seq, event in events:
        if event.type != "workflow.call.result.admitted":
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        if str(payload.get("admission_status") or "") != "admitted":
            continue
        source_event_id = str(payload.get("source_event_id") or "")
        if source_event_id:
            admissions[source_event_id] = dict(payload)
    return admissions


def _admitted_result_ref(
    payload: Mapping[str, Any],
    admission: Mapping[str, Any],
    *,
    expected_schema: str,
) -> dict[str, Any]:
    if admission:
        schema = str(admission.get("control_result_schema") or "")
        descriptor = admission.get("envelope_ref")
        if schema != expected_schema:
            return {}
    else:
        schema = str(payload.get("control_result_schema") or expected_schema)
        descriptor = payload.get("admitted_call_result_ref")
        if schema != expected_schema:
            return {}
    if not isinstance(descriptor, Mapping):
        return {}
    ref = str(descriptor.get("ref") or "")
    digest = str(descriptor.get("sha256") or "")
    return dict(descriptor) if ref and digest else {}


def _goal_claim_set_binding(
    events: EventSlice,
    *,
    source: Mapping[str, Any],
    workflow_run_id: str,
    goal_id: str,
    task_map_generation: str,
    content_digest: str,
) -> tuple[str, str]:
    ref = str(source.get("goal_claim_set_ref") or "")
    digest = str(source.get("goal_claim_set_digest") or "")
    for _seq, event in events:
        if event.type != "goal.claim_set.pinned":
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        identities = (
            (str(payload.get("workflow_run_id") or ""), workflow_run_id),
            (str(payload.get("goal_id") or ""), goal_id),
            (str(payload.get("task_map_generation") or ""), task_map_generation),
        )
        if any(expected and actual != expected for actual, expected in identities):
            continue
        ref = str(payload.get("goal_claim_set_ref") or ref)
        digest = str(payload.get("goal_claim_set_digest") or digest)
    return ref, digest or content_digest


def _claim_gap_refs(
    closure_row: Mapping[str, Any], closure_result: Mapping[str, Any],
) -> list[str]:
    direct = _strings(closure_row.get("gap_refs") or closure_row.get("open_gap_refs"))
    if direct:
        return direct
    if str(closure_row.get("status") or "") in {"open", "blocked"}:
        return _strings(closure_result.get("open_gap_refs"))
    return []


def _result_ref(
    payload: Mapping[str, Any], result: Mapping[str, Any], event: ZfEvent,
) -> str:
    admitted = payload.get("admitted_call_result_ref")
    if isinstance(admitted, Mapping):
        admitted = admitted.get("ref") or admitted.get("path")
    return _first_nonempty(
        result.get("result_ref"), admitted, f"event:{event.id or event.type}"
    )


def _goal_title(task_map: Mapping[str, Any], goal_id: str) -> str:
    objective = task_map.get("objective")
    if isinstance(objective, Mapping):
        objective = objective.get("title") or objective.get("summary")
    return _first_nonempty(task_map.get("goal_title"), objective, goal_id, "Goal")


def _latest_event_value(events: EventSlice, *keys: str) -> str:
    for _seq, event in reversed(events):
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        for key in keys:
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    return ""


def _object_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value is None:
        return {}
    if is_dataclass(value):
        return asdict(value)
    return dict(vars(value)) if hasattr(value, "__dict__") else {}


def _strings(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value] if value else []
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _first_nonempty(*values: Any) -> str:
    return next((str(value).strip() for value in values if str(value or "").strip()), "")


__all__ = [
    "SCHEMA_VERSION",
    "build_goal_coverage_graph",
    "degraded_goal_coverage_graph",
]
