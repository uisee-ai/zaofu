"""Immutable top-level goal claim sets derived from accepted planning truth."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.candidate_result_binding import same_task_map_generation
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_map import load_task_map, resolve_artifact_file


SCHEMA_VERSION = "goal-claim-set.v1"
_EXPLICIT_ID = re.compile(r"^([A-Za-z][A-Za-z0-9_.-]{2,63}):\s*(.+)$")


class GoalClaimSetError(ValueError):
    """Accepted planning truth contains an ambiguous Goal claim set."""


def mandatory_claim_continuity(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    claim_waivers: Mapping[str, str] | None = None,
    active_waiver_refs: Iterable[str] = (),
) -> dict[str, Any]:
    """Mechanically reject silent mandatory-claim loss across plan generations."""

    previous_claims = {
        str(item.get("goal_claim_id") or ""): dict(item)
        for item in (previous or {}).get("claims", [])
        if isinstance(item, Mapping)
        and str(item.get("goal_claim_id") or "").strip()
        and bool(item.get("mandatory", True))
    }
    current_claims = {
        str(item.get("goal_claim_id") or ""): dict(item)
        for item in current.get("claims", [])
        if isinstance(item, Mapping)
        and str(item.get("goal_claim_id") or "").strip()
    }
    waivers = {
        str(claim_id): str(ref)
        for claim_id, ref in (claim_waivers or {}).items()
        if str(claim_id).strip() and str(ref).strip()
    }
    active = {str(ref) for ref in active_waiver_refs if str(ref).strip()}
    findings: list[dict[str, Any]] = []
    waived: list[dict[str, str]] = []
    for claim_id, before in previous_claims.items():
        after = current_claims.get(claim_id)
        reason = ""
        if after is None:
            reason = "mandatory_claim_removed"
        elif not bool(after.get("mandatory", True)):
            reason = "mandatory_claim_downgraded"
        elif str(after.get("text") or "").strip() != str(
            before.get("text") or ""
        ).strip():
            reason = "mandatory_claim_rewritten"
        if not reason:
            continue
        waiver_ref = waivers.get(claim_id, "")
        if waiver_ref and waiver_ref in active:
            waived.append({
                "goal_claim_id": claim_id,
                "waiver_ref": waiver_ref,
                "reason": reason,
            })
            continue
        findings.append({
            "code": reason,
            "goal_claim_id": claim_id,
            "previous_text": str(before.get("text") or ""),
            "current_text": str((after or {}).get("text") or ""),
            "waiver_ref": waiver_ref,
        })
    return {
        "schema_version": "goal-claim-continuity.v1",
        "status": "passed" if not findings else "rejected",
        "previous_mandatory_count": len(previous_claims),
        "current_claim_count": len(current_claims),
        "findings": findings,
        "waived": waived,
    }


def canonical_task_map_generation(
    *,
    task_map_generation: object = "",
    task_map_digest: object = "",
    task_map_ref: object = "",
) -> str:
    """Return one generation identity for explicit and legacy task maps."""

    explicit = str(task_map_generation or "").strip()
    if explicit:
        return explicit
    source = str(task_map_digest or task_map_ref or "").strip()
    return hashlib.sha256(source.encode("utf-8")).hexdigest() if source else ""


def build_goal_claim_set(
    task_map: Mapping[str, Any],
    *,
    workflow_run_id: str,
    goal_id: str,
    task_map_generation: str,
    objective_ref: str = "",
    objective: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    objective_claims = _objective_acceptance_claims(
        objective or {},
        objective_ref=objective_ref,
    )
    planning_claims = _explicit_goal_claims(task_map)
    planning_source = "task_map.goal_claims"
    if not planning_claims:
        planning_claims = _task_acceptance_claims(task_map)
        planning_source = "task_map.acceptance_criteria_fallback"
    claims = _merge_claim_groups(objective_claims, planning_claims)
    source = planning_source
    if objective_claims:
        source = f"objective.acceptance+{planning_source}" if planning_claims else "objective.acceptance"
    if not claims:
        root_text = str(task_map.get("objective") or objective_ref or goal_id).strip()
        claims = [{
            "goal_claim_id": "GOAL-ROOT",
            "text": root_text,
            "mandatory": True,
            "source_ref": objective_ref,
        }]
        source = "objective_fallback"
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "workflow_run_id": workflow_run_id,
        "goal_id": goal_id,
        "task_map_generation": task_map_generation,
        "objective_ref": objective_ref,
        "source": source,
        "claims": claims,
    }
    body["claim_set_digest"] = _digest({k: v for k, v in body.items() if k != "claim_set_digest"})
    return body


def pin_goal_claim_set_from_task_map(
    *,
    state_dir: Path,
    project_root: Path,
    task_map_ref: str,
    workflow_run_id: str,
    goal_id: str,
    task_map_generation: str,
    objective_ref: str = "",
    source_event_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = resolve_artifact_file(
        task_map_ref,
        project_root=project_root,
        state_dir=state_dir,
    )
    task_map = load_task_map(path)
    objective: Mapping[str, Any] = {}
    if objective_ref and Path(objective_ref).suffix.lower() == ".json":
        objective_path = resolve_artifact_file(
            objective_ref,
            project_root=project_root,
            state_dir=state_dir,
        )
        try:
            decoded = json.loads(objective_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GoalClaimSetError(
                f"objective_ref is not a readable requirement spec: {objective_ref}"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise GoalClaimSetError(
                f"objective_ref must contain a JSON object: {objective_ref}"
            )
        objective = decoded
    claim_set = build_goal_claim_set(
        task_map,
        workflow_run_id=workflow_run_id,
        goal_id=goal_id,
        task_map_generation=task_map_generation,
        objective_ref=objective_ref,
        objective=objective,
    )
    descriptor = write_immutable_json_sidecar(
        state_dir,
        claim_set,
        root="goal-closure/claim-sets",
        kind="goal_claim_set",
        schema_version=SCHEMA_VERSION,
        created_by="goal-claim-set-projector",
        source_event_id=source_event_id,
    )
    return claim_set, descriptor


def pin_goal_claim_set_from_requirement(
    *,
    state_dir: Path,
    project_root: Path,
    requirement_ref: str,
    requirement_digest: str,
    workflow_run_id: str,
    goal_id: str,
    workflow_generation: str,
    source_event_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pin artifact-only Goal claims directly from a confirmed Requirement."""

    requirement_path = resolve_artifact_file(
        requirement_ref,
        project_root=project_root,
        state_dir=state_dir,
    )
    try:
        raw = requirement_path.read_bytes()
        decoded = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoalClaimSetError(
            f"requirement_ref is not a readable JSON object: {requirement_ref}"
        ) from exc
    if not isinstance(decoded, Mapping):
        raise GoalClaimSetError(
            f"requirement_ref must contain a JSON object: {requirement_ref}"
        )
    actual_digest = hashlib.sha256(raw).hexdigest()
    if requirement_digest and actual_digest != requirement_digest:
        raise GoalClaimSetError("requirement spec digest mismatch")
    claim_set = build_goal_claim_set(
        {},
        workflow_run_id=workflow_run_id,
        goal_id=goal_id,
        task_map_generation=workflow_generation,
        objective_ref=requirement_ref,
        objective=decoded,
    )
    descriptor = write_immutable_json_sidecar(
        state_dir,
        claim_set,
        root="goal-closure/claim-sets",
        kind="goal_claim_set",
        schema_version=SCHEMA_VERSION,
        created_by="goal-claim-set-projector",
        source_event_id=source_event_id,
    )
    return claim_set, descriptor


