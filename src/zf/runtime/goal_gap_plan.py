"""Goal-closure gap plan compatibility helpers.

The canonical implementation currently lives in ``module_gap_plan`` because
Hermes/Cangjie module parity shipped first. This module exposes the generic
names used by issue, PRD, and refactor workflows without creating a second
runtime path.
"""

from __future__ import annotations

from zf.runtime.module_gap_plan import (
    ModuleGapPlanValidationResult as GoalGapPlanValidationResult,
    build_gap_task_map_amend,
    gap_tasks_from_gap_plan_payload,
    gap_tasks_from_rework_summary,
    validate_module_gap_plan_payload,
    write_gap_task_map_amend_artifact,
)


def validate_goal_gap_plan_payload(payload):
    return validate_module_gap_plan_payload(payload)


__all__ = [
    "GoalGapPlanValidationResult",
    "build_gap_task_map_amend",
    "gap_tasks_from_gap_plan_payload",
    "gap_tasks_from_rework_summary",
    "validate_goal_gap_plan_payload",
    "write_gap_task_map_amend_artifact",
]
