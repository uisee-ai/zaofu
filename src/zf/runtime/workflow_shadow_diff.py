"""Shadow-diff helpers for WorkflowGraph strangler migrations.

The helpers compare semantic runtime output, not provider-specific audit
decorations. P7 uses this before moving one legacy branch at a time behind a
graph action flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zf.core.events.model import ZfEvent


_STATIC_GATE_PAYLOAD_KEYS: tuple[str, ...] = (
    "passed",
    "skipped",
    "skip_reason",
    "check_count",
    "failed_count",
    "failed_commands",
    "trigger_event_id",
    "trigger_event_type",
    "dispatch_id",
    "workdir",
)

_STATIC_GATE_CHECK_KEYS: tuple[str, ...] = (
    "command",
    "exit_code",
    "passed",
    "output",
    "gate_name",
)


@dataclass(frozen=True)
class WorkflowShadowDiff:
    subject: str
    expected: dict[str, Any]
    actual: dict[str, Any]
    mismatches: tuple[str, ...]

    @property
    def matches(self) -> bool:
        return not self.mismatches

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "matches": self.matches,
            "expected": self.expected,
            "actual": self.actual,
            "mismatches": list(self.mismatches),
        }


def static_gate_event_signature(event: ZfEvent) -> dict[str, Any]:
    """Return the semantic signature used to compare static gate paths."""

    payload = event.payload if isinstance(event.payload, dict) else {}
    signature_payload = {
        key: payload.get(key)
        for key in _STATIC_GATE_PAYLOAD_KEYS
        if key in payload
    }
    checks = payload.get("checks")
    if isinstance(checks, list):
        signature_payload["checks"] = [
            {
                key: item.get(key)
                for key in _STATIC_GATE_CHECK_KEYS
                if isinstance(item, dict) and key in item
            }
            for item in checks
        ]
    return {
        "type": event.type,
        "task_id": event.task_id or "",
        "causation_id": event.causation_id or "",
        "correlation_id": event.correlation_id or "",
        "payload": signature_payload,
    }


def compare_static_gate_shadow_events(
    *,
    expected: ZfEvent,
    actual: ZfEvent,
) -> WorkflowShadowDiff:
    """Compare legacy static gate output with graph action output."""

    expected_signature = static_gate_event_signature(expected)
    actual_signature = static_gate_event_signature(actual)
    mismatches = _diff_dict("", expected_signature, actual_signature)
    return WorkflowShadowDiff(
        subject="static_gate",
        expected=expected_signature,
        actual=actual_signature,
        mismatches=tuple(mismatches),
    )


def _diff_dict(
    prefix: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> list[str]:
    mismatches: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        path = f"{prefix}.{key}" if prefix else key
        if key not in expected:
            mismatches.append(f"{path}: unexpected actual field")
            continue
        if key not in actual:
            mismatches.append(f"{path}: missing actual field")
            continue
        left = expected[key]
        right = actual[key]
        if isinstance(left, dict) and isinstance(right, dict):
            mismatches.extend(_diff_dict(path, left, right))
        elif left != right:
            mismatches.append(f"{path}: expected {left!r}, got {right!r}")
    return mismatches
