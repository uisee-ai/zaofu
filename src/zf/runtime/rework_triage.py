"""Deterministic classification before routing a failure to rework."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from zf.core.events.model import ZfEvent


# PREREQ-B (2026-05-18, doc 40 §6 I57): single source of truth via
# WorkflowEventSets.baseline(). Previously a separate hardcoded
# frozenset literal here — kept identical, but adding a new rework
# trigger now requires editing only WorkflowEventSets.baseline() in
# zf.core.workflow.topology.
from zf.core.workflow.topology import WorkflowEventSets

REWORK_TRIAGE_TRIGGER_EVENTS: frozenset[str] = (
    WorkflowEventSets.baseline().rework_triage_trigger_events
)

REWORK_RETRY_CLASSIFICATIONS: frozenset[str] = frozenset({
    "product_issue",
    "design_issue",
    "yaml_routing",
    # #U fix: arch redesigns plan after phase_gate_violation,
    # then retry the original vertical. Retry counter advances
    # so 4-level cap still bounds runaway.
    "phase_gate_violation",
})


# EVAL-FAILURE-TAXONOMY-001 (doc 43 §2.2): 7-category failure taxonomy
# keyed by task.failure_reason. Splits failures into:
#
# **infra** (auto-retry eligible — kernel can retry without operator):
#   transport_failed / worker_stuck / provider_timeout / runtime_offline
# **content** (agent output quality — needs review of prompt / spec):
#   product_issue / design_issue / yaml_routing / review_rejected_content /
#   test_failed_real / agent_error / scope_violation
# **terminal** (poisoned session — must escalate, never retry):
#   iteration_limit / agent_fallback_message / api_invalid_request
#
# ``retryable`` derives directly from the classification's bucket.
# ``is_terminal`` is True only for the 3 terminal classifications.
INFRA_CLASSIFICATIONS: frozenset[str] = frozenset({
    "transport_failed",
    "worker_stuck",
    "provider_timeout",
    "runtime_offline",
})

TERMINAL_CLASSIFICATIONS: frozenset[str] = frozenset({
    "iteration_limit",
    "agent_fallback_message",
    "api_invalid_request",
})

CONTENT_CLASSIFICATIONS: frozenset[str] = frozenset({
    "product_issue",
    "design_issue",
    "yaml_routing",
    "review_rejected_content",
    "test_failed_real",
    "agent_error",
    "scope_violation",
    # #U fix (TR-TRIAGE-PHASE-GATE-001, cangjie 2026-05-22 r3 P0V06):
    # plan-level phase gate order violation (e.g. P1 ship before P0
    # RED gate established). Same bucket as design_issue — needs arch
    # redesign, not dev rework.
    "phase_gate_violation",
})


def derive_retryable(classification: str) -> bool:
    """Return True iff classification is in the infra bucket (auto-retry)."""
    return classification in INFRA_CLASSIFICATIONS


def derive_is_terminal(classification: str) -> bool:
    """Return True iff classification is in the terminal bucket
    (poisoned session — never retry)."""
    return classification in TERMINAL_CLASSIFICATIONS


def derive_taxonomy_bucket(classification: str) -> str:
    """Return 'infra' | 'content' | 'terminal' | 'unknown'."""
    if classification in INFRA_CLASSIFICATIONS:
        return "infra"
    if classification in TERMINAL_CLASSIFICATIONS:
        return "terminal"
    if classification in CONTENT_CLASSIFICATIONS:
        return "content"
    return "unknown"


@dataclass(frozen=True)
class ReworkTriageResult:
    classification: str
    gate_rule: str
    suspected_owner: str
    recommended_action: str
    should_increment_retry: bool
    notes: str = ""
    # EVAL-FAILURE-TAXONOMY-001 fields. Derived from classification when
    # not set explicitly. Frozen-safe default via field().
    retryable: bool = False
    is_terminal: bool = False
    taxonomy_bucket: str = ""

    def __post_init__(self) -> None:
        # Derive taxonomy fields from classification when callers leave
        # them at default. Use object.__setattr__ because the dataclass
        # is frozen.
        if not self.taxonomy_bucket:
            object.__setattr__(
                self, "taxonomy_bucket",
                derive_taxonomy_bucket(self.classification),
            )
        if not self.retryable:
            object.__setattr__(
                self, "retryable", derive_retryable(self.classification),
            )
        if not self.is_terminal:
            object.__setattr__(
                self, "is_terminal", derive_is_terminal(self.classification),
            )

    def to_payload(self, event: ZfEvent) -> dict[str, Any]:
        return {
            "task_id": event.task_id or "",
            "failed_event_id": event.id,
            "failed_event_type": event.type,
            "classification": self.classification,
            "gate_rule": self.gate_rule,
            "suspected_owner": self.suspected_owner,
            "recommended_action": self.recommended_action,
            "should_increment_retry": self.should_increment_retry,
            "notes": self.notes,
            # EVAL-FAILURE-TAXONOMY-001 surfaces:
            "retryable": self.retryable,
            "is_terminal": self.is_terminal,
            "taxonomy_bucket": self.taxonomy_bucket,
        }


def classify_rework_trigger(
    event: ZfEvent,
    config: Any = None,
) -> ReworkTriageResult:
    """Classify a failure event before product rework is requested.

    This is intentionally deterministic and conservative. LLM/autoresearch can
    add explanation later, but retry accounting and dispatch routing need a
    stable fallback that works without another agent turn.

    P1/K2 (docs/impl/22): if ``config.workflow.rework_routing[event.type]``
    has an explicit target role, honor it BEFORE the heuristic classifier.
    Without this priority, ``gate.failed`` from critic was getting caught by
    the ``_evidence_gap`` heuristic (because critic's risks/fix_items text
    contains words like "missing" or "artifact_refs") and routed back to
    critic for evidence reissue, ignoring the yaml's explicit ``arch``
    target. This makes the yaml ``rework_routing`` actually authoritative.
    """
    payload = event.payload if isinstance(event.payload, dict) else {}
    text = _payload_text(payload)
    failed_d = _failed_d(payload)
    gate = _gate_rule(payload, failed_d)

    # K2: yaml workflow.rework_routing has highest priority.
    if config is not None:
        try:
            routing = getattr(getattr(config, "workflow", None), "rework_routing", {}) or {}
            target = str(routing.get(event.type, "") or "").strip()
        except Exception:
            target = ""
        if target:
            return _result(
                "yaml_routing",
                gate or event.type,
                target,
                "dispatch_rework",
                f"workflow.rework_routing maps {event.type} -> {target}",
            )

    if event.type == "task.done.blocked":
        return _result(
            "evidence_payload_gap",
            gate or "terminal_done_evidence",
            "judge",
            "request_evidence_reissue",
            "terminal done claim is blocked by missing evidence",
        )

    if (
        _contains_any(text, _HARNESS_MARKERS)
        or _discriminator_harness_profile_issue(event, text, failed_d)
        or "DiscriminatorRunner" in failed_d
    ):
        return _result(
            "harness_rule_issue",
            gate or "harness_rule",
            "harness",
            "suspend_for_harness_fix",
            "failure text points at harness/gate/rule behavior",
        )

    if _contains_any(text, _ENVIRONMENT_MARKERS):
        return _result(
            "environment_issue",
            gate or "environment",
            "operator",
            "recover_environment",
            "failure text points at environment/provider/dependency state",
        )

    # #U fix (TR-TRIAGE-PHASE-GATE-001, cangjie 2026-05-22 r3 P0V06):
    # detect phase_gate_violation BEFORE evidence_payload_gap fallback.
    # Phase gate violations are plan-level (baseline order conflicts,
    # gate placement) — evidence reissue retry is wasted; arch redesign
    # is the right path.
    if _is_phase_gate_violation(payload, text):
        return _result(
            "phase_gate_violation",
            gate or "phase_gate",
            "arch",
            "dispatch_rework",
            "phase gate order violation — baseline contamination or plan-level"
            " gate placement conflict; arch must redesign plan order before retry",
        )

    if _evidence_gap(event, text, failed_d, payload):
        return _result(
            "evidence_payload_gap",
            gate or "evidence_schema",
            _evidence_owner(event, payload),
            "request_evidence_reissue",
            "failure is missing or malformed evidence, not product behavior",
        )

    if event.type == "gate.failed":
        return _result(
            "design_issue",
            gate or "gate",
            "arch",
            "dispatch_rework",
            "gate failure requires bounded design/contract rework",
        )

    if event.type == "discriminator.failed":
        if any(name in failed_d for name in {"ArchitectureRulesD", "ContractD", "ContractQualityD"}):
            return _result(
                "design_issue",
                gate or "discriminator",
                "arch",
                "dispatch_rework",
                "discriminator failure points at design/contract mismatch",
            )
        return _result(
            "product_issue",
            gate or "discriminator",
            "dev",
            "dispatch_rework",
            "discriminator failure points at delivered behavior",
        )

    if event.type == "dev.failed":
        if (
            "artifact_integrity_mismatch" in text
            or "reproject_or_replan" in text
            or "re-plan" in text
            or "replan" in text
        ):
            return _result(
                "design_issue",
                gate or "dev",
                "arch",
                "dispatch_rework",
                "dev failure points at stale or invalid plan artifacts; "
                "arch must refresh the plan/artifact contract before retry",
            )
        return _result(
            "product_issue",
            gate or "dev",
            "dev",
            "dispatch_rework",
            "dev failure points at implementation behavior by default",
        )

    if event.type in {"review.rejected", "review.child.failed"}:
        if _contains_any(text, _DESIGN_MARKERS):
            return _result(
                "design_issue",
                gate or "review",
                "arch",
                "dispatch_rework",
                "review rejection points at spec/design/contract",
            )
        return _result(
            "product_issue",
            gate or "review",
            "dev",
            "dispatch_rework",
            "review rejection points at implementation quality",
        )

    if event.type in {
        "verify.failed",
        "verify.child.failed",
        "test.failed",
        "judge.failed",
    }:
        return _result(
            "product_issue",
            gate or event.type,
            "dev",
            "dispatch_rework",
            "verify/test/judge failure points at product behavior by default",
        )

    if event.type == "static_gate.failed":
        # P3/K5: static_gate failure (typecheck/biome/install) is by
        # construction a dev issue — the proposal already passed critic,
        # so dev's implementation broke something. Routes to dev by default;
        # yaml workflow.rework_routing.static_gate.failed can override.
        return _result(
            "product_issue",
            gate or "static_gate",
            "dev",
            "dispatch_rework",
            "static_gate failure (typecheck/lint/install) is a dev product issue",
        )

    if event.type == "candidate.conflict":
        # doc 78 W2: a cherry-pick conflict at candidate build means two slices'
        # commits touch the same files — a decomposition (plan-level) error.
        # Re-implementing the same task_map reproduces it; arch must re-plan.
        return _result(
            "design_issue",
            gate or "integration",
            "arch",
            "dispatch_rework",
            "candidate cherry-pick conflict is a plan-level slice overlap;"
            " arch must re-plan slice boundaries before retry",
        )

    if event.type == "integration.failed":
        # doc 78 W2: a cherry-pick conflict or path overlap means two slices
        # touch the same files — a decomposition (plan-level) error, not impl.
        # Route to arch for re-plan; a clean build/quality failure is dev's.
        # Match the overlap/conflict markers only against human-readable failure
        # fields (error/reason/message), NOT the whole serialized payload — a
        # benign field such as target_ref="cand/fix-merge-conflict" or a failing
        # test name must not misroute a clean build failure to a re-plan.
        status = str(payload.get("status") or "")
        reason_text = _integration_plan_reason_text(payload)
        if (
            status == "conflict"
            or payload.get("conflict_files")
            or _contains_any(reason_text, _INTEGRATION_PLAN_MARKERS)
        ):
            return _result(
                "design_issue",
                gate or "integration",
                "arch",
                "dispatch_rework",
                "integration conflict/overlap is a plan-level decomposition error;"
                " arch must re-plan slice boundaries before retry",
            )
        return _result(
            "product_issue",
            gate or "integration",
            "dev",
            "dispatch_rework",
            "integration failure without conflict signals points at dev product",
        )

    return _result(
        "ambiguous",
        gate or "unknown",
        "orchestrator",
        "request_critic_triage",
        "no deterministic classifier matched",
    )


def should_increment_retry(result: ReworkTriageResult) -> bool:
    return result.classification in REWORK_RETRY_CLASSIFICATIONS


def triage_from_payload(payload: object) -> ReworkTriageResult | None:
    if not isinstance(payload, dict):
        return None
    classification = str(payload.get("classification") or "").strip()
    if not classification:
        return None
    return ReworkTriageResult(
        classification=classification,
        gate_rule=str(payload.get("gate_rule") or ""),
        suspected_owner=str(payload.get("suspected_owner") or ""),
        recommended_action=str(payload.get("recommended_action") or ""),
        should_increment_retry=bool(payload.get("should_increment_retry")),
        notes=str(payload.get("notes") or ""),
    )


def _result(
    classification: str,
    gate_rule: str,
    suspected_owner: str,
    recommended_action: str,
    notes: str,
) -> ReworkTriageResult:
    return ReworkTriageResult(
        classification=classification,
        gate_rule=gate_rule,
        suspected_owner=suspected_owner,
        recommended_action=recommended_action,
        should_increment_retry=classification in REWORK_RETRY_CLASSIFICATIONS,
        notes=notes,
    )


def _payload_text(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    except (TypeError, ValueError):
        return str(payload).lower()


def _integration_plan_reason_text(payload: dict[str, Any]) -> str:
    """Human-readable integration failure text used for plan-level routing.

    Keep this narrower than the full payload so benign branch names or test
    identifiers containing words like "conflict" do not trigger replan.
    """
    parts = [str(payload.get(key) or "") for key in ("error", "reason", "message")]
    findings = payload.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            for key in ("category", "reason", "message", "summary", "title"):
                value = item.get(key)
                if value not in (None, ""):
                    parts.append(str(value))
    return " ".join(parts).lower()


def _failed_d(payload: dict[str, Any]) -> set[str]:
    raw = payload.get("failed_d")
    if isinstance(raw, list):
        return {str(item).strip() for item in raw if str(item).strip()}
    if isinstance(raw, str) and raw.strip():
        return {raw.strip()}
    details = payload.get("details")
    out: set[str] = set()
    if isinstance(details, list):
        for item in details:
            if not isinstance(item, dict):
                continue
            name = str(item.get("d") or item.get("name") or "").strip()
            if name:
                out.add(name)
    return out


def _gate_rule(payload: dict[str, Any], failed_d: set[str]) -> str:
    for key in ("gate", "rule", "failed_rule", "source"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    if failed_d:
        return ",".join(sorted(failed_d))
    return ""


def _is_phase_gate_violation(
    payload: dict[str, Any], text: str,
) -> bool:
    """#U fix: detect phase_gate_violation across 3 signals.

    Signal 1: payload.phase_gate_check.violation non-empty (kernel-emitted
              when dev runs phase gate check and detects baseline contamination)
    Signal 2: payload.trigger_misclassification.actual_classification
              contains 'phase_gate' (dev round-2 explicit halt with override)
    Signal 3: text marker 'phase_gate_violation' / 'phase gate violation'
              (catch-all fallback for unstructured payload)

    Returns True if any signal matches. Conservative — only fires on
    explicit signal, not inferred from generic 'gate' / 'phase' words.
    """
    pgc = payload.get("phase_gate_check")
    if isinstance(pgc, dict) and pgc.get("violation"):
        return True
    tm = payload.get("trigger_misclassification")
    if isinstance(tm, dict):
        actual = str(tm.get("actual_classification") or "").lower()
        if "phase_gate" in actual:
            return True
    if "phase_gate_violation" in text or "phase gate violation" in text:
        return True
    return False


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _discriminator_harness_profile_issue(
    event: ZfEvent,
    text: str,
    failed_d: set[str],
) -> bool:
    if event.type != "discriminator.failed":
        return False
    if "ContractD" in failed_d and _contains_any(
        text,
        _CONTRACT_COMMAND_HARNESS_MARKERS,
    ):
        return True
    if "FunctionalD" in failed_d and _contains_any(
        text,
        _FUNCTIONAL_GATE_PROFILE_MARKERS,
    ):
        return True
    return False


def _evidence_gap(
    event: ZfEvent,
    text: str,
    failed_d: set[str],
    payload: dict[str, Any],
) -> bool:
    if "ReworkDeltaD" in failed_d:
        return True
    if "missing" in payload and event.type in {
        "task.done.blocked",
        "discriminator.failed",
        "gate.failed",
    }:
        return True
    return _contains_any(text, _EVIDENCE_GAP_MARKERS)


def _evidence_owner(event: ZfEvent, payload: dict[str, Any]) -> str:
    owner = str(payload.get("role") or payload.get("actor") or event.actor or "")
    if owner and owner != "zf-cli":
        return owner
    trigger = str(payload.get("trigger_event") or "")
    if trigger.startswith("judge."):
        return "judge"
    if trigger.startswith("test."):
        return "test"
    if trigger.startswith("review."):
        return "review"
    return "dev"


_EVIDENCE_GAP_MARKERS = (
    "missing evidence",
    "evidence missing",
    "payload missing",
    "missing payload",
    "schema missing",
    "evidence schema",
    "artifact_refs",
    "evidence_refs",
    "dispatch_id missing",
    "dispatch_id_missing",
    "required_actions_not_covered",
    "rework_delta_missing",
    "no code/test/doc/evidence delta",
)

_HARNESS_MARKERS = (
    "false positive",
    "false_positive",
    "harness bug",
    "harness_rule",
    "gate bug",
    "gate misfire",
    "rule bug",
    "rule misfire",
    "discriminator bug",
    "misclassified",
    "internal error",
    "traceback",
)

_CONTRACT_COMMAND_HARNESS_MARKERS = (
    "ambiguous argument",
    "unknown revision or path",
    "verification command failed (rc=128)",
)

_FUNCTIONAL_GATE_PROFILE_MARKERS = (
    "cannot read file",
    "tsconfig.json",
    "no files were processed",
    "specified paths were ignored",
    "no files found",
    "scoped worktree",
)

_ENVIRONMENT_MARKERS = (
    "environment",
    "dependency unavailable",
    "external service",
    "port in use",
    "connection refused",
    "network",
    "rate limit",
    "rate_limited",
    "api blocked",
    "auth error",
    "permission denied",
    "command not found",
    "no such file",
    "timeout",
    "timed out",
)

# doc 78 W2: integration.failed signals that point at slice decomposition
# (plan-level) rather than implementation quality.
_INTEGRATION_PLAN_MARKERS = (
    "conflict",
    "cherry-pick",
    "cherry pick",
    "overlap",
    "overlapping",
    "authoritative_verification_unrunnable_inside_allowed_scope",
    "outside allowed_paths",
    "outside this slice's allowed_paths",
    "outside this child's allowed paths",
    "requires workspace/package files",
    "verification command exceeds task scope",
)

_DESIGN_MARKERS = (
    "spec",
    "sdd",
    "tdd",
    "plan",
    "architecture",
    "architectural",
    "contract",
    "acceptance",
    "backlog",
    "scope",
)
