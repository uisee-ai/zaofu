"""Immutable top-level goal claim sets derived from accepted planning truth."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.task_map import load_task_map, resolve_artifact_file


SCHEMA_VERSION = "goal-claim-set.v1"
_EXPLICIT_ID = re.compile(r"^([A-Za-z][A-Za-z0-9_.-]{2,63}):\s*(.+)$")


class GoalClaimSetError(ValueError):
    """Accepted planning truth contains an ambiguous Goal claim set."""


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
) -> dict[str, Any]:
    claims = _explicit_goal_claims(task_map)
    source = "task_map.goal_claims"
    if not claims:
        claims = _task_acceptance_claims(task_map)
        source = "task_map.acceptance_criteria_fallback"
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
    claim_set = build_goal_claim_set(
        task_map,
        workflow_run_id=workflow_run_id,
        goal_id=goal_id,
        task_map_generation=task_map_generation,
        objective_ref=objective_ref,
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


def _explicit_goal_claims(task_map: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = task_map.get("goal_claims")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if isinstance(item, Mapping):
            claim_id = str(item.get("goal_claim_id") or item.get("id") or "").strip()
            text = str(item.get("text") or item.get("claim") or item.get("title") or "").strip()
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
                text = str(item.get("text") or item.get("criterion") or item.get("title") or "").strip()
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
    "pin_goal_claim_set_from_task_map",
]
