"""ZF-LH-INLINE-001 — user-message inline override scanner.

Operator can include emergency override keywords inside a
``user.message`` event payload to skip a pipeline stage for the next
dispatched task. The scanner here is the pure-function half: given an
event + config, return the list of matched override keys.

Wiring (audit emission, dispatch enforcement) is intentionally
separate — keeps this module side-effect free and unit-testable.

Discipline enforced here:

- Only events whose ``actor`` is in ``_HUMAN_ACTORS`` produce matches.
  Agent-emitted events that quote a keyword don't trigger.
- Only events whose ``type`` is ``user.message`` are scanned.
- When ``config.workflow.inline_overrides.enabled`` is ``False``, the
  scanner short-circuits — returns empty list, no work done.
- Matching is case-insensitive substring match against
  ``payload.text`` / ``payload.message`` / ``payload.content``
  (whichever is present, in that priority order).
"""

from __future__ import annotations

from dataclasses import dataclass

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent


_HUMAN_ACTORS: frozenset[str] = frozenset({"user", "zf-cli"})


@dataclass(frozen=True)
class InlineOverrideMatch:
    """One inline override match. ``key`` corresponds to a key in
    ``WorkflowInlineOverrides.patterns`` (e.g. ``skip_critic``);
    ``trigger_phrase`` is the literal text that matched."""

    key: str
    trigger_phrase: str


def _extract_message_text(event: ZfEvent) -> str:
    """Pick the first non-empty text-bearing field from the event
    payload. ``user.message`` events have historically used
    ``text`` / ``message`` / ``content`` interchangeably."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    for key in ("text", "message", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def scan_inline_overrides(
    event: ZfEvent,
    config: ZfConfig,
) -> list[InlineOverrideMatch]:
    """Return all override matches for a user.message event.

    Returns an empty list (no work, no audit) when:
    - inline_overrides feature is disabled in zf.yaml
    - event.type is not ``user.message``
    - event.actor is not a human actor (closes the "quote attack" gap)
    - no message text payload field is set
    - no pattern matches
    """
    overrides = getattr(
        getattr(config, "workflow", None), "inline_overrides", None
    )
    if overrides is None or not overrides.enabled:
        return []

    if event.type != "user.message":
        return []

    actor = (event.actor or "").lower()
    if actor not in _HUMAN_ACTORS:
        return []

    text = _extract_message_text(event)
    if not text:
        return []

    haystack = text.casefold()
    matches: list[InlineOverrideMatch] = []
    for key, phrases in (overrides.patterns or {}).items():
        if not isinstance(phrases, list):
            continue
        for phrase in phrases:
            if not isinstance(phrase, str) or not phrase:
                continue
            if phrase.casefold() in haystack:
                matches.append(
                    InlineOverrideMatch(key=key, trigger_phrase=phrase)
                )
                break  # one phrase per key is enough — don't double-count
    return matches


def build_audit_payload(
    event: ZfEvent,
    matches: list[InlineOverrideMatch],
) -> dict[str, object]:
    """Build the audit-event payload for a successful scan.

    Caller writes this to events.jsonl as a ``workflow.inline_override``
    event. ``human_initiated`` is always ``True`` — the scanner
    refuses non-human actors above, so any audit payload coming out of
    this module corresponds to an operator action.
    """
    return {
        "source_event_id": event.id,
        "source_event_type": event.type,
        "source_actor": event.actor,
        "matched_keys": [m.key for m in matches],
        "trigger_phrases": [m.trigger_phrase for m in matches],
        "human_initiated": True,
    }
