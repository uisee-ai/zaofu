"""Long-horizon work unit, feedback, and recovery projections.

These helpers are deterministic read models over the existing ZaoFu stores.
They do not introduce a second control plane: task/feature truth remains in
TaskStore/FeatureStore and events.jsonl.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.core.task.schema import Task
from zf.core.task.store import TERMINAL_STATES, TaskStore
from zf.runtime.recovery_sufficiency import build_artifact_recovery_refs


SUCCESS_EVENT_TYPES = {
    "dev.build.done",
    "arch.proposal.done",
    "design.critique.done",
    "review.approved",
    "verify.passed",
    "test.passed",
    "judge.passed",
    "static_gate.passed",
    "gate.passed",
    "discriminator.passed",
    "task.done.evidence",
}

FAILURE_EVENT_TYPES = {
    "review.rejected",
    "verify.failed",
    "test.failed",
    "judge.failed",
    "static_gate.failed",
    "gate.failed",
    "discriminator.failed",
}

COMPLEXITY_LEVELS = {"simple", "standard", "complex", "release"}

_COMPLEXITY_STRICT_PATHS = (
    "src/zf/runtime/**",
    "src/zf/core/config/**",
    "src/zf/core/security/**",
    "src/zf/web/**",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _age_seconds(ts: str, *, now: datetime | None = None) -> float | None:
    parsed = _parse_ts(ts)
    if parsed is None:
        return None
    now = now or datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed).total_seconds())


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


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


def _event_task_match(event: ZfEvent, task_id: str) -> bool:
    return event.task_id == task_id or _payload_mentions(event.payload, task_id)


def _events_for_task(events: Iterable[ZfEvent], task_id: str) -> list[ZfEvent]:
    return [event for event in events if _event_task_match(event, task_id)]


def _event_text(event: ZfEvent) -> str:
    parts = [event.type, event.actor or ""]
    payload = event.payload if isinstance(event.payload, dict) else {}
    for key in (
        "summary",
        "message",
        "reason",
        "command",
        "check",
        "status",
        "output_summary",
    ):
        value = payload.get(key)
        if value:
            parts.append(str(value))
    for key in ("commands", "checks", "evidence_refs", "artifact_refs"):
        value = payload.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
    return "\n".join(parts)


def task_complexity(task: Task) -> str:
    """Return explicit or inferred complexity for a task.

    Empty/unknown explicit values fall back to a deterministic heuristic
    instead of adding another operator-maintained control plane.
    """
    explicit = str(getattr(task.contract, "complexity", "") or "").strip().lower()
    if explicit in COMPLEXITY_LEVELS:
        return explicit
    evidence = task.contract.evidence_contract or {}
    if isinstance(evidence, dict):
        value = str(evidence.get("complexity") or "").strip().lower()
        if value in COMPLEXITY_LEVELS:
            return value
    validation = task.contract.validation or {}
    if isinstance(validation, dict):
        value = str(validation.get("complexity") or "").strip().lower()
        if value in COMPLEXITY_LEVELS:
            return value
    return infer_task_complexity(task)


def infer_task_complexity(task: Task) -> str:
    files = _task_file_surface(task)
    if _boundary_from_task(task) in {"release", "ship"}:
        return "release"
    if len(files) >= 8:
        return "complex"
    if any(fnmatch.fnmatch(path, pattern) for pattern in _COMPLEXITY_STRICT_PATHS for path in files):
        return "complex"
    tiers = {str(item).strip().lower() for item in task.contract.verification_tiers}
    if {"e2e", "manual_evidence"} & tiers:
        return "complex"
    if len(files) <= 1 and not task.blocked_by:
        return "simple"
    return "standard"


def complexity_strict_reasons_for_task(
    task: Task,
    *,
    config: ZfConfig | None = None,
    context_usage_ratio: float | None = None,
) -> list[str]:
    reasons: list[str] = []
    complexity = task_complexity(task)
    if complexity == "release":
        reasons.append("complexity=release")
    elif complexity == "complex":
        reasons.append("complexity=complex")
    triggers = getattr(getattr(config, "workflow", None), "strict_triggers", None)
    if triggers is not None:
        if triggers.rework_attempts_gte and task.retry_count >= triggers.rework_attempts_gte:
            reasons.append(f"retry_count >= {triggers.rework_attempts_gte}")
        if (
            context_usage_ratio is not None
            and triggers.context_usage_gte
            and context_usage_ratio >= triggers.context_usage_gte
        ):
            reasons.append(f"context_usage_ratio >= {triggers.context_usage_gte}")
        files = _task_file_surface(task)
        for pattern in triggers.file_globs:
            if any(fnmatch.fnmatch(path, pattern) for path in files):
                reasons.append(f"file_glob:{pattern}")
                break
    return reasons


def _task_file_surface(task: Task) -> set[str]:
    contract = task.contract
    return (
        set(contract.scope)
        | set(contract.affected_files)
        | set(contract.shared_files)
        | set(contract.exclusive_files)
        | set(contract.handoff_artifacts)
    )


def _boundary_from_task(task: Task) -> str:
    evidence = task.contract.evidence_contract or {}
    if isinstance(evidence, dict):
        value = str(evidence.get("boundary") or evidence.get("profile_boundary") or "")
        if value:
            return value
    return str(task.contract.validation.get("boundary") or "") if isinstance(task.contract.validation, dict) else ""


@dataclass(frozen=True)
class SuccessCriterion:
    kind: str
    value: str = ""
    path: str = ""
    command: str = ""
    event_type: str = ""
    state: str = ""
    contains: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_obj(cls, obj: Any) -> "SuccessCriterion | None":
        if isinstance(obj, str):
            text = obj.strip()
            if not text:
                return None
            if text.startswith("event:"):
                return cls(kind="event_exists", event_type=text.split(":", 1)[1].strip())
            if text.startswith("file:"):
                return cls(kind="file_exists", path=text.split(":", 1)[1].strip())
            return cls(kind="command_passed", command=text)
        if not isinstance(obj, dict):
            return None
        kind = str(obj.get("kind") or obj.get("type") or "").strip()
        if not kind:
            return None
        return cls(
            kind=kind,
            value=str(obj.get("value") or ""),
            path=str(obj.get("path") or ""),
            command=str(obj.get("command") or obj.get("value") or ""),
            event_type=str(obj.get("event_type") or obj.get("event") or obj.get("value") or ""),
            state=str(obj.get("state") or obj.get("status") or obj.get("value") or ""),
            contains=str(obj.get("contains") or obj.get("value") or ""),
            params=dict(obj),
        )


@dataclass
class CriterionResult:
    criterion: SuccessCriterion
    passed: bool
    reason: str
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class WorkUnitContract:
    id: str
    task_id: str
    feature_id: str
    title: str
    goal: str
    outcome: str
    scope_include: list[str] = field(default_factory=list)
    scope_exclude: list[str] = field(default_factory=list)
    owner_role: str = ""
    owner_instance: str = ""
    depends_on: list[str] = field(default_factory=list)
    expected_artifacts: list[dict[str, Any]] = field(default_factory=list)
    validation_surface: dict[str, list[str]] = field(default_factory=dict)
    acceptance_criteria: list[str] = field(default_factory=list)
    boundary: str = "worker_task"
    complexity: str = "standard"
    effective_profile: str = "baseline"
    effective_profile_reason: list[str] = field(default_factory=list)
    success_criteria: list[SuccessCriterion] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["success_criteria"] = [asdict(item) for item in self.success_criteria]
        return data


@dataclass
class WhyNotDoneReason:
    kind: str
    severity: str
    message: str
    expected: str = ""
    owner_role: str = ""
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class RecommendedAction:
    kind: str
    role: str = ""
    reason: str = ""


@dataclass
class WhyNotDoneProjection:
    task_id: str
    state: str
    work_unit: WorkUnitContract | None
    why_not_done: list[WhyNotDoneReason] = field(default_factory=list)
    recommended_action: RecommendedAction = field(
        default_factory=lambda: RecommendedAction(kind="none")
    )
    next_required_event: str = ""
    freshness: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "state": self.state,
            "effective_profile": (
                self.work_unit.effective_profile if self.work_unit else "baseline"
            ),
            "work_unit": self.work_unit.to_dict() if self.work_unit else None,
            "why_not_done": [asdict(item) for item in self.why_not_done],
            "recommended_action": asdict(self.recommended_action),
            "next_required_event": self.next_required_event,
            "freshness": self.freshness,
        }


@dataclass
class CompletionAuditResult:
    task_id: str
    route: str
    reason: str
    missing_evidence: list[dict[str, Any]] = field(default_factory=list)
    recommended_role: str = ""
    next_required_event: str = ""
    work_unit_id: str = ""
    dispatch_id: str = ""
    attempt: int = 0
    trigger_event_type: str = ""
    trigger_event_id: str = ""
    resume_packet_path: str = ""
    resume_packet_missing_evidence_count: int = 0
    previous_snapshot_ref: str = ""
    recovery_snapshot_ref: str = ""

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SplitQualityFinding:
    kind: str
    severity: str
    message: str


@dataclass
class IntegrationItem:
    id: str
    feature_id: str
    work_units: list[str]
    branches: list[str]
    changed_files: list[str]
    required_checks: list[str]
    conflict_risk: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GoalContract:
    outcome: str
    verification_surface: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)
    iteration_policy: str = ""
    blocked_stop_condition: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkpadProjection:
    task_id: str
    work_unit_id: str
    plan: list[dict[str, Any]] = field(default_factory=list)
    acceptance: list[dict[str, Any]] = field(default_factory=list)
    validation: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[dict[str, Any]] = field(default_factory=list)
    split_quality: list[dict[str, Any]] = field(default_factory=list)
    effective_profile: str = "baseline"
    latest_update_event: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetryMetadata:
    task_id: str
    attempt: int = 0
    reason: str = ""
    worker: str = ""
    workspace_path: str = ""
    dispatch_id: str = ""
    due_at: str = ""
    retry_token: str = ""
    generation: str = ""
    route_event: str = ""
    source_event_id: str = ""
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StallProjection:
    task_id: str
    status: str
    reasons: list[str] = field(default_factory=list)
    freshness: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_runtime_inputs(
    state_dir: Path,
) -> tuple[TaskStore, EventLog, list[ZfEvent]]:
    task_store = TaskStore(state_dir / "kanban.json")
    event_log = EventLog(state_dir / "events.jsonl")
    return task_store, event_log, event_log.read_all()


def success_criteria_from_task(task: Task) -> list[SuccessCriterion]:
    contract = task.contract
    evidence_contract = contract.evidence_contract or {}
    raw: list[Any] = []
    if isinstance(evidence_contract, dict):
        raw.extend(evidence_contract.get("success_criteria") or [])
        for command in _coerce_list(evidence_contract.get("required_commands")):
            raw.append({"kind": "command_passed", "command": command})
        for event_type in _coerce_list(evidence_contract.get("required_events")):
            raw.append({"kind": "event_exists", "event_type": event_type})
        for artifact in _coerce_list(evidence_contract.get("required_artifacts")):
            raw.append({"kind": "artifact_exists", "path": artifact})
    if contract.verification and contract.verification.strip().startswith(("pytest", "npm", "pnpm", "uv ", "python")):
        raw.append({"kind": "command_passed", "command": contract.verification.strip()})
    criteria: list[SuccessCriterion] = []
    for item in raw:
        criterion = SuccessCriterion.from_obj(item)
        if criterion is not None:
            criteria.append(criterion)
    return criteria


def effective_profile_for_task(
    task: Task,
    *,
    config: ZfConfig | None = None,
    boundary: str = "worker_task",
    context_usage_ratio: float | None = None,
) -> str:
    base = "baseline"
    if config is not None:
        base = getattr(config.workflow, "harness_profile", "baseline") or "baseline"
    if boundary in {"release", "ship"}:
        return "release"
    complexity = task_complexity(task)
    if complexity == "release":
        return "release"
    if base == "release":
        return "release"
    if base == "strict":
        return "strict"
    if complexity == "complex":
        return "strict"
    triggers = getattr(getattr(config, "workflow", None), "strict_triggers", None)
    if triggers is None:
        return "baseline"
    if triggers.rework_attempts_gte and task.retry_count >= triggers.rework_attempts_gte:
        return "strict"
    if (
        context_usage_ratio is not None
        and triggers.context_usage_gte
        and context_usage_ratio >= triggers.context_usage_gte
    ):
        return "strict"
    files = _task_file_surface(task)
    for pattern in triggers.file_globs:
        if any(fnmatch.fnmatch(path, pattern) for path in files):
            return "strict"
    return "baseline"


def work_unit_from_task(
    task: Task,
    *,
    config: ZfConfig | None = None,
    boundary: str = "worker_task",
    context_usage_ratio: float | None = None,
) -> WorkUnitContract:
    contract = task.contract
    evidence_contract = contract.evidence_contract or {}
    validation_commands: list[str] = []
    validation_events: list[str] = []
    validation_files: list[str] = []
    expected_artifacts: list[dict[str, Any]] = []
    if isinstance(evidence_contract, dict):
        validation_commands.extend(_coerce_list(evidence_contract.get("required_commands")))
        validation_events.extend(_coerce_list(evidence_contract.get("required_events")))
        validation_files.extend(_coerce_list(evidence_contract.get("required_files")))
        for path in _coerce_list(evidence_contract.get("required_artifacts")):
            expected_artifacts.append({"kind": "artifact", "path": path})
    if contract.verification and contract.verification not in validation_commands:
        validation_commands.append(contract.verification)
    for path in contract.handoff_artifacts:
        expected_artifacts.append({"kind": "artifact", "path": path})
    include = list(dict.fromkeys([
        *contract.scope,
        *contract.affected_files,
        *contract.shared_files,
        *contract.exclusive_files,
    ]))
    complexity = task_complexity(task)
    profile_reasons = complexity_strict_reasons_for_task(
        task,
        config=config,
        context_usage_ratio=context_usage_ratio,
    )
    return WorkUnitContract(
        id=f"WU-{task.id}",
        task_id=task.id,
        feature_id=contract.feature_id,
        title=task.title,
        goal=contract.behavior or task.title,
        outcome=contract.acceptance or contract.behavior or task.title,
        scope_include=include,
        scope_exclude=list(contract.exclusions) + list(contract.explicit_non_goals),
        owner_role=contract.owner_role or _role_from_assignee(task.assigned_to or ""),
        owner_instance=contract.owner_instance or (task.assigned_to or ""),
        depends_on=list(task.blocked_by),
        expected_artifacts=expected_artifacts,
        validation_surface={
            "commands": list(dict.fromkeys(validation_commands)),
            "events": list(dict.fromkeys(validation_events)),
            "files": list(dict.fromkeys(validation_files)),
        },
        acceptance_criteria=list(contract.acceptance_criteria),
        boundary=boundary,
        complexity=complexity,
        effective_profile=effective_profile_for_task(
            task,
            config=config,
            boundary=boundary,
            context_usage_ratio=context_usage_ratio,
        ),
        effective_profile_reason=profile_reasons,
        success_criteria=success_criteria_from_task(task),
    )


def _role_from_assignee(assignee: str) -> str:
    if not assignee:
        return ""
    if "-" in assignee:
        return assignee.split("-", 1)[0]
    return assignee


def evaluate_success_criteria(
    criteria: list[SuccessCriterion],
    *,
    task: Task,
    state_dir: Path,
    events: list[ZfEvent],
    project_root: Path | None = None,
) -> list[CriterionResult]:
    project_root = project_root or state_dir.parent
    results: list[CriterionResult] = []
    for criterion in criteria:
        passed = False
        refs: list[str] = []
        reason = ""
        if criterion.kind == "event_exists":
            target = criterion.event_type or criterion.value
            matches = [event for event in events if event.type == target]
            passed = bool(matches)
            refs = [event.id for event in matches[-3:]]
            reason = f"event {target!r} {'observed' if passed else 'missing'}"
        elif criterion.kind == "command_passed":
            command = criterion.command or criterion.value
            matches = [
                event for event in events
                if event.type in SUCCESS_EVENT_TYPES
                and command
                and command in _event_text(event)
            ]
            passed = bool(matches)
            refs = [event.id for event in matches[-3:]]
            reason = f"command {command!r} {'has passing evidence' if passed else 'has no passing evidence'}"
        elif criterion.kind in {"file_exists", "artifact_exists"}:
            path = criterion.path or criterion.value
            candidates = [_safe_join(project_root, path), _safe_join(state_dir, path)]
            passed = any(candidate.exists() for candidate in candidates if candidate)
            reason = f"path {path!r} {'exists' if passed else 'missing'}"
        elif criterion.kind == "file_contains":
            path = criterion.path
            needle = criterion.contains or criterion.value
            candidates = [_safe_join(project_root, path), _safe_join(state_dir, path)]
            for candidate in candidates:
                if candidate and candidate.exists() and needle in candidate.read_text(encoding="utf-8", errors="ignore"):
                    passed = True
                    break
            reason = f"path {path!r} {'contains' if passed else 'does not contain'} {needle!r}"
        elif criterion.kind == "task_state":
            target_state = criterion.state or criterion.value
            passed = task.status == target_state
            reason = f"task state is {task.status!r}, expected {target_state!r}"
        elif criterion.kind in {"artifact_matrix_gate", "candidate_artifact_matrix_gate"}:
            from zf.runtime.artifact_matrix_gate import evaluate_artifact_matrix_gate

            config = dict(criterion.params)
            if criterion.path and not config.get("config_ref"):
                config["config_ref"] = criterion.path
            result = evaluate_artifact_matrix_gate(project_root, config)
            passed = result.passed
            refs = result.checked_artifacts + result.checked_matrices
            if passed:
                reason = (
                    "artifact matrix gate passed "
                    f"({result.blocking_rows} blocking rows, {result.checked_rows} rows checked)"
                )
            else:
                messages = [finding.message for finding in result.findings[:3]]
                reason = "artifact matrix gate failed: " + "; ".join(messages)
        else:
            reason = f"unsupported criterion kind {criterion.kind!r}"
        results.append(CriterionResult(
            criterion=criterion,
            passed=passed,
            reason=reason,
            evidence_refs=refs,
        ))
    return results


def _safe_join(root: Path, path: str) -> Path | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        if not Path(path).is_absolute():
            return None
    return candidate


def project_why_not_done(
    state_dir: Path,
    task_id: str,
    *,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> WhyNotDoneProjection:
    task_store, _, events = load_runtime_inputs(state_dir)
    task = task_store.get(task_id)
    if task is None:
        return WhyNotDoneProjection(
            task_id=task_id,
            state="missing",
            work_unit=None,
            why_not_done=[WhyNotDoneReason(
                kind="task_missing",
                severity="blocking",
                message=f"task {task_id} not found",
            )],
            recommended_action=RecommendedAction(
                kind="escalate",
                reason="task is missing from active/archive stores",
            ),
        )
    task_events = _events_for_task(events, task.id)
    freshness = project_state_freshness(
        state_dir,
        task_id=task.id,
        actor=task.assigned_to or "",
        events=events,
    )
    context_ratio = freshness.get("context_usage_ratio")
    work_unit = work_unit_from_task(
        task,
        config=config,
        context_usage_ratio=context_ratio if isinstance(context_ratio, float | int) else None,
    )
    reasons: list[WhyNotDoneReason] = []
    if task.status in TERMINAL_STATES:
        return WhyNotDoneProjection(
            task_id=task.id,
            state=task.status,
            work_unit=work_unit,
            freshness=freshness,
        )
    for blocker_id in task.blocked_by:
        blocker = task_store.get(blocker_id)
        if blocker is None or blocker.status not in TERMINAL_STATES:
            reasons.append(WhyNotDoneReason(
                kind="active_blocker",
                severity="blocking",
                message=f"blocker {blocker_id} is not terminal",
                expected=f"{blocker_id} done/cancelled",
                owner_role=work_unit.owner_role,
            ))
    for index, criterion in enumerate(task.contract.acceptance_criteria):
        evidence = task.contract.acceptance_evidence.get(criterion)
        evidence = evidence or task.contract.acceptance_evidence.get(str(index))
        if not evidence:
            reasons.append(WhyNotDoneReason(
                kind="missing_acceptance_evidence",
                severity="blocking",
                message=f"acceptance criterion has no evidence: {criterion}",
                expected=criterion,
                owner_role=work_unit.owner_role,
            ))
    for result in evaluate_success_criteria(
        work_unit.success_criteria,
        task=task,
        state_dir=state_dir,
        events=task_events,
        project_root=project_root,
    ):
        if result.passed:
            continue
        kind = (
            "required_event_missing"
            if result.criterion.kind == "event_exists"
            else "missing_evidence"
        )
        reasons.append(WhyNotDoneReason(
            kind=kind,
            severity="blocking",
            message=result.reason,
            expected=_criterion_expected(result.criterion),
            owner_role=work_unit.owner_role,
            evidence_refs=result.evidence_refs,
        ))
    if not work_unit.success_criteria and not task.contract.acceptance_criteria:
        reasons.append(WhyNotDoneReason(
            kind="missing_validation_surface",
            severity="warning",
            message="task has no success criteria or acceptance criteria",
            expected="success_criteria or acceptance_criteria",
            owner_role=work_unit.owner_role,
        ))
    for event in task_events:
        if event.type == "task.done.blocked":
            payload = event.payload if isinstance(event.payload, dict) else {}
            missing = payload.get("missing")
            reasons.append(WhyNotDoneReason(
                kind="done_blocked",
                severity="blocking",
                message="terminal done gate blocked the task",
                expected=json.dumps(missing, ensure_ascii=False) if missing else "terminal evidence",
                owner_role=work_unit.owner_role,
                evidence_refs=[event.id],
            ))
    action = _recommended_action(task, reasons, task_events, work_unit)
    next_event = _next_required_event(reasons, work_unit)
    return WhyNotDoneProjection(
        task_id=task.id,
        state=task.status,
        work_unit=work_unit,
        why_not_done=reasons,
        recommended_action=action,
        next_required_event=next_event,
        freshness=freshness,
    )


def _criterion_expected(criterion: SuccessCriterion) -> str:
    return (
        criterion.command
        or criterion.event_type
        or criterion.path
        or criterion.state
        or criterion.value
        or criterion.kind
    )


def _recommended_action(
    task: Task,
    reasons: list[WhyNotDoneReason],
    task_events: list[ZfEvent],
    work_unit: WorkUnitContract,
) -> RecommendedAction:
    latest_type = task_events[-1].type if task_events else ""
    if task.status == "blocked" or any(r.kind == "active_blocker" for r in reasons):
        return RecommendedAction(
            kind="escalate",
            role=work_unit.owner_role,
            reason="task is blocked by dependency or external state",
        )
    if latest_type in FAILURE_EVENT_TYPES:
        return RecommendedAction(
            kind="rework",
            role=work_unit.owner_role or "dev",
            reason=f"latest task event is {latest_type}",
        )
    if any(r.kind.startswith("missing") or r.kind == "done_blocked" for r in reasons):
        return RecommendedAction(
            kind="continuation",
            role=work_unit.owner_role or _role_from_assignee(task.assigned_to or "") or "dev",
            reason="task has missing validation or completion evidence",
        )
    if reasons:
        return RecommendedAction(kind="wait", role=work_unit.owner_role, reason="task has open non-blocking findings")
    return RecommendedAction(kind="none", role=work_unit.owner_role, reason="no blocking why-not-done reason")


def _next_required_event(
    reasons: list[WhyNotDoneReason],
    work_unit: WorkUnitContract,
) -> str:
    for reason in reasons:
        if reason.kind == "required_event_missing":
            return reason.expected
    events = work_unit.validation_surface.get("events") or []
    return events[0] if events else ""


def audit_completion(
    state_dir: Path,
    task_id: str,
    *,
    trigger_event: ZfEvent | None = None,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> CompletionAuditResult:
    task_store = TaskStore(state_dir / "kanban.json")
    task = task_store.get(task_id)
    trigger_payload = trigger_event.payload if (
        trigger_event is not None and isinstance(trigger_event.payload, dict)
    ) else {}
    dispatch_id = str(
        trigger_payload.get("dispatch_id")
        or getattr(task, "active_dispatch_id", "")
        or ""
    )
    try:
        attempt = int(
            trigger_payload.get("attempt")
            or getattr(task, "retry_count", 0)
            or 0
        )
    except (TypeError, ValueError):
        attempt = 0
    projection = project_why_not_done(
        state_dir,
        task_id,
        config=config,
        project_root=project_root,
    )
    if projection.work_unit is None:
        return CompletionAuditResult(
            task_id=task_id,
            route="escalate",
            reason="task missing",
            dispatch_id=dispatch_id,
            attempt=attempt,
            trigger_event_type=trigger_event.type if trigger_event is not None else "",
            trigger_event_id=trigger_event.id if trigger_event is not None else "",
        )
    route = "done" if not any(r.severity == "blocking" for r in projection.why_not_done) else projection.recommended_action.kind
    event_type = trigger_event.type if trigger_event is not None else ""
    if event_type in FAILURE_EVENT_TYPES:
        route = "rework"
    elif event_type in {"agent.timeout", "worker.context.critical"}:
        route = "retry"
    elif route in {"none", "wait"}:
        route = "done" if not projection.why_not_done else "continuation"
    boundary = str(
        trigger_payload.get("boundary")
        or trigger_payload.get("completion_boundary")
        or ""
    )
    if route == "done" and boundary in {"feature", "release", "ship", "integration"}:
        route = "integration_queue"
    route = _route_override(config, projection, route)
    reason = projection.recommended_action.reason or f"completion audit routed {route}"
    if event_type == "worker.context.critical":
        context_reason = str(
            trigger_payload.get("reason")
            or trigger_payload.get("action")
            or "context critical"
        )
        reason = f"context critical: {context_reason}; {reason}"
    return CompletionAuditResult(
        task_id=task_id,
        route=route,
        reason=reason,
        missing_evidence=[asdict(item) for item in projection.why_not_done],
        recommended_role=projection.recommended_action.role,
        next_required_event=projection.next_required_event,
        work_unit_id=projection.work_unit.id,
        dispatch_id=dispatch_id,
        attempt=attempt,
        trigger_event_type=event_type,
        trigger_event_id=trigger_event.id if trigger_event is not None else "",
    )


def _route_override(
    config: ZfConfig | None,
    projection: WhyNotDoneProjection,
    route: str,
) -> str:
    routes = getattr(getattr(getattr(config, "workflow", None), "completion_audit", None), "routes", {})
    if not isinstance(routes, dict):
        return route
    for reason in projection.why_not_done:
        if reason.kind in routes:
            return routes[reason.kind]
    return routes.get(route, route)


def apply_completion_audit(
    *,
    state_dir: Path,
    task_id: str,
    event_writer: EventWriter,
    trigger_event: ZfEvent | None = None,
    task_store: TaskStore | None = None,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    mutate: bool = False,
) -> CompletionAuditResult:
    result = audit_completion(
        state_dir,
        task_id,
        trigger_event=trigger_event,
        config=config,
        project_root=project_root,
    )
    causation_id = trigger_event.id if trigger_event is not None else None
    correlation_id = trigger_event.correlation_id if trigger_event is not None else None
    event_writer.append(ZfEvent(
        type="completion_audit.started",
        actor="zf-cli",
        task_id=task_id,
        payload={
            "work_unit_id": result.work_unit_id,
            "trigger_event": trigger_event.type if trigger_event else "",
        },
        causation_id=causation_id,
        correlation_id=correlation_id,
    ))
    if trigger_event is not None and trigger_event.type == "worker.context.critical":
        trigger_payload = (
            trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
        )
        result.previous_snapshot_ref = str(trigger_payload.get("snapshot_ref") or "")
        try:
            packet = build_resume_packet(
                state_dir,
                task_id,
                dispatch_id=result.dispatch_id or "context-critical",
                config=config,
                project_root=project_root,
            )
            path = write_resume_packet(
                state_dir,
                packet,
                dispatch_id=result.dispatch_id or "context-critical",
            )
            result.resume_packet_path = str(path)
            result.resume_packet_missing_evidence_count = len(
                packet.get("missing_evidence") or []
            )
            try:
                from types import SimpleNamespace

                from zf.runtime.runtime_snapshot import (
                    RuntimeSnapshotInput,
                    build_runtime_snapshot,
                    runtime_snapshot_event_payload,
                    write_runtime_snapshot,
                )

                task_store = task_store or TaskStore(state_dir / "kanban.json")
                task = task_store.get(task_id)
                role = SimpleNamespace(
                    name=str(trigger_payload.get("role") or ""),
                    instance_id=str(trigger_payload.get("instance_id") or ""),
                    role_kind="auto",
                    backend=str(trigger_payload.get("backend") or ""),
                    publishes=[],
                )
                dispatch_id = result.dispatch_id or str(
                    trigger_payload.get("dispatch_id") or "context-critical"
                )
                per_dispatch_dir = state_dir / "briefings" / task_id / dispatch_id
                project_id = ""
                try:
                    project_id = str(getattr(config.project, "name", "") or "")
                except Exception:
                    project_id = ""
                snapshot = build_runtime_snapshot(RuntimeSnapshotInput(
                    state_dir=state_dir,
                    project_root=project_root or state_dir.parent,
                    project_id=project_id,
                    source="context_recovery",
                    task=task,
                    role=role,
                    dispatch_id=dispatch_id,
                    run_id=dispatch_id,
                    refs={
                        "previous_snapshot_ref": result.previous_snapshot_ref,
                        "resume_packet_ref": path,
                        "state_packet_ref": per_dispatch_dir / "state-packet.json",
                        "context_manifest_ref": per_dispatch_dir / "context.jsonl",
                    },
                ))
                snapshot_result = write_runtime_snapshot(
                    snapshot,
                    state_dir=state_dir,
                    project_root=project_root or state_dir.parent,
                )
                result.recovery_snapshot_ref = snapshot_result.snapshot_ref
                event_writer.append(ZfEvent(
                    type="runtime.snapshot.recorded",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        **runtime_snapshot_event_payload(snapshot_result),
                        "previous_snapshot_ref": result.previous_snapshot_ref,
                    },
                    causation_id=trigger_event.id,
                    correlation_id=trigger_event.correlation_id,
                ))
            except Exception as snapshot_exc:
                event_writer.append(ZfEvent(
                    type="runtime.snapshot.invalid",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "source": "context_recovery",
                        "reason": str(snapshot_exc),
                        "task_id": task_id,
                        "dispatch_id": result.dispatch_id,
                        "previous_snapshot_ref": result.previous_snapshot_ref,
                    },
                    causation_id=trigger_event.id,
                    correlation_id=trigger_event.correlation_id,
                ))
        except Exception:
            # Audit routing should remain visible even if packet materialization
            # fails; the empty path is an explicit missing-evidence signal.
            pass
    routed = event_writer.append(ZfEvent(
        type="completion_audit.routed",
        actor="zf-cli",
        task_id=task_id,
        payload=result.to_payload(),
        causation_id=causation_id,
        correlation_id=correlation_id,
    ))
    event_map = {
        "done": "task.done.accepted",
        "continuation": "task.continuation_scheduled",
        "retry": "task.retry_scheduled",
        "rework": "task.rework.requested",
        "reset": "task.reset_requested",
        "escalate": "task.escalated",
        "integration_queue": "task.integration_enqueued",
    }
    event_writer.append(ZfEvent(
        type=event_map.get(result.route, "task.done.blocked"),
        actor="zf-cli",
        task_id=task_id,
        payload={
            **result.to_payload(),
            "completion_audit_event_id": routed.id,
        },
        causation_id=routed.id,
        correlation_id=correlation_id,
    ))
    if mutate:
        task_store = task_store or TaskStore(state_dir / "kanban.json")
        if result.route == "done":
            task_store.update(task_id, status="done")
        elif result.route in {"continuation", "retry", "rework", "reset"}:
            task_store.update(task_id, status="in_progress")
        elif result.route == "escalate":
            task_store.update(task_id, status="blocked")
    return result


def build_resume_packet(
    state_dir: Path,
    task_id: str,
    *,
    dispatch_id: str = "",
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    task_store, _, events = load_runtime_inputs(state_dir)
    task = task_store.get(task_id)
    projection = project_why_not_done(
        state_dir,
        task_id,
        config=config,
        project_root=project_root,
    )
    project_root = project_root or state_dir.parent
    changed_files = _changed_files(task, _events_for_task(events, task_id)) if task else []
    completed = [
        {"event_id": event.id, "type": event.type, "summary": _event_text(event)[:240]}
        for event in _events_for_task(events, task_id)
        if event.type in SUCCESS_EVENT_TYPES
    ]
    packet = {
        "schema_version": "resume-packet.v1",
        "generated_at": _now_iso(),
        "task_id": task_id,
        "work_unit_id": projection.work_unit.id if projection.work_unit else "",
        "dispatch_id": dispatch_id,
        "objective": projection.work_unit.goal if projection.work_unit else "",
        "current_state": projection.state,
        "outcome": projection.work_unit.outcome if projection.work_unit else "",
        "completed_evidence": completed,
        "missing_evidence": [asdict(item) for item in projection.why_not_done],
        "changed_files": changed_files,
        "git_state": _git_state(project_root),
        "blockers": list(getattr(task, "blocked_by", []) or []) if task else [],
        "next_required_event": projection.next_required_event,
        "next_required_action": projection.recommended_action.reason,
        "do_not_repeat": [
            f"不要重复已完成事件 {item['type']} ({item['event_id']})"
            for item in completed[-5:]
        ],
        "allowed_scope": projection.work_unit.scope_include if projection.work_unit else [],
        "source_event_ids": [item["event_id"] for item in completed[-10:]],
    }
    required_refs = _resume_required_contract_refs(task, config)
    artifact_recovery = build_artifact_recovery_refs(
        state_dir,
        task,
        project_root=project_root,
        required_contract_refs=required_refs,
    )
    packet.update({
        "artifact_recovery": artifact_recovery,
        "accepted_artifact_refs": artifact_recovery.get("accepted_artifact_refs", []),
        "stale_artifact_refs": artifact_recovery.get("stale_artifact_refs", []),
        "artifact_hash_status": artifact_recovery.get(
            "accepted_hash_status",
            artifact_recovery.get("hash_status", []),
        ),
        "missing_artifact_refs": artifact_recovery.get("missing_required_refs", []),
        "sufficiency_requirements": {
            "required_fields": [
                "task_id",
                "current_state",
                "next_required_action",
            ],
            "required_contract_refs": required_refs,
            "optional_contract_refs": [
                field for field in (
                    "spec_ref",
                    "plan_ref",
                    "tdd_ref",
                    "critic_gate_ref",
                    "critic_event_id",
                    "evidence_contract",
                )
                if field not in required_refs
            ],
        },
    })
    return packet


def _resume_required_contract_refs(
    task: Task | None,
    config: ZfConfig | None,
) -> list[str]:
    if task is None or config is None:
        return []
    dag = getattr(getattr(config, "workflow", None), "dag", None)
    if dag is None or not getattr(dag, "dev_requires_orchestrator_backlog", False):
        return []
    required = [
        str(item).strip()
        for item in (getattr(dag, "required_backlog_refs", []) or [])
        if str(item).strip()
    ]
    if not required or not _resume_task_targets_writer_role(task, config):
        return []
    return list(dict.fromkeys(required))


def _resume_task_targets_writer_role(task: Task, config: ZfConfig) -> bool:
    target = (
        getattr(task.contract, "owner_role", "")
        or _resume_role_name_from_instance(
            getattr(task.contract, "owner_instance", ""),
            config,
        )
        or _resume_role_name_from_instance(task.assigned_to or "", config)
        or (task.assigned_to or "")
    )
    if not target:
        return False
    for role in config.roles:
        if role.name == target:
            return getattr(role, "role_kind", "") == "writer"
    return False


def _resume_role_name_from_instance(value: str, config: ZfConfig) -> str:
    if not value:
        return ""
    for role in config.roles:
        if role.instance_id == value:
            return role.name
    return ""


def write_resume_packet(
    state_dir: Path,
    packet: dict[str, Any],
    *,
    dispatch_id: str = "",
) -> Path:
    task_id = str(packet.get("task_id") or "unknown")
    dispatch = dispatch_id or str(packet.get("dispatch_id") or "latest")
    root = state_dir / "briefings" / task_id / dispatch
    root.mkdir(parents=True, exist_ok=True)
    path = root / "resume-packet.json"
    atomic_write_text(path, json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
    latest_dir = state_dir / "resume_packets"
    latest_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        latest_dir / f"{task_id}.json",
        json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
    )
    return path


def _changed_files(task: Task | None, events: list[ZfEvent]) -> list[str]:
    files: list[str] = []
    if task and task.evidence:
        files.extend(task.evidence.files_touched)
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key in ("files", "files_touched", "changed_files", "affected_files"):
            files.extend(_coerce_list(payload.get(key)))
    return list(dict.fromkeys(files))


def _git_state(project_root: Path) -> dict[str, Any]:
    if not (project_root / ".git").exists():
        return {"branch": "", "head": "", "dirty": False}
    def run(args: list[str]) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    status = run(["status", "--short"])
    return {
        "branch": run(["branch", "--show-current"]),
        "head": run(["rev-parse", "--verify", "HEAD"]),
        "dirty": bool(status),
    }


def project_state_freshness(
    state_dir: Path,
    *,
    task_id: str = "",
    actor: str = "",
    events: list[ZfEvent] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    events = events if events is not None else EventLog(state_dir / "events.jsonl").read_all()
    now = now or datetime.now(timezone.utc)
    relevant = [
        event for event in events
        if (task_id and _event_task_match(event, task_id)) or (actor and event.actor == actor)
    ]
    last_event = relevant[-1] if relevant else None
    heartbeat = next((event for event in reversed(events) if event.type == "worker.heartbeat" and (not actor or event.actor == actor)), None)
    last_progress = next((event for event in reversed(relevant) if event.type in {"worker.progress", "phase.progressed"}), None)
    last_file = next((event for event in reversed(relevant) if event.type in {"task.files_touched", "task.ref.updated", "dev.build.done"}), None)
    last_test = next((event for event in reversed(relevant) if event.type in {"test.passed", "test.failed", "static_gate.passed", "static_gate.failed"}), None)
    last_evidence = next((event for event in reversed(relevant) if event.type in {"task.done.evidence", "task.evidence_linked", "task.artifact_refs.updated"}), None)
    context_ratio = None
    for event in reversed(events):
        if actor and event.actor not in {actor, None}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key in ("context_usage_ratio", "usage_ratio", "context_ratio", "ratio"):
            value = payload.get(key)
            if isinstance(value, int | float):
                context_ratio = float(value)
                break
        if context_ratio is not None:
            break
    return {
        "last_event_at": last_event.ts if last_event else "",
        "last_event_age_sec": _age_seconds(last_event.ts, now=now) if last_event else None,
        "last_heartbeat_at": heartbeat.ts if heartbeat else "",
        "last_heartbeat_age_sec": _age_seconds(heartbeat.ts, now=now) if heartbeat else None,
        "last_progress_at": last_progress.ts if last_progress else "",
        "last_progress_age_sec": _age_seconds(last_progress.ts, now=now) if last_progress else None,
        "last_file_change_at": last_file.ts if last_file else "",
        "last_file_change_age_sec": _age_seconds(last_file.ts, now=now) if last_file else None,
        "last_test_at": last_test.ts if last_test else "",
        "last_test_age_sec": _age_seconds(last_test.ts, now=now) if last_test else None,
        "last_evidence_at": last_evidence.ts if last_evidence else "",
        "last_evidence_age_sec": _age_seconds(last_evidence.ts, now=now) if last_evidence else None,
        "context_usage_ratio": context_ratio,
        "idle_age_sec": _age_seconds(last_event.ts, now=now) if last_event else None,
    }


def project_stall_status(
    state_dir: Path,
    task_id: str,
    *,
    actor: str = "",
    events: list[ZfEvent] | None = None,
    idle_threshold_sec: float = 1800.0,
    heartbeat_threshold_sec: float = 300.0,
    context_warning_ratio: float = 0.80,
) -> StallProjection:
    freshness = project_state_freshness(
        state_dir,
        task_id=task_id,
        actor=actor,
        events=events,
    )
    reasons: list[str] = []
    status = "fresh"
    heartbeat_age = freshness.get("last_heartbeat_age_sec")
    idle_age = freshness.get("idle_age_sec")
    context_ratio = freshness.get("context_usage_ratio")
    if isinstance(context_ratio, int | float) and context_ratio >= context_warning_ratio:
        status = "context_warn"
        reasons.append(f"context usage {context_ratio:.2f} >= {context_warning_ratio:.2f}")
    if isinstance(heartbeat_age, int | float) and heartbeat_age > heartbeat_threshold_sec:
        status = "stalled"
        reasons.append(f"heartbeat stale for {heartbeat_age:.0f}s")
    if isinstance(idle_age, int | float) and idle_age > idle_threshold_sec:
        status = "stalled" if status != "context_warn" else status
        reasons.append(f"no task event for {idle_age:.0f}s")
    if not freshness.get("last_event_at"):
        status = "unknown"
        reasons.append("no event observed for task or actor")
    return StallProjection(
        task_id=task_id,
        status=status,
        reasons=reasons,
        freshness=freshness,
    )


def check_split_quality(
    work_unit: WorkUnitContract,
    *,
    mode: str = "warning",
    max_scope_files: int = 12,
    require_validation_surface: bool = True,
) -> list[SplitQualityFinding]:
    blocking = "blocking" if mode == "blocking" else "warning"
    findings: list[SplitQualityFinding] = []
    if not work_unit.outcome:
        findings.append(SplitQualityFinding("missing_outcome", "blocking", "work unit outcome is required"))
    if not work_unit.acceptance_criteria:
        findings.append(SplitQualityFinding("missing_acceptance", blocking, "acceptance criteria are required"))
    if require_validation_surface and not any(work_unit.validation_surface.values()):
        findings.append(SplitQualityFinding("missing_validation_surface", blocking, "validation surface is required"))
    if max_scope_files and len(work_unit.scope_include) > max_scope_files:
        findings.append(SplitQualityFinding("scope_too_large", blocking, f"scope has {len(work_unit.scope_include)} files, max is {max_scope_files}"))
    if work_unit.id in work_unit.depends_on:
        findings.append(SplitQualityFinding("self_dependency", "blocking", "work unit depends on itself"))
    return findings


def project_workpad(
    state_dir: Path,
    task_id: str,
    *,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> WorkpadProjection:
    task_store, _, events = load_runtime_inputs(state_dir)
    task = task_store.get(task_id)
    projection = project_why_not_done(
        state_dir,
        task_id,
        config=config,
        project_root=project_root,
    )
    work_unit = projection.work_unit
    if task is None or work_unit is None:
        return WorkpadProjection(
            task_id=task_id,
            work_unit_id="",
            blockers=[asdict(item) for item in projection.why_not_done],
        )
    split_cfg = getattr(
        getattr(getattr(config, "workflow", None), "work_units", None),
        "split_quality",
        None,
    )
    split_mode = getattr(split_cfg, "mode", "warning")
    max_scope = int(getattr(split_cfg, "max_scope_files", 12) or 0)
    require_validation = bool(getattr(split_cfg, "require_validation_surface", True))
    split_findings = check_split_quality(
        work_unit,
        mode=split_mode,
        max_scope_files=max_scope,
        require_validation_surface=require_validation,
    )
    criteria_results = evaluate_success_criteria(
        work_unit.success_criteria,
        task=task,
        state_dir=state_dir,
        events=_events_for_task(events, task.id),
        project_root=project_root,
    )
    plan = [
        {
            "item": "outcome",
            "status": "done" if work_unit.outcome else "missing",
            "value": work_unit.outcome,
        },
        {
            "item": "scope",
            "status": "done" if work_unit.scope_include else "missing",
            "value": work_unit.scope_include,
        },
        {
            "item": "owner",
            "status": "done" if work_unit.owner_role else "missing",
            "value": work_unit.owner_role,
        },
    ]
    acceptance = []
    for index, criterion in enumerate(work_unit.acceptance_criteria):
        evidence = (
            task.contract.acceptance_evidence.get(criterion)
            or task.contract.acceptance_evidence.get(str(index))
            or []
        )
        acceptance.append({
            "criterion": criterion,
            "status": "done" if evidence else "missing",
            "evidence_refs": list(evidence),
        })
    validation = [
        {
            "kind": result.criterion.kind,
            "expected": _criterion_expected(result.criterion),
            "status": "done" if result.passed else "missing",
            "reason": result.reason,
            "evidence_refs": result.evidence_refs,
        }
        for result in criteria_results
    ]
    relevant_events = _events_for_task(events, task.id)
    latest = _event_projection(relevant_events[-1]) if relevant_events else {}
    return WorkpadProjection(
        task_id=task.id,
        work_unit_id=work_unit.id,
        plan=plan,
        acceptance=acceptance,
        validation=validation,
        blockers=[asdict(item) for item in projection.why_not_done],
        split_quality=[asdict(item) for item in split_findings],
        effective_profile=work_unit.effective_profile,
        latest_update_event=latest,
    )


def _event_projection(event: ZfEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "ts": event.ts,
        "actor": event.actor,
        "task_id": event.task_id,
        "payload": event.payload if isinstance(event.payload, dict) else {},
    }


def build_integration_item(
    state_dir: Path,
    feature_id: str,
    *,
    project_root: Path | None = None,
) -> IntegrationItem:
    task_store, _, events = load_runtime_inputs(state_dir)
    tasks = [
        task for task in task_store.list_all_with_archive()
        if task.contract.feature_id == feature_id
    ]
    work_units = [f"WU-{task.id}" for task in tasks]
    changed: list[str] = []
    checks: list[str] = []
    branches: list[str] = []
    for task in tasks:
        task_events = _events_for_task(events, task.id)
        changed.extend(_changed_files(task, task_events))
        changed.extend(task.contract.affected_files)
        changed.extend(task.contract.scope)
        changed.extend(task.contract.shared_files)
        changed.extend(task.contract.exclusive_files)
        wu = work_unit_from_task(task)
        checks.extend(wu.validation_surface.get("commands", []))
        for event in task_events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            branches.extend(_coerce_list(payload.get("branch") or payload.get("task_ref") or payload.get("source_branch")))
    duplicates = sorted({path for path in changed if changed.count(path) > 1})
    level = "high" if duplicates else "low"
    return IntegrationItem(
        id=f"INT-{feature_id or 'default'}",
        feature_id=feature_id,
        work_units=work_units,
        branches=list(dict.fromkeys(branches)),
        changed_files=list(dict.fromkeys(changed)),
        required_checks=list(dict.fromkeys(checks)),
        conflict_risk={
            "level": level,
            "reasons": [f"multiple work units modified {path}" for path in duplicates],
        },
    )


def audit_runtime_identity(
    state_dir: Path,
    *,
    instance_id: str,
    expected_worktree: str = "",
    expected_task_id: str = "",
) -> dict[str, Any]:
    workdir = state_dir / "workdirs" / instance_id / "project"
    ok = True
    findings: list[dict[str, str]] = []
    if expected_worktree:
        if Path(expected_worktree).resolve() != workdir.resolve():
            ok = False
            findings.append({
                "kind": "worktree_mismatch",
                "expected": expected_worktree,
                "observed": str(workdir),
            })
    if not workdir.exists():
        ok = False
        findings.append({
            "kind": "worktree_missing",
            "expected": str(workdir),
            "observed": "",
        })
    return {
        "ok": ok,
        "instance_id": instance_id,
        "expected_task_id": expected_task_id,
        "worktree": str(workdir),
        "findings": findings,
    }


def project_retry_metadata(
    state_dir: Path,
    task_id: str,
    *,
    events: list[ZfEvent] | None = None,
) -> RetryMetadata:
    task_store = TaskStore(state_dir / "kanban.json")
    task = task_store.get(task_id)
    events = events if events is not None else EventLog(state_dir / "events.jsonl").read_all()
    relevant = _events_for_task(events, task_id)
    retry_events = [
        event for event in relevant
        if event.type in {
            "task.retry_scheduled",
            "task.continuation_scheduled",
            "task.rework.requested",
            "worker.respawn.requested",
            "worker.respawn.started",
        }
    ]
    latest = retry_events[-1] if retry_events else None
    payload = latest.payload if latest is not None and isinstance(latest.payload, dict) else {}
    worker = str(
        payload.get("worker")
        or payload.get("assignee")
        or payload.get("role")
        or getattr(task, "assigned_to", "")
        or ""
    )
    dispatch_id = str(
        payload.get("dispatch_id")
        or getattr(task, "active_dispatch_id", "")
        or ""
    )
    try:
        attempt = int(payload.get("attempt") or getattr(task, "retry_count", 0) or 0)
    except (TypeError, ValueError):
        attempt = 0
    workspace_path = str(
        payload.get("workspace_path")
        or payload.get("worktree")
        or payload.get("cwd")
        or ""
    )
    if not workspace_path and worker:
        workspace_path = str(state_dir / "workdirs" / worker / "project")
    expected_dispatch = getattr(task, "active_dispatch_id", "") if task else ""
    stale = bool(dispatch_id and expected_dispatch and dispatch_id != expected_dispatch)
    return RetryMetadata(
        task_id=task_id,
        attempt=attempt,
        reason=str(payload.get("reason") or payload.get("route") or ""),
        worker=worker,
        workspace_path=workspace_path,
        dispatch_id=dispatch_id,
        due_at=str(payload.get("due_at") or ""),
        retry_token=str(payload.get("retry_token") or payload.get("token") or ""),
        generation=str(payload.get("generation") or payload.get("retry_generation") or ""),
        route_event=latest.type if latest else "",
        source_event_id=latest.id if latest else "",
        stale=stale,
    )


def guard_retry_token(
    state_dir: Path,
    task_id: str,
    *,
    retry_token: str,
    generation: str = "",
    event_writer: EventWriter | None = None,
) -> dict[str, Any]:
    metadata = project_retry_metadata(state_dir, task_id).to_dict()
    expected_token = str(metadata.get("retry_token") or "")
    expected_generation = str(metadata.get("generation") or "")
    ok = bool(retry_token) and retry_token == expected_token
    if generation and expected_generation:
        ok = ok and generation == expected_generation
    result = {
        "ok": ok,
        "task_id": task_id,
        "expected_retry_token": expected_token,
        "actual_retry_token": retry_token,
        "expected_generation": expected_generation,
        "actual_generation": generation,
    }
    if not ok and event_writer is not None:
        event_writer.append(ZfEvent(
            type="task.retry.stale_ignored",
            actor="zf-cli",
            task_id=task_id,
            payload=result,
        ))
    return result


def goal_contract_from_feature(
    state_dir: Path,
    feature_id: str,
) -> GoalContract:
    try:
        from zf.core.feature.store import FeatureStore

        feature = FeatureStore(state_dir / "feature_list.json").get(feature_id)
    except Exception:
        feature = None
    if feature is None:
        return GoalContract(outcome=feature_id)
    verification_surface: list[str] = []
    constraints: list[str] = []
    boundaries: list[str] = []
    for raw_line in (feature.description or feature.user_message or "").splitlines():
        line = raw_line.strip("-* \t")
        lower = line.lower()
        if lower.startswith(("verify:", "verification:", "测试:", "验证:")):
            verification_surface.append(line.split(":", 1)[-1].strip())
        elif lower.startswith(("constraint:", "constraints:", "约束:")):
            constraints.append(line.split(":", 1)[-1].strip())
        elif lower.startswith(("boundary:", "boundaries:", "边界:")):
            boundaries.append(line.split(":", 1)[-1].strip())
    return GoalContract(
        outcome=feature.description or feature.user_message or feature.title,
        verification_surface=verification_surface,
        constraints=constraints,
        boundaries=boundaries,
        iteration_policy="continue until evidence complete",
        blocked_stop_condition="external blocker or deterministic state mismatch",
    )


def map_goal_to_work_units(
    state_dir: Path,
    feature_id: str,
    *,
    config: ZfConfig | None = None,
) -> dict[str, Any]:
    task_store = TaskStore(state_dir / "kanban.json")
    tasks = [
        task for task in task_store.list_all_with_archive()
        if task.contract.feature_id == feature_id
    ]
    return {
        "feature_id": feature_id,
        "goal": goal_contract_from_feature(state_dir, feature_id).to_dict(),
        "work_units": [
            work_unit_from_task(task, config=config).to_dict()
            for task in tasks
        ],
    }


def decision_trace_for_task(
    state_dir: Path,
    task_id: str,
) -> dict[str, Any]:
    events = EventLog(state_dir / "events.jsonl").read_all()
    relevant = _events_for_task(events, task_id)
    decision_types = {
        "completion_audit.started",
        "completion_audit.routed",
        "task.continuation_scheduled",
        "task.retry_scheduled",
        "task.rework.requested",
        "task.reset_requested",
        "task.escalated",
        "task.integration_enqueued",
        "task.done.accepted",
        "task.done.blocked",
    }
    return {
        "task_id": task_id,
        "decisions": [
            _event_projection(event)
            for event in relevant
            if event.type in decision_types
        ],
    }


def project_skill_set(
    state_dir: Path,
    task_id: str,
    *,
    config: ZfConfig | None = None,
) -> dict[str, Any]:
    task = TaskStore(state_dir / "kanban.json").get(task_id)
    if task is None:
        return {"task_id": task_id, "role": "", "skills": [], "load_reasons": []}
    role_name = _role_from_assignee(task.assigned_to or task.contract.owner_role)
    skills: list[str] = list(task.skills_required)
    reasons: list[dict[str, str]] = [
        {"skill": skill, "reason": "task.skills_required"}
        for skill in skills
    ]
    if config is not None:
        for role in config.roles:
            if role.name == role_name or role.instance_id == task.assigned_to:
                for skill in role.skills:
                    if skill not in skills:
                        skills.append(skill)
                    reasons.append({
                        "skill": skill,
                        "reason": f"role {role.instance_id} config",
                    })
                break
    profile = effective_profile_for_task(task, config=config)
    if profile in {"strict", "release"}:
        reasons.append({
            "skill": "strict-harness",
            "reason": f"effective profile {profile}",
        })
    return {
        "task_id": task.id,
        "role": role_name,
        "effective_profile": profile,
        "skills": skills,
        "load_reasons": reasons,
    }


def harness_strength_score(
    *,
    why_not_done: WhyNotDoneProjection,
    completion_audit: CompletionAuditResult | None = None,
    integration_item: IntegrationItem | None = None,
) -> dict[str, Any]:
    score = 100
    blocking = [item for item in why_not_done.why_not_done if item.severity == "blocking"]
    score -= min(40, len(blocking) * 10)
    if completion_audit and completion_audit.route not in {"done", "integration_queue"}:
        score -= 15
    if integration_item and integration_item.conflict_risk.get("level") == "high":
        score -= 15
    score = max(0, score)
    return {
        "score": score,
        "blocking_reasons": len(blocking),
        "completion_route": completion_audit.route if completion_audit else "",
        "integration_conflict": integration_item.conflict_risk if integration_item else {},
    }
