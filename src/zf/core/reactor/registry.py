"""EventActionRegistry — event_type → list[handler].

Replaces the hardcoded `_event_handlers()` dict in EventReactorMixin.
Supports:
  - Built-in registration (the ~13 `_on_*` methods, one per event)
  - YAML extension (workflow.event_actions adds emit/log/noop actions
    for custom events without touching Python)
  - Multiple handlers per event (though built-ins register only one)
  - Observability: `handled_events()` for coverage checks

Layer 1 invariant (I24): registry stays pure-Python + deterministic.
No LLM calls, no I/O at registration time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.reactor.actions import (
    ActionContext,
    ActionSpec,
    build_action,
)


log = logging.getLogger(__name__)

HandlerFn = Callable[[ZfEvent], Any]


@dataclass
class RegisteredHandler:
    """A single entry in the registry. Wraps either a built-in method
    (which returns an OrchestratorDecision) or a YAML-declared action
    (which returns None but may emit derived events)."""
    event_type: str
    handler: HandlerFn
    source: str  # "builtin" | "yaml" | "test"


class EventActionRegistry:
    """event_type → list of registered handlers, in registration order."""

    def __init__(self) -> None:
        self._entries: dict[str, list[RegisteredHandler]] = {}

    def register(
        self,
        event_type: str,
        handler: HandlerFn,
        *,
        source: str = "builtin",
    ) -> None:
        """Register a handler for an event type. First handler for an
        event type is the 'primary' (its return value is used by the
        orchestrator decision path). Subsequent handlers are side
        effects (their return values are ignored)."""
        entry = RegisteredHandler(
            event_type=event_type,
            handler=handler,
            source=source,
        )
        self._entries.setdefault(event_type, []).append(entry)

    def resolve(self, event_type: str) -> list[RegisteredHandler]:
        """Return all handlers registered for this event type, in
        registration order. Empty list if none."""
        return list(self._entries.get(event_type, ()))

    def primary(self, event_type: str) -> HandlerFn | None:
        """Return the first (primary) handler for backward compat with
        call sites that expect a single handler. None if none registered."""
        entries = self._entries.get(event_type)
        if not entries:
            return None
        return entries[0].handler

    def handled_events(self) -> set[str]:
        """Return the set of event types with at least one handler."""
        return set(self._entries.keys())

    def load_yaml_actions(
        self,
        event_actions_config: list[dict],
        event_log: EventLog,
    ) -> None:
        """Load `workflow.event_actions` from yaml and register each
        action as a handler.

        Expected shape (one entry per event):
          - event: my.event
            actions:
              - type: emit
                params: {event: derived.event}
              - type: log
                params: {level: info, message: "X fired"}
        """
        ctx = ActionContext(event_log=event_log)
        for entry in event_actions_config or []:
            event_type = entry.get("event")
            if not event_type:
                log.warning("event_actions entry missing 'event' key: %r", entry)
                continue
            for action_dict in entry.get("actions", []) or []:
                spec = ActionSpec(
                    type=action_dict.get("type", ""),
                    params=action_dict.get("params", {}),
                )
                try:
                    action = build_action(spec)
                except ValueError as e:
                    log.warning(
                        "skipping invalid action for %s: %s", event_type, e
                    )
                    continue
                handler = _make_action_handler(action, ctx)
                self.register(event_type, handler, source="yaml")

    def __len__(self) -> int:
        return sum(len(v) for v in self._entries.values())


def _make_action_handler(action: Any, ctx: ActionContext) -> HandlerFn:
    """Wrap an Action instance into a handler function matching the
    reactor's expected signature."""
    def handler(event: ZfEvent) -> None:
        action.apply(event, ctx)
        return None
    return handler
