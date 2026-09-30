"""Pure planning, budget, and applicability rules for Issue Investigation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

from .enrichment import Evidence


@dataclass(frozen=True)
class InvestigationBudget:
    max_rounds: int = 3
    max_tools_per_round: int = 20
    max_reproductions_per_round: int = 3
    command_timeout_seconds: int = 180
    active_runtime_seconds: int = 1200
    max_no_new_evidence_rounds: int = 2
    transient_retries: int = 2

    def validate(self) -> None:
        if min(asdict(self).values()) < 1:
            raise ValueError("Investigation budget values must be positive")


@dataclass(frozen=True)
class InvestigationPlan:
    plan_id: str
    draft_id: str
    draft_revision: int
    draft_digest: str
    target_repository: str
    project_revision: str
    environment_fingerprint: str
    allowed_paths: tuple[str, ...]
    attachment_refs: tuple[str, ...]
    commands: tuple[str, ...]
    budget: InvestigationBudget = InvestigationBudget()
    schema_version: str = "investigation-plan.v1"

    def validate(self) -> None:
        if self.schema_version != "investigation-plan.v1":
            raise ValueError("unsupported Investigation Plan schema")
        if not all((
            self.plan_id, self.draft_id, self.target_repository,
            self.project_revision, self.environment_fingerprint,
        )):
            raise ValueError("Investigation Plan binding is incomplete")
        if self.draft_revision < 1 or not self.draft_digest.startswith("sha256:"):
            raise ValueError("Investigation Plan must bind a Draft revision and digest")
        if not self.environment_fingerprint.startswith("sha256:"):
            raise ValueError("Investigation Plan requires an environment fingerprint")
        if any(path.startswith(("/", "..")) for path in self.allowed_paths):
            raise ValueError("Investigation allowed paths must remain repository-relative")
        if any(not value.strip() for value in (*self.allowed_paths, *self.commands)):
            raise ValueError("Investigation paths and commands cannot be empty")
        self.budget.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            **asdict(self),
            "allowed_paths": list(self.allowed_paths),
            "attachment_refs": list(self.attachment_refs),
            "commands": list(self.commands),
            "budget": asdict(self.budget),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InvestigationPlan":
        if str(value.get("schema_version") or "") != "investigation-plan.v1":
            raise ValueError("unsupported Investigation Plan schema")
        plan = cls(
            **{
                **dict(value),
                "allowed_paths": tuple(value.get("allowed_paths") or ()),
                "attachment_refs": tuple(value.get("attachment_refs") or ()),
                "commands": tuple(value.get("commands") or ()),
                "budget": InvestigationBudget(**dict(value.get("budget") or {})),
            },
        )
        plan.validate()
        return plan


@dataclass(frozen=True)
class InvestigationRun:
    run_id: str
    plan: InvestigationPlan
    status: str
    round_number: int
    tools_in_round: int
    reproductions_in_round: int
    active_seconds: int
    consecutive_no_new_evidence_rounds: int
    transient_failures: int
    started_at: str
    stop_reason: str = ""
    schema_version: str = "investigation-run.v1"

    @classmethod
    def start(
        cls, run_id: str, plan: InvestigationPlan, *, started_at: str,
    ) -> "InvestigationRun":
        plan.validate()
        run = cls(
            run_id=run_id,
            plan=plan,
            status="active",
            round_number=1,
            tools_in_round=0,
            reproductions_in_round=0,
            active_seconds=0,
            consecutive_no_new_evidence_rounds=0,
            transient_failures=0,
            started_at=started_at,
        )
        run.validate()
        return run

    def validate(self) -> None:
        if self.schema_version != "investigation-run.v1":
            raise ValueError("unsupported Investigation Run schema")
        if not self.run_id or not self.started_at:
            raise ValueError("Investigation Run identity is incomplete")
        if self.status not in {"active", "completed", "stopped", "failed", "cancelled"}:
            raise ValueError("unsupported Investigation Run status")
        if not 1 <= self.round_number <= self.plan.budget.max_rounds:
            raise ValueError("Investigation Run round is outside its budget")
        self.plan.validate()

    def _require_active(self) -> None:
        if self.status != "active":
            raise ValueError("Investigation Run is not active")

    def record_tool(self) -> "InvestigationRun":
        self._require_active()
        if self.tools_in_round >= self.plan.budget.max_tools_per_round:
            raise ValueError("Investigation tool budget exhausted")
        return replace(self, tools_in_round=self.tools_in_round + 1)

    def record_reproduction(self) -> "InvestigationRun":
        self._require_active()
        if self.reproductions_in_round >= self.plan.budget.max_reproductions_per_round:
            raise ValueError("Investigation reproduction budget exhausted")
        return replace(self, reproductions_in_round=self.reproductions_in_round + 1)

    def record_transient_failure(self) -> "InvestigationRun":
        self._require_active()
        failures = self.transient_failures + 1
        if failures > self.plan.budget.transient_retries:
            return replace(self, status="stopped", stop_reason="transient_retry_exhausted")
        return replace(self, transient_failures=failures)

    def finish_round(
        self, *, new_evidence_count: int, active_seconds: int,
    ) -> "InvestigationRun":
        self._require_active()
        if new_evidence_count < 0 or active_seconds < 0:
            raise ValueError("Investigation round counters cannot be negative")
        total_seconds = self.active_seconds + active_seconds
        no_evidence = (
            0 if new_evidence_count else self.consecutive_no_new_evidence_rounds + 1
        )
        if total_seconds >= self.plan.budget.active_runtime_seconds:
            return replace(
                self,
                status="stopped",
                stop_reason="active_runtime_exhausted",
                active_seconds=total_seconds,
                consecutive_no_new_evidence_rounds=no_evidence,
            )
        if no_evidence >= self.plan.budget.max_no_new_evidence_rounds:
            return replace(
                self,
                status="stopped",
                stop_reason="no_new_evidence",
                active_seconds=total_seconds,
                consecutive_no_new_evidence_rounds=no_evidence,
            )
        if self.round_number >= self.plan.budget.max_rounds:
            return replace(
                self,
                status="stopped",
                stop_reason="round_budget_exhausted",
                active_seconds=total_seconds,
                consecutive_no_new_evidence_rounds=no_evidence,
            )
        return replace(
            self,
            round_number=self.round_number + 1,
            tools_in_round=0,
            reproductions_in_round=0,
            active_seconds=total_seconds,
            consecutive_no_new_evidence_rounds=no_evidence,
        )


def evidence_for_context(
    evidence: Evidence,
    *,
    project_revision: str,
    environment_fingerprint: str,
    candidate_digest: str = "",
) -> Evidence:
    """Return an applicability update without mutating the immutable artifact."""

    evidence.validate()
    if evidence.integrity == "invalid" or evidence.applicability != "current":
        return evidence
    if evidence.project_revision and evidence.project_revision != project_revision:
        return evidence.mark_stale("project_revision_changed")
    if (
        evidence.environment_fingerprint
        and evidence.environment_fingerprint != environment_fingerprint
    ):
        return evidence.mark_stale("environment_changed")
    if evidence.candidate_digest and evidence.candidate_digest != candidate_digest:
        return evidence.mark_stale("candidate_digest_changed")
    return evidence


__all__ = [
    "InvestigationBudget", "InvestigationPlan", "InvestigationRun",
    "evidence_for_context",
]
