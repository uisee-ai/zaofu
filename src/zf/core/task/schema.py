"""Task schema — canonical task contract."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any


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
    # Source-authored conditions (including approval boundaries), not permission
    # grants. Keep them in the canonical contract through Workflow preparation.
    constraints: list[str] = field(default_factory=list)
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
    # Provider-neutral Task-local execution strategy. Empty fields preserve
    # legacy behavior and resolve through the assigned role's direct-v1
    # default. Provider/session identity remains Attempt/Operation state.
    execution_profile_id: str = ""
    execution_profile_digest: str = ""
    # Task Pipeline v4 integration admission is planner-owned semantic input.
    # The Kernel validates these facts against the frozen controller profile;
    # it never infers risk from task prose or code.
    risk_class: str = ""
    integration_admission_profile: str = ""
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
    # v1 accepted strings only. Structured entries keep the same canonical
    # field while adding stable acceptance id / verifier owner / tier metadata;
    # consumers must normalize through task_contract_snapshot.criterion_text.
    acceptance_criteria: list[Any] = field(default_factory=list)
    acceptance_evidence: dict[str, list[str]] = field(default_factory=dict)
    # Claim-centered delivery relation (doc 149). Planner-owned semantics;
    # the kernel only preserves and validates the referenced identities.
    goal_claim_ids: list[str] = field(default_factory=list)
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


def task_contract_identity_payload(contract: TaskContract) -> dict[str, Any]:
    """Preserve legacy identities when additive source conditions are absent."""
    payload = asdict(contract)
    if not payload["constraints"]:
        payload.pop("constraints")
    return payload


def task_contract_from_mapping(value: object) -> TaskContract:
    """Materialize every known TaskContract field without schema drift."""
    if not isinstance(value, dict):
        return TaskContract()

    defaults = TaskContract()
    normalized: dict[str, Any] = {}
    for contract_field in fields(TaskContract):
        name = contract_field.name
        if name not in value:
            continue
        raw = value[name]
        default = getattr(defaults, name)
        if isinstance(default, list):
            if name == "acceptance_criteria":
                normalized[name] = (
                    [item for item in raw if item not in (None, "")]
                    if isinstance(raw, list)
                    else []
                )
            elif isinstance(raw, list):
                normalized[name] = [
                    str(item).strip()
                    for item in raw
                    if str(item).strip()
                ]
            elif isinstance(raw, str):
                normalized[name] = [
                    item.strip()
                    for item in raw.split(",")
                    if item.strip()
                ]
            else:
                normalized[name] = []
        elif isinstance(default, dict):
            normalized[name] = dict(raw) if isinstance(raw, dict) else {}
        elif isinstance(default, bool):
            if isinstance(raw, str):
                normalized[name] = raw.strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
            else:
                normalized[name] = bool(raw)
        elif isinstance(default, int):
            try:
                normalized[name] = int(raw)
            except (TypeError, ValueError):
                normalized[name] = default
        else:
            normalized[name] = str(raw or default)
    return TaskContract(**normalized)


@dataclass
class TaskEvidence:
    commit: str = ""
    output_summary: str = ""
    verified_at: str = ""
    commits: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)


@dataclass
class TaskExecutionBinding:
    """Mechanical owner/request identity, separate from the semantic contract."""

    owner: str = ""
    request_id: str = ""
    request_revision: int = 0
    workflow_run_id: str = ""
    origin_binding_digest: str = ""
    origin_task_digest: str = ""


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
    execution_binding: TaskExecutionBinding = field(
        default_factory=TaskExecutionBinding
    )
    # A CAS lineage for complete semantic-contract / execution-binding writes.
    # Task capsule refs and acceptance evidence are field-level metadata and do
    # not advance this identity.
    contract_authority_revision: str = ""
    contract_authority_sequence: int = 0
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
