"""Workflow stage criteria and output contract helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from zf.core.config.schema import WorkflowStageConfig
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.long_horizon import (
    CriterionResult,
    SuccessCriterion,
    evaluate_success_criteria,
)


@dataclass(frozen=True)
class StageContractResult:
    stage_id: str
    passed: bool
    missing_output_keys: list[str] = field(default_factory=list)
    missing_artifacts: list[str] = field(default_factory=list)
    missing_artifact_kinds: list[str] = field(default_factory=list)
    criteria_results: list[CriterionResult] = field(default_factory=list)
    matched_event_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["criteria_results"] = [
            {
                "criterion": asdict(item.criterion),
                "passed": item.passed,
                "reason": item.reason,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in self.criteria_results
        ]
        return data


def evaluate_stage_contract(
    *,
    stage: WorkflowStageConfig,
    task: Task,
    events: list[ZfEvent],
    state_dir: Path,
    project_root: Path | None = None,
) -> StageContractResult:
    """Evaluate configured stage criteria for one task.

    The helper is deterministic and read-only. Empty criteria pass by default
    so old zf.yaml files keep their previous behavior.
    """

    task_events = [
        event for event in events
        if event.task_id == task.id or _payload_mentions(event.payload, task.id)
    ]
    matched = [
        event for event in task_events
        if not stage.trigger or event.type == stage.trigger
    ]
    missing_keys = _missing_output_keys(stage, matched)
    missing_artifacts = _missing_artifacts(stage, project_root or state_dir.parent, state_dir)
    missing_kinds = _missing_artifact_kinds(stage, task_events)
    criteria = _criteria_from_stage(stage)
    criteria_results = evaluate_success_criteria(
        criteria,
        task=task,
        state_dir=state_dir,
        events=events,
        project_root=project_root,
    )
    passed = (
        not missing_keys
        and not missing_artifacts
        and not missing_kinds
        and all(item.passed for item in criteria_results)
    )
    return StageContractResult(
        stage_id=stage.id,
        passed=passed,
        missing_output_keys=missing_keys,
        missing_artifacts=missing_artifacts,
        missing_artifact_kinds=missing_kinds,
        criteria_results=criteria_results,
        matched_event_ids=[event.id for event in matched],
    )


def _criteria_from_stage(stage: WorkflowStageConfig) -> list[SuccessCriterion]:
    criteria: list[SuccessCriterion] = []
    for raw in stage.criteria.success_criteria:
        criterion = SuccessCriterion.from_obj(raw)
        if criterion is not None:
            criteria.append(criterion)
    for path in stage.criteria.output.required_artifacts:
        criteria.append(SuccessCriterion(kind="artifact_exists", path=path))
    return criteria


def _missing_output_keys(stage: WorkflowStageConfig, events: list[ZfEvent]) -> list[str]:
    required = list(stage.criteria.output.required_keys)
    if not required:
        return []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        missing = [key for key in required if not _has_nested_key(payload, key)]
        if not missing:
            return []
    return required


def _missing_artifacts(stage: WorkflowStageConfig, project_root: Path, state_dir: Path) -> list[str]:
    missing: list[str] = []
    for raw in stage.criteria.output.required_artifacts:
        path = Path(raw)
        candidates = [path] if path.is_absolute() else [project_root / path, state_dir / path]
        if not any(candidate.exists() for candidate in candidates):
            missing.append(raw)
    return missing


def _missing_artifact_kinds(stage: WorkflowStageConfig, events: list[ZfEvent]) -> list[str]:
    required = list(stage.criteria.output.artifact_kinds)
    if not required:
        return []
    observed: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        refs = payload.get("artifact_refs") or payload.get("artifacts") or []
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if isinstance(ref, dict):
                observed.add(str(ref.get("kind") or ""))
    return [kind for kind in required if kind not in observed]


def _payload_mentions(payload: Any, needle: str) -> bool:
    if not needle:
        return False
    if isinstance(payload, dict):
        return any(
            _payload_mentions(key, needle) or _payload_mentions(value, needle)
            for key, value in payload.items()
        )
    if isinstance(payload, list | tuple | set):
        return any(_payload_mentions(item, needle) for item in payload)
    return str(payload) == needle


def _has_nested_key(payload: dict[str, Any], key: str) -> bool:
    current: Any = payload
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


__all__ = [
    "StageContractResult",
    "evaluate_stage_contract",
]
