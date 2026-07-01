"""Authorization gate + bounded cap for the authorized self-repair loop.

backlog 2026-06-05-0820. Decides whether an autoresearch ``bug_candidate``
should be AUTO-dispatched to a ``zf-self-repair`` agent (detect → backlog → fix
→ verify → done), versus the default propose-only / prepare-for-approval path.

Safety is opt-in + bounded:
- **Default OFF.** Only when the operator sets
  ``ZF_AUTORESEARCH_AUTO_REPAIR=authorized`` does anything auto-dispatch. Any
  other value (unset, "off", "prepare") → skip → existing behavior unchanged.
- **Bounded.** A failure ``fingerprint`` that already reached the attempt cap
  escalates to a human instead of looping forever on a fix that does not stick.

Pure functions (events in → decision out) so the orchestrator reactor can call
this and it stays unit-testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from zf.runtime.remediation_cascade import classify_bucket

AUTO_REPAIR_ENV = "ZF_AUTORESEARCH_AUTO_REPAIR"
SELF_REPAIR_BACKEND_ENV = "ZF_AUTORESEARCH_SELF_REPAIR_BACKEND"
REPAIR_DISPATCH_EVENT = "autoresearch.repair.dispatch_requested"
SELF_REPAIR_SKILL = "zf-self-repair"
DEFAULT_REPAIR_CAP = 2
SUPPORTED_SELF_REPAIR_BACKENDS = frozenset({"codex", "claude-code"})

# R14 fix (backlog 2026-06-06-0401 §B follow-up): kernel-LOGIC structural bugs.
# These are NOT in the rework_triage taxonomy (classify_bucket → "unknown") but,
# unlike transient infra (worker_stuck → Tier1 cascade), they are reproducible
# CODE bugs the authorized self-repair loop can investigate + fix in an isolated
# worktree. R14 detected 5x handoff_stall (= verify-not-firing: a stage success
# event not triggering the downstream stage) + dispatch_preflight_blocker, but
# the §16 routing wrongly lumped them with transient infra → skip → 0 dispatched.
# Route these to Tier2 self-repair (subject to the cap), not skip.
KERNEL_LOGIC_STRUCTURAL = frozenset({
    "handoff_stall",
    "dispatch_preflight_blocker",
    "stall",
    "task_ref_rejected",
    "missing_task_ref_after_dev_build_done",
    "fanout_timed_out",
    "completion_snapshot_ref_missing",
    "task_contract_invalid",
})


def canonical_repair_fingerprint(fingerprint: str) -> str:
    """Return the cap/dedupe key for repairable runtime failures.

    Real runs can surface the same structural bug through two detector paths:
    a direct fingerprint (``task_ref_rejected:...``) and a generic failure
    wrapper (``failure:task_ref_rejected:...``). The repair cap must count
    those as one failure or autoresearch spends two bounded attempts on the
    same root cause before escalating.
    """

    text = str(fingerprint or "").strip()
    parts = text.split(":", 2)
    if (
        len(parts) >= 3
        and parts[0] == "failure"
        and parts[1] in KERNEL_LOGIC_STRUCTURAL
    ):
        return f"{parts[1]}:{parts[2]}"
    return text


def auto_repair_authorized(env: dict[str, str] | None = None) -> bool:
    """True only when the operator explicitly authorized auto-repair."""
    src = os.environ if env is None else env
    return str(src.get(AUTO_REPAIR_ENV) or "").strip().lower() == "authorized"


def configured_repair_mode(config: object | None) -> str:
    policy = getattr(getattr(config, "autoresearch", None), "trigger_policy", None)
    mode = str(getattr(policy, "repair_mode", "") or "proposal_only").strip()
    return mode if mode in {"proposal_only", "bounded_repair"} else "proposal_only"


def auto_repair_consumer_enabled(
    config: object | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    """True when self-repair dispatch requests should be consumed.

    ``bounded_repair`` in zf.yaml is a control-plane authorization to consume
    already-gated repair dispatches. The env var remains a deliberate override
    for legacy/manual runs that have not encoded the policy in config.
    """

    if auto_repair_authorized(env):
        return True
    return configured_repair_mode(config) == "bounded_repair"


def configured_self_repair_backend(
    config: object | None = None,
    env: dict[str, str] | None = None,
) -> str:
    src = os.environ if env is None else env
    override = str(src.get(SELF_REPAIR_BACKEND_ENV) or "").strip()
    if override:
        return override
    policy = getattr(getattr(config, "autoresearch", None), "trigger_policy", None)
    backend = str(getattr(policy, "self_repair_backend", "") or "").strip()
    if backend:
        return backend
    run_manager = getattr(getattr(config, "runtime", None), "run_manager", None)
    backend = str(getattr(run_manager, "backend", "") or "").strip()
    if backend:
        return backend
    return infer_self_repair_backend(config)


def infer_self_repair_backend(config: object | None) -> str:
    """Infer a repair backend only when the project has one obvious agent backend."""

    backends: set[str] = set()
    for role in getattr(config, "roles", []) or []:
        for backend in [getattr(role, "backend", "") or "", *(
            getattr(role, "backends", []) or []
        )]:
            normalized = str(backend or "").strip()
            if normalized in SUPPORTED_SELF_REPAIR_BACKENDS:
                backends.add(normalized)
    if len(backends) == 1:
        return next(iter(backends))
    return ""


def _fingerprint(candidate_payload: dict) -> str:
    candidate = candidate_payload.get("candidate")
    candidate = candidate if isinstance(candidate, dict) else {}
    return canonical_repair_fingerprint(
        str(candidate.get("fingerprint") or candidate_payload.get("fingerprint") or ""),
    )


def repair_attempts_for_fingerprint(events, fingerprint: str) -> int:
    """Count prior auto-repair dispatches for the same failure fingerprint."""
    canonical = canonical_repair_fingerprint(fingerprint)
    if not canonical:
        return 0
    n = 0
    for event in events:
        if getattr(event, "type", "") != REPAIR_DISPATCH_EVENT:
            continue
        payload = getattr(event, "payload", {}) or {}
        if (
            isinstance(payload, dict)
            and canonical_repair_fingerprint(str(payload.get("fingerprint") or ""))
            == canonical
        ):
            n += 1
    return n


def _failure_class(candidate_payload: dict) -> str:
    """Extract the failure class — explicit field, else the fingerprint head.

    Fingerprints come in two real shapes (verified against R12):
    - ``failure:<class>:<subject>:...`` (e.g. ``failure:worker_stuck:dev-lane-4``)
      → class is the 2nd segment;
    - ``stall:<trigger>-><stage>:<feature>`` (structural stall detector) → these
      are structural, returned as ``"stall"`` so they are identifiable (and
      route to the non-content path) rather than silently collapsing to ``""``.
    """
    candidate = candidate_payload.get("candidate")
    candidate = candidate if isinstance(candidate, dict) else {}
    explicit = str(
        candidate.get("classification")
        or candidate.get("failure_class")
        or candidate_payload.get("classification")
        or ""
    ).strip()
    if explicit:
        return explicit
    fingerprint = _fingerprint(candidate_payload)
    for structural in sorted(KERNEL_LOGIC_STRUCTURAL, key=len, reverse=True):
        if fingerprint.startswith(f"{structural}:"):
            return structural
    parts = fingerprint.split(":")
    if len(parts) >= 2 and parts[0] == "failure":
        return parts[1].strip()
    if parts and parts[0] == "stall":
        return "stall"
    return ""


@dataclass(frozen=True)
class RepairDecision:
    action: str  # "dispatch" | "escalate" | "skip"
    fingerprint: str
    attempt: int
    reason: str
    bucket: str = ""


def decide_repair(
    candidate_payload: dict,
    events,
    *,
    env: dict[str, str] | None = None,
    cap: int = DEFAULT_REPAIR_CAP,
) -> RepairDecision:
    """Decide what to do with an autoresearch bug_candidate under authorization.

    - not authorized → ``skip`` (default; existing propose-only behavior stands)
    - authorized + under cap → ``dispatch`` (auto-run the zf-self-repair loop)
    - authorized + at/over cap → ``escalate`` (a fix that won't stick goes to a
      human rather than looping)
    """
    if not auto_repair_authorized(env):
        return RepairDecision("skip", "", 0, "auto-repair not authorized (default)")
    fingerprint = _fingerprint(candidate_payload)
    if not fingerprint:
        return RepairDecision("skip", "", 0, "candidate has no fingerprint")

    # doc 79 Tier routing (R14-adjusted): LLM self-repair = the ``content``
    # bucket PLUS kernel-LOGIC structural classes (handoff_stall etc.). R12
    # dispatched 17 fingerprints, 16 of them transient-infra the LLM cannot fix
    # → churn; the over-correction (R14: skip ALL non-content) then meant 9
    # bug_candidates → 0 dispatched. The right line:
    #   infra (transient: worker_stuck)        → Tier1 cascade owns it, skip LLM
    #   kernel-logic structural (handoff_stall) → Tier2 self-repair (reproducible code bug)
    #   content                                 → Tier2 self-repair
    #   terminal                                → Tier3 escalate
    #   truly-unknown                           → skip (no handler; cascade floors keep-alive)
    failure_class = _failure_class(candidate_payload)
    bucket = classify_bucket(failure_class)
    llm_repairable = bucket == "content" or failure_class in KERNEL_LOGIC_STRUCTURAL
    if bucket == "infra":
        return RepairDecision(
            "skip", fingerprint, 0,
            f"transient infra ({failure_class}) → Tier1 cascade owns it, not LLM",
            bucket,
        )
    if bucket == "terminal":
        return RepairDecision(
            "escalate", fingerprint, 0,
            f"terminal ({failure_class}) → Tier3 escalate, not LLM self-repair",
            bucket,
        )
    if not llm_repairable:
        return RepairDecision(
            "skip", fingerprint, 0,
            f"unrecognised class ({failure_class!r}) → no handler; cascade floors "
            "keep-alive (not LLM-repairable)",
            bucket,
        )

    attempts = repair_attempts_for_fingerprint(events, fingerprint)
    if attempts >= cap:
        return RepairDecision(
            "escalate", fingerprint, attempts,
            f"auto-repair cap {cap} reached for fingerprint; escalate to human",
            bucket,
        )
    kind = "content" if bucket == "content" else f"kernel-logic ({failure_class})"
    return RepairDecision(
        "dispatch", fingerprint, attempts + 1,
        f"{kind}, authorized, under cap → Tier2 self-repair",
        bucket,
    )
