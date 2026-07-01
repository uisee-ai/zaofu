"""Task schema — canonical task contract."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


VALID_VERIFICATION_TIERS = {
    "static",
    "runtime",
    "e2e",
    "manual_evidence",
}


def _new_task_id() -> str:
    return f"TASK-{uuid.uuid4().hex[:6].upper()}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskContract:
    schema_version: str = ""
    locale: str = ""
    feature_id: str = ""
    parent_task_id: str = ""
    campaign: str = ""
    phase: str = ""
    source_backlog_task_id: str = ""
    behavior: str = ""
    verification: str = ""
    verification_tiers: list[str] = field(default_factory=list)
    validation: dict = field(default_factory=dict)
    spec_ref: str = ""
    plan_ref: str = ""
    tdd_ref: str = ""
    critic_gate_ref: str = ""
    critic_event_id: str = ""
    critic_dispatch_id: str = ""
    reviewed_arch_event_id: str = ""
    source_arch_dispatch_id: str = ""
    dispatch_id: str = ""
    dispatch_id_requirement: str = ""
    canonical_case_id: str = ""
    case_alias: str = ""
    canonical_behavior_test: str = ""
    package_namespace: str = ""
    scope: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    explicit_non_goals: list[str] = field(default_factory=list)
    acceptance: str = "exit_code=0"
    evidence_contract: dict = field(default_factory=dict)
    # Deterministic long-horizon profile hint. Empty string means infer
    # from task shape; valid explicit values are simple / standard /
    # complex / release.
    complexity: str = ""
    review_route: dict = field(default_factory=dict)
    owner_role: str = ""
    owner_instance: str = ""
    wave: int = 0
    shared_files: list[str] = field(default_factory=list)
    exclusive_files: list[str] = field(default_factory=list)
    handoff_artifacts: list[str] = field(default_factory=list)
    # Doc 71 Task Capsule canonical refs. These are revision identifiers for
    # kernel-managed task context, not file hashes and not a second task schema.
    task_doc_ref: str = ""
    source_doc_ref: str = ""
    progress_doc_ref: str = ""
    evidence_doc_ref: str = ""
    source_revision: str = ""
    contract_revision: str = ""
    capsule_revision: str = ""
    source_key: str = ""
    source_ref: str = ""
    source_task_id: str = ""
    source_index_ref: str = ""
    source_mode: str = ""
    source_title: str = ""
    source_excerpt: str = ""
    product_contract_ref: str = ""
    spec_skip_reason: str = ""
    unknowns: list[str] = field(default_factory=list)
    review_profile: str = ""
    # P1-1 (2026-04-20): per-task override for rework routing. When a
    # failure event (review.rejected / test.failed / verify.failed /
    # judge.failed / gate.failed) triggers rework, the orchestrator sends the retry to
    # this role.name if set. Empty string falls back to
    # WorkflowConfig.rework_routing[event.type], then to "dev".
    #
    # Example: arch-driven design rework — contract.rework_to = "arch"
    # so a critic's rejection bounces back to the architect, not dev.
    rework_to: str = ""
    # α-1 (2026-05-17): operator escape hatch for fanout independence
    # check. When True, _check_fanout_independence skips the file-overlap
    # gate for this task — operator-acknowledged conflict OK. Use only
    # when operator explicitly wants to fanout despite shared/exclusive
    # file overlap. Default False (safe).
    fanout_force: bool = False
    # β-4 (2026-05-17): fix-task linkage. When a local-CRITICAL
    # multi-task failure surfaces, instead of requeuing the original task
    # (覆盖式重做), zaofu spawns a NEW task with fix_of=<origin_id>. The
    # original task stays "done" — failure didn't discard completed work.
    # Empty string for non-fix tasks.
    fix_of: str = ""
    # EVAL-ACCEPTANCE-CRITERIA-001 (doc 43 §2.5): per-criterion
    # evidence mapping.
    #
    # ``acceptance_criteria`` is a list of human-readable criterion
    # statements the worker MUST satisfy. Distinct from
    # ``verification_tiers`` (which is stage-level: review/test/judge).
    #
    # ``acceptance_evidence`` maps each criterion (by its string text
    # or by integer-as-string index "0" / "1" / …) to a list of
    # event_ids that prove the criterion is satisfied. Reviewers /
    # judges write these via completion event payload
    # ``acceptance_evidence_update`` field; Layer 1 merges them in.
    acceptance_criteria: list[str] = field(default_factory=list)
    acceptance_evidence: dict[str, list[str]] = field(default_factory=dict)
    # #E fix (TR-STATIC-GATE-PER-TASK-OVERRIDE-001, cangjie 2026-05-21
    # observation-E): per-task quality_gates override. yaml-level
    # quality_gates.<gate>.{enabled, required_checks} is global default;
    # task contract can override on per-task basis, so doc-type tasks
    # (scope: docs/**) can `{static: {enabled: false}}` to skip pnpm
    # install while code-type tasks (scope: src/**) run normal yaml
    # checks. Shape:
    #   {"static": {"enabled": False, "required_checks": [...]},
    #    "test":   {"enabled": False}}
    # Empty dict = inherit yaml default verbatim (current behavior).
    quality_gates_override: dict = field(default_factory=dict)


@dataclass
class TaskEvidence:
    commit: str = ""
    output_summary: str = ""
    verified_at: str = ""
    commits: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)


@dataclass
class Task:
    title: str = ""
    id: str = field(default_factory=_new_task_id)
    key: str = ""
    status: str = "backlog"
    priority: int = 3
    assigned_to: str | None = None
    skills_required: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    contract: TaskContract = field(default_factory=TaskContract)
    evidence: TaskEvidence | None = None
    created_at: str = field(default_factory=_now_iso)
    dispatched_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    cancelled_at: str | None = None
    active_dispatch_id: str = ""
    # LH-0.T1: rework cap — incremented on review.rejected / test.failed /
    # verify.failed / judge.failed by _apply_housekeeping; compared to
    # RoleConfig.max_rework_attempts by _dispatch_ready / _dispatch_rework.
    # When retry_count > max, dispatch is refused and task.rework.capped
    # + human.escalate fire instead.
    retry_count: int = 0
    # LH-3.T4: populated when review.suspended / test.suspended routes
    # the task to status=blocked. Surfaced in `zf kanban show` + the
    # Layer 2 briefing so the human/LLM knows why the task stalled.
    blocked_reason: str = ""
