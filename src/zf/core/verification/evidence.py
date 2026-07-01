"""Structured verification evidence helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.verification.gates import GateResult

TAIL_LIMIT = 2000
_CANONICAL_VERIFICATION_TIERS = {
    "static",
    "runtime",
    "e2e",
    "manual_evidence",
}
_E2E_HINTS = {
    "e2e",
    "end_to_end",
    "end-to-end",
    "test/behavior/",
    "tests/behavior/",
    "behavior test",
    "behavior_test",
    "behaviour test",
    "behaviour_test",
    "playwright",
    "cypress",
    "browser flow",
    "browser_flow",
    "api flow",
    "api_flow",
}


@dataclass
class Evidence:
    gate_name: str
    passed: bool
    output_summary: str
    verified_at: str = ""

    @classmethod
    def from_gate_result(cls, result: GateResult) -> Evidence:
        return cls(
            gate_name=result.name,
            passed=result.passed,
            output_summary=result.output,
            verified_at=datetime.now(timezone.utc).isoformat(),
        )


class EvidenceCollector:
    def __init__(self) -> None:
        self.results: list[Evidence] = []

    def record(self, result: GateResult) -> Evidence:
        evidence = Evidence.from_gate_result(result)
        self.results.append(evidence)
        return evidence

    def all_passed(self) -> bool:
        return all(e.passed for e in self.results)

    def summary(self) -> str:
        lines = []
        for e in self.results:
            status = "PASS" if e.passed else "FAIL"
            lines.append(f"  [{status}] {e.gate_name}: {e.output_summary}")
        return "\n".join(lines)


@dataclass(frozen=True)
class CommandEvidence:
    command: str
    exit_code: int | None
    passed: bool
    stdout_tail: str = ""
    stderr_tail: str = ""
    timed_out: bool = False
    error: str = ""

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def command_evidence(
    *,
    command: str,
    exit_code: int | None,
    stdout: str | bytes = "",
    stderr: str | bytes = "",
    timed_out: bool = False,
    error: str = "",
) -> dict[str, Any]:
    return CommandEvidence(
        command=command,
        exit_code=exit_code,
        passed=(exit_code == 0 and not timed_out and not error),
        stdout_tail=_tail_text(stdout),
        stderr_tail=_tail_text(stderr),
        timed_out=timed_out,
        error=error,
    ).to_payload()


def _tail_text(value: str | bytes) -> str:
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    return (value or "")[-TAIL_LIMIT:]


def latest_task_event(
    event_log: EventLog,
    *,
    task_id: str,
    event_type: str,
) -> ZfEvent | None:
    for event in reversed(event_log.read_all()):
        if event.task_id == task_id and event.type == event_type:
            return event
    return None


def build_done_evidence_payload(
    *,
    task: Task,
    trigger_event: ZfEvent,
    event_log: EventLog,
    discriminator_details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    review = latest_task_event(
        event_log, task_id=task.id, event_type="review.approved",
    )
    test = latest_task_event(
        event_log, task_id=task.id, event_type="test.passed",
    )
    judge = latest_task_event(
        event_log, task_id=task.id, event_type="judge.passed",
    )
    discriminator = latest_task_event(
        event_log, task_id=task.id, event_type="discriminator.passed",
    )
    payload = trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
    contract = task.contract
    evidence = task.evidence
    return {
        "task_id": task.id,
        "trigger_event": trigger_event.type,
        "trigger_event_id": trigger_event.id,
        "actor": trigger_event.actor or "",
        "payload_evidence": payload.get("evidence") or {},
        "payload_checks": payload.get("checks") or payload.get("commands") or [],
        "payload_summary": str(payload.get("summary") or ""),
        "payload_scores": payload.get("scores") or payload.get("scorecard") or {},
        "artifact_refs": payload.get("artifact_refs") or payload.get("artifacts") or [],
        "evidence_refs": (
            payload.get("evidence_refs") or payload.get("evidence_paths") or []
        ),
        "review_event_id": review.id if review else "",
        "test_event_id": test.id if test else "",
        "judge_event_id": judge.id if judge else "",
        "discriminator_event_id": discriminator.id if discriminator else "",
        "contract": {
            "behavior": contract.behavior,
            "verification": contract.verification,
            "verification_tiers": list(contract.verification_tiers),
            "scope": list(contract.scope),
            "exclusions": list(contract.exclusions),
            "acceptance": contract.acceptance,
        },
        "task_evidence": {
            "commit": evidence.commit if evidence else "",
            "commits": list(evidence.commits) if evidence else [],
            "files_touched": list(evidence.files_touched) if evidence else [],
            "output_summary": evidence.output_summary if evidence else "",
            "verified_at": evidence.verified_at if evidence else "",
        },
        "discriminators": discriminator_details or [],
    }


def validate_terminal_done_evidence(
    *,
    done_evidence: dict[str, Any],
    require_payload_evidence: bool,
    required_prior_events: list[str] | None = None,
) -> list[str]:
    missing: list[str] = []
    if require_payload_evidence:
        missing.extend(_validate_judge_payload(done_evidence))
    for event_type in required_prior_events or []:
        if (
            event_type == "review.approved"
            and not done_evidence.get("review_event_id")
        ):
            missing.append("missing prior review.approved evidence")
        elif (
            event_type == "test.passed"
            and not done_evidence.get("test_event_id")
        ):
            missing.append("missing prior test.passed evidence")
        elif (
            event_type == "judge.passed"
            and not done_evidence.get("judge_event_id")
        ):
            missing.append("missing judge.passed evidence")
    return missing


def _validate_judge_payload(done_evidence: dict[str, Any]) -> list[str]:
    """Validate strict judge evidence.

    This is intentionally shape-oriented instead of content-subjective. The
    judge role remains free to decide how to test, but a terminal claim must
    be replayable: summary, command evidence, score dimensions, artifacts,
    evidence references, and all contract-required verification tiers.
    """

    missing: list[str] = []
    if not str(done_evidence.get("payload_summary") or "").strip():
        missing.append("judge.passed payload must include summary")

    checks = _payload_checks(done_evidence)
    if not any(_check_passed(check) for check in checks):
        missing.append(
            "judge.passed payload must include passing command/check evidence"
        )

    scores = _payload_scores(done_evidence)
    required_scores = {
        "correctness",
        "completeness",
        "regression_risk",
        "evidence_quality",
    }
    missing_scores = sorted(required_scores - set(scores))
    if missing_scores:
        missing.append(
            "judge.passed payload missing score dimensions: "
            + ", ".join(missing_scores)
        )

    if not _payload_refs(done_evidence, "artifact_refs", "artifacts"):
        missing.append("judge.passed payload must include replayable artifact refs")
    if not _payload_refs(done_evidence, "evidence_refs", "evidence_paths"):
        missing.append("judge.passed payload must include evidence refs")

    required_tiers = _required_verification_tiers(done_evidence)
    if required_tiers:
        covered = _covered_verification_tiers(done_evidence, checks)
        missing_tiers = sorted(set(required_tiers) - covered)
        if missing_tiers:
            missing.append(
                "judge.passed payload missing verification tier evidence: "
                + ", ".join(missing_tiers)
            )

    return missing


def _payload_evidence(done_evidence: dict[str, Any]) -> dict[str, Any]:
    payload_evidence = done_evidence.get("payload_evidence")
    return payload_evidence if isinstance(payload_evidence, dict) else {}


def _payload_checks(done_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = _payload_evidence(done_evidence)
    values: list[Any] = []
    for key in ("payload_checks",):
        raw = done_evidence.get(key)
        if isinstance(raw, list):
            values.extend(raw)
    for key in ("checks", "commands", "command_evidence"):
        raw = evidence.get(key)
        if isinstance(raw, list):
            values.extend(raw)
    return [item for item in values if isinstance(item, dict)]


def _check_passed(check: dict[str, Any]) -> bool:
    if not str(check.get("command") or check.get("name") or "").strip():
        return False
    if check.get("passed") is True:
        return True
    if check.get("exit_code") == 0:
        return True
    return _check_expected_red(check)


def _check_expected_red(check: dict[str, Any]) -> bool:
    if str(check.get("status") or "").strip() != "RED_expected":
        return False
    if not str(check.get("command") or check.get("name") or "").strip():
        return False
    try:
        exit_code = int(check.get("exit_code"))
    except (TypeError, ValueError):
        return False
    return exit_code != 0 and check.get("timed_out") is not True and not check.get("error")


def _payload_scores(done_evidence: dict[str, Any]) -> dict[str, Any]:
    evidence = _payload_evidence(done_evidence)
    for key in ("scores", "scorecard"):
        raw = evidence.get(key)
        if isinstance(raw, dict):
            return raw
    raw = done_evidence.get("payload_scores")
    return raw if isinstance(raw, dict) else {}


def _payload_refs(done_evidence: dict[str, Any], *keys: str) -> list[str]:
    evidence = _payload_evidence(done_evidence)
    refs: list[str] = []
    for source in (done_evidence, evidence):
        for key in keys:
            raw = source.get(key)
            if isinstance(raw, list):
                refs.extend(str(item).strip() for item in raw if str(item).strip())
            elif isinstance(raw, str) and raw.strip():
                refs.append(raw.strip())
    for check in _payload_checks(done_evidence):
        for key in keys:
            raw = check.get(key)
            if isinstance(raw, list):
                refs.extend(str(item).strip() for item in raw if str(item).strip())
            elif isinstance(raw, str) and raw.strip():
                refs.append(raw.strip())
    return refs


def _required_verification_tiers(done_evidence: dict[str, Any]) -> list[str]:
    contract = done_evidence.get("contract")
    if not isinstance(contract, dict):
        return []
    raw = contract.get("verification_tiers")
    if not isinstance(raw, list):
        return []
    return [
        _canonical_verification_tier(item)
        for item in raw
        if str(item).strip()
    ]


def _covered_verification_tiers(
    done_evidence: dict[str, Any],
    checks: list[dict[str, Any]],
) -> set[str]:
    evidence = _payload_evidence(done_evidence)
    covered: set[str] = set()
    for check in checks:
        if not _check_passed(check):
            continue
        tier = check.get("tier") or check.get("verification_tier")
        if isinstance(tier, str) and tier.strip():
            covered.add(_canonical_verification_tier(tier))
        covered.update(_inferred_verification_tiers(check))
    # 2026-06-10 review (I4/I7): tier_results previously accepted bare
    # booleans / "pass" strings — a self-asserted claim with no replayable
    # command satisfied the contract's tier coverage. Now an entry only
    # counts when it carries command-level evidence (same bar as checks:
    # command/name + passed/exit_code), so coverage stays replayable.
    tier_results = evidence.get("tier_results")
    if isinstance(tier_results, dict):
        for tier, result in tier_results.items():
            if isinstance(result, dict) and _check_passed(result):
                covered.add(_canonical_verification_tier(tier))
    elif isinstance(tier_results, list):
        for item in tier_results:
            if not isinstance(item, dict) or not _check_passed(item):
                continue
            tier = item.get("tier") or item.get("name")
            if isinstance(tier, str) and tier.strip():
                covered.add(_canonical_verification_tier(tier))
    return covered


def _inferred_verification_tiers(check: dict[str, Any]) -> set[str]:
    """Infer canonical tiers from replayable check metadata.

    Judge payloads may classify a CLI behavior-flow command as ``runtime`` even
    when the contract calls that flow ``e2e``. Keep inference narrow: only
    passing checks with behavior/e2e/browser/API-flow hints can add e2e cover.
    """
    text = _check_search_text(check)
    inferred: set[str] = set()
    if any(hint in text for hint in _E2E_HINTS):
        inferred.add("e2e")
    return inferred


def _check_search_text(check: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "id",
        "name",
        "command",
        "summary",
        "description",
        "evidence",
        "artifact_refs",
        "evidence_refs",
    ):
        value = check.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value is not None:
            parts.append(str(value))
    return " ".join(parts).lower().replace(" ", "_")


def _canonical_verification_tier(value: Any) -> str:
    """Collapse tier subtypes into the strict contract tier family.

    Agents often report useful subtypes such as ``runtime_regression`` or
    ``static_quality``. The contract only needs evidence for the canonical
    family, so subtype evidence should satisfy the parent tier.
    """
    raw = str(value).strip()
    token = raw.lower().replace("-", "_").replace(" ", "_")
    if token in _CANONICAL_VERIFICATION_TIERS:
        return token
    for tier in sorted(_CANONICAL_VERIFICATION_TIERS, key=len, reverse=True):
        if token.startswith(f"{tier}_") or token.startswith(f"{tier}:"):
            return tier
    return raw


