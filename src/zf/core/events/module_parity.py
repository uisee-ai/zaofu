"""Canonical module parity / verify-rescan event names.

Runtime code must use the generic event names below for newly generated
workflow events. The Cangjie-prefixed names are accepted only as legacy aliases
so historical runs remain readable and resumable.

Legacy alias retirement condition: the ``cangjie.*`` aliases (here, in
``known_types``, wake patterns, event contracts, and the problem registry) may
be removed once no retained ``events.jsonl`` needs kernel replay/resume —
i.e. the cangjie-mono / cj-min run archives are frozen as documents rather
than resumable state. Until then they stay read-side only: nothing may mint
new events under the legacy names.
"""

from __future__ import annotations

MODULE_PARITY_SCAN_REQUESTED = "verify.parity_scan.requested"
MODULE_PARITY_SCAN_COMPLETED = "module.parity.scan.completed"
MODULE_PARITY_SCAN_FAILED = "module.parity.scan.failed"
MODULE_PARITY_CLOSED = "module.parity.closed"
MODULE_PARITY_BLOCKED = "module.parity.blocked"

LEGACY_CANGJIE_MODULE_PARITY_SCAN_COMPLETED = (
    "cangjie.module.parity.scan.completed"
)
LEGACY_CANGJIE_MODULE_PARITY_SCAN_FAILED = "cangjie.module.parity.scan.failed"

MODULE_PARITY_SCAN_COMPLETED_EVENTS = frozenset({
    MODULE_PARITY_SCAN_COMPLETED,
    LEGACY_CANGJIE_MODULE_PARITY_SCAN_COMPLETED,
})
MODULE_PARITY_SCAN_FAILED_EVENTS = frozenset({
    MODULE_PARITY_SCAN_FAILED,
    LEGACY_CANGJIE_MODULE_PARITY_SCAN_FAILED,
})
MODULE_PARITY_SCAN_RESULT_EVENTS = frozenset({
    *MODULE_PARITY_SCAN_COMPLETED_EVENTS,
    *MODULE_PARITY_SCAN_FAILED_EVENTS,
})


def is_module_parity_scan_completed_event(event_type: str) -> bool:
    return event_type in MODULE_PARITY_SCAN_COMPLETED_EVENTS


def is_module_parity_scan_failed_event(event_type: str) -> bool:
    return event_type in MODULE_PARITY_SCAN_FAILED_EVENTS


def is_module_parity_scan_result_event(event_type: str) -> bool:
    return event_type in MODULE_PARITY_SCAN_RESULT_EVENTS
