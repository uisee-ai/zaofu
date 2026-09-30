"""Deterministic strong-signal routing into the canonical Self-Issue Intake."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zf.core.events import ZfEvent
from zf.core.security.redaction import redact_obj


_STRONG_KERNEL_SIGNALS: dict[str, tuple[str, str, str, bool]] = {
    "kernel.housekeeping.failed": (
        "P1", "Kernel housekeeping failed", "kernel/state", False,
    ),
    "orchestrator.tick.failed": (
        "P1", "Runtime watcher or Orchestrator tick failed", "runtime", False,
    ),
    "runtime.watcher.lag_warning": (
        "P1", "Runtime event watcher accumulated excessive lag", "performance", True,
    ),
    "briefing.hydration.failed": (
        "P1", "Worker briefing hydration failed", "runtime", False,
    ),
    "dispatch.briefing_hydration.failed": (
        "P1", "Dispatched worker briefing could not be hydrated", "runtime", False,
    ),
    "scope.snapshot.failed": (
        "P2", "A bounded runtime scope snapshot failed", "kernel/state", False,
    ),
    "workflow.call.result.invalid": (
        "P1", "A workflow call returned an invalid result contract", "runtime", False,
    ),
    "workdir.dependency_apply.failed": (
        "P2", "A workdir dependency could not be applied", "runtime", False,
    ),
    "flow.roles.activation.failed": (
        "P1", "Configured workflow roles could not be activated", "configuration", False,
    ),
}

_SAFE_SUMMARY_FIELDS = (
    "reason", "error", "failure_class", "step", "status", "component",
    "operation", "action", "route", "lag_seconds", "pending_events",
)


@dataclass(frozen=True)
class SelfIssueSignal:
    event_id: str
    event_type: str
    actor: str
    task_id: str
    title: str
    summary: str
    severity: str
    classification: str
    reporter_kind: str
    evidence_refs: tuple[dict[str, Any], ...]
    browser_capture_requested: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "actor": self.actor,
            "task_id": self.task_id,
            "title": self.title,
            "summary": self.summary,
            "severity": self.severity,
            "classification": self.classification,
            "reporter_kind": self.reporter_kind,
            "evidence_refs": [dict(item) for item in self.evidence_refs],
            "browser_capture_requested": self.browser_capture_requested,
        }


def classify_self_issue_signal(event: ZfEvent) -> SelfIssueSignal | None:
    """Return only mechanically strong ZaoFu-internal incident signals."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.type == "worker.self_issue.detected":
        refs = payload.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            return None
        title = _bounded_text(payload.get("title"), "Worker detected a ZaoFu incident", 240)
        summary = _bounded_text(
            payload.get("summary"),
            "A worker reported a ZaoFu-internal failure with local evidence.",
            1000,
        )
        capture_target = str(payload.get("browser_capture_target") or "")
        return SelfIssueSignal(
            event_id=event.id,
            event_type=event.type,
            actor=str(event.actor or "worker"),
            task_id=str(event.task_id or payload.get("task_id") or ""),
            title=title,
            summary=summary,
            severity=_enum(payload.get("severity"), {"P0", "P1", "P2", "P3"}, "P2"),
            classification=_enum(
                payload.get("classification"),
                {
                    "runtime", "kernel/state", "provider/integration", "web/ui",
                    "configuration", "security", "performance", "test/regression",
                    "unknown",
                },
                "unknown",
            ),
            reporter_kind="worker",
            evidence_refs=tuple(item for item in refs[:20] if isinstance(item, dict)),
            browser_capture_requested=capture_target == "kanban_board",
        )

    spec = _STRONG_KERNEL_SIGNALS.get(event.type)
    if spec is None:
        return None
    severity, title, classification, browser_capture = spec
    summary_parts = []
    for field in _SAFE_SUMMARY_FIELDS:
        value = payload.get(field)
        if value not in (None, "", [], {}):
            summary_parts.append(f"{field}: {_bounded_text(value, 'unknown', 240)}")
    summary = "; ".join(summary_parts) or f"ZaoFu emitted {event.type}."
    return SelfIssueSignal(
        event_id=event.id,
        event_type=event.type,
        actor=str(event.actor or "kernel"),
        task_id=str(event.task_id or ""),
        title=title,
        summary=summary[:1000],
        severity=severity,
        classification=classification,
        reporter_kind="kernel",
        evidence_refs=(),
        browser_capture_requested=browser_capture,
    )


def auto_trigger_from_event(
    *, event: ZfEvent, state_dir: Any, writer: Any, project_root: Any, config: Any,
) -> dict[str, Any] | None:
    """Create/update one local Intake candidate; never creates a Draft or publishes."""
    from zf.runtime.issue_settings import current_issue_policy
    policy = current_issue_policy(project_root, getattr(config, "self_issue", None))
    if not (
        policy is not None
        and bool(getattr(policy, "enabled", False))
        and bool(getattr(policy, "automatic_detection_enabled", True))
    ):
        return None
    signal = classify_self_issue_signal(event)
    if signal is None:
        return None
    from zf.runtime.self_issue_service import SelfIssueService

    return SelfIssueService(
        state_dir, writer, project_root=project_root, policy=policy,
    ).system_detect(signal.to_payload(), causation_id=event.id)


def safe_auto_trigger_from_event(
    event: ZfEvent, state_dir: Any, writer: Any, project_root: Any, config: Any,
) -> dict[str, Any] | None:
    """Keep optional incident reporting from breaking the runtime event loop."""
    try:
        return auto_trigger_from_event(
            event=event,
            state_dir=state_dir,
            writer=writer,
            project_root=project_root,
            config=config,
        )
    except Exception:
        return None


def safe_signal_snapshot(signal: dict[str, Any]) -> dict[str, Any]:
    return redact_obj({
        key: signal.get(key)
        for key in (
            "event_id", "event_type", "actor", "task_id", "title", "summary",
            "severity", "classification", "reporter_kind",
            "browser_capture_requested",
        )
    })


def _bounded_text(value: object, default: str, limit: int) -> str:
    text = str(value or "").strip()
    return (text or default)[:limit]


def _enum(value: object, allowed: set[str], default: str) -> str:
    text = str(value or "")
    return text if text in allowed else default


__all__ = [
    "SelfIssueSignal", "auto_trigger_from_event", "classify_self_issue_signal",
    "safe_auto_trigger_from_event", "safe_signal_snapshot",
]