def hydrate_pinned_goal_claim_set(
    *,
    state_dir: Path,
    events: Iterable[Any],
    workflow_run_id: str = "",
    goal_id: str = "",
    task_map_generation: str = "",
) -> dict[str, Any] | None:
    """Load the latest matching immutable Claim Set from its pinned ref."""

    for event in reversed(list(events)):
        if str(getattr(event, "type", "") or "") != "goal.claim_set.pinned":
            continue
        payload = getattr(event, "payload", {})
        if not isinstance(payload, Mapping):
            continue
        expected = (
            ("workflow_run_id", workflow_run_id),
            ("goal_id", goal_id),
            ("task_map_generation", task_map_generation),
        )
        mismatched = False
        for field, wanted in expected:
            actual = str(payload.get(field) or "").strip()
            if (
                actual
                and wanted
                and not _identity_value_matches(field, actual, wanted)
            ):
                mismatched = True
                break
        if mismatched:
            continue
        ref = str(payload.get("goal_claim_set_ref") or "").strip()
        digest = str(payload.get("goal_claim_set_digest") or "").strip()
        if not ref or not digest:
            raise GoalClaimSetError("pinned goal claim set ref/digest missing")
        hydrated = hydrate_sidecar_ref(
            state_dir,
            {
                "ref": ref,
                "sha256": digest,
                "kind": "goal_claim_set",
                "schema_version": SCHEMA_VERSION,
                "content_type": "application/json",
                "required": True,
            },
            purpose="goal_claim_set_projection",
            actor="goal-claim-set-projector",
        )
        if not isinstance(hydrated.payload, Mapping):
            raise GoalClaimSetError("pinned goal claim set payload is not an object")
        return validate_goal_claim_set(
            hydrated.payload,
            workflow_run_id=workflow_run_id,
            goal_id=goal_id,
            task_map_generation=task_map_generation,
        )
    return None


def validate_goal_claim_set(
    payload: Mapping[str, Any],
    *,
    workflow_run_id: str = "",
    goal_id: str = "",
    task_map_generation: str = "",
) -> dict[str, Any]:
    """Validate Claim Set schema, identity and content digest."""

    body = dict(payload)
    if str(body.get("schema_version") or "") != SCHEMA_VERSION:
        raise GoalClaimSetError("unsupported goal claim set schema")
    claims = body.get("claims")
    if not isinstance(claims, list) or not claims:
        raise GoalClaimSetError("goal claim set claims missing")
    expected = (
        ("workflow_run_id", workflow_run_id),
        ("goal_id", goal_id),
        ("task_map_generation", task_map_generation),
    )
    for field, wanted in expected:
        actual = str(body.get(field) or "").strip()
        if wanted and not _identity_value_matches(field, actual, wanted):
            raise GoalClaimSetError(f"goal claim set identity mismatch: {field}")
    claimed_digest = str(body.get("claim_set_digest") or "").strip()
    actual_digest = _digest({
        key: value
        for key, value in body.items()
        if key != "claim_set_digest"
    })
    if not claimed_digest or claimed_digest != actual_digest:
        raise GoalClaimSetError("goal claim set content digest mismatch")
    return body


def _identity_value_matches(field: str, actual: str, expected: str) -> bool:
    if field == "task_map_generation":
        return same_task_map_generation(actual, expected)
    return actual == expected


def _explicit_goal_claims(task_map: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = task_map.get("goal_claims")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if isinstance(item, Mapping):
            claim_id = str(item.get("goal_claim_id") or item.get("id") or "").strip()
            text = str(
                item.get("text")
                or item.get("statement")
                or item.get("claim")
                or item.get("title")
                or ""
            ).strip()
            mandatory = bool(item.get("mandatory", True))
            source_ref = str(item.get("source_ref") or "").strip()
        else:
            claim_id, text = _claim_id_and_text(str(item), prefix="GOAL", index=index)
            mandatory = True
            source_ref = ""
        if not claim_id or not text:
            continue
        out.append({
            "goal_claim_id": claim_id,
            "text": text,
            "mandatory": mandatory,
            "source_ref": source_ref,
        })
    return _dedupe_claims(out)


def _task_acceptance_claims(task_map: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    tasks = task_map.get("tasks")
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, Mapping):
            continue
        task_id = str(task.get("task_id") or task.get("id") or "TASK").strip()
        criteria = task.get("acceptance_criteria") or task.get("acceptance")
        for index, item in enumerate(criteria if isinstance(criteria, list) else []):
            if isinstance(item, Mapping):
                text = str(
                    item.get("text")
                    or item.get("statement")
                    or item.get("criterion")
                    or item.get("title")
                    or ""
                ).strip()
                claim_id = str(item.get("goal_claim_id") or item.get("acceptance_id") or item.get("id") or "").strip()
            else:
                claim_id, text = _claim_id_and_text(
                    str(item), prefix=f"{task_id}-AC", index=index,
                )
            if not text:
                continue
            if not claim_id:
                claim_id = _stable_id(f"{task_id}:{index}:{text}", prefix=f"{task_id}-AC")
            out.append({
                "goal_claim_id": claim_id,
                "text": text,
                "mandatory": True,
                "source_ref": f"task-map:{task_id}",
            })
    return _dedupe_claims(out)


def _objective_acceptance_claims(
    objective: Mapping[str, Any],
    *,
    objective_ref: str,
) -> list[dict[str, Any]]:
    raw = objective.get("acceptance") or objective.get("acceptance_criteria")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw if isinstance(raw, list) else []):
        if isinstance(item, Mapping):
            text = str(
                item.get("text")
                or item.get("statement")
                or item.get("criterion")
                or item.get("title")
                or ""
            ).strip()
            claim_id = str(
                item.get("goal_claim_id")
                or item.get("acceptance_id")
                or item.get("id")
                or ""
            ).strip()
            mandatory = bool(item.get("mandatory", True))
        else:
            claim_id, text = _claim_id_and_text(
                str(item),
                prefix="OBJECTIVE-AC",
                index=index,
            )
            mandatory = True
        stable_ids = objective.get("acceptance_claim_ids")
        if isinstance(stable_ids, list) and index < len(stable_ids) and stable_ids[index]:
            claim_id = str(stable_ids[index])
        if not text:
            continue
        if not claim_id:
            claim_id = _stable_id(
                f"objective:{index}:{text}",
                prefix=f"OBJECTIVE-AC{index + 1}",
            )
        out.append({
            "goal_claim_id": claim_id,
            "text": text,
            "mandatory": mandatory,
            "source_ref": objective_ref,
        })
    return _dedupe_claims(out)


def _merge_claim_groups(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for claim in (claim for group in groups for claim in group):
        claim_id = str(claim.get("goal_claim_id") or "")
        existing = seen.get(claim_id)
        if existing is None:
            seen[claim_id] = claim
            out.append(claim)
            continue
        if str(existing.get("text") or "") != str(claim.get("text") or ""):
            raise GoalClaimSetError(f"duplicate goal claim id: {claim_id}")
        existing["mandatory"] = bool(
            existing.get("mandatory", True) or claim.get("mandatory", True)
        )
    return out


def _claim_id_and_text(value: str, *, prefix: str, index: int) -> tuple[str, str]:
    text = value.strip()
    match = _EXPLICIT_ID.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    return _stable_id(text, prefix=f"{prefix}{index + 1}"), text


def _stable_id(value: str, *, prefix: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", prefix).strip("-") or "GOAL"
    return f"{clean}-{digest}"


def _dedupe_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for claim in claims:
        claim_id = str(claim.get("goal_claim_id") or "")
        if not claim_id:
            continue
        if claim_id in seen:
            raise GoalClaimSetError(f"duplicate goal claim id: {claim_id}")
        seen.add(claim_id)
        out.append(claim)
    return out


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "GoalClaimSetError",
    "SCHEMA_VERSION",
    "build_goal_claim_set",
    "canonical_task_map_generation",
    "hydrate_pinned_goal_claim_set",
    "mandatory_claim_continuity",
    "pin_goal_claim_set_from_requirement",
    "pin_goal_claim_set_from_task_map",
    "validate_goal_claim_set",
]
