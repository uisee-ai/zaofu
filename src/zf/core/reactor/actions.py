"""Reactor Action abstraction — YAML-declared side effects that fire
when a registered event arrives.

Kept intentionally narrow (emit / log / noop). More complex actions
(move_task / dispatch_role) stay in the existing `_on_*` handlers for
now; once those are fully migrated the set here can grow.

Layer 1 invariant (I24): actions are pure deterministic functions of
(event, context). No LLM calls, no ambient state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


log = logging.getLogger(__name__)


@dataclass
class ActionContext:
    """State an action can read/write. Passed to `apply()` at call time."""
    event_log: EventLog


@dataclass
class ActionSpec:
    """YAML-parseable action description.

    Example YAML:
      - event: my.custom.event
        actions:
          - type: emit
            event: task.notification
          - type: log
            level: warning
            message: "{event.type} fired for {event.task_id}"
    """
    type: str  # emit / log / noop
    params: dict[str, Any] = field(default_factory=dict)


# -- Concrete actions --

@dataclass
class EmitAction:
    """Emit a derived event. Common case: custom YAML routes event X
    into an internal event Y that existing handlers know how to react
    to — effectively letting YAML chain to built-in logic."""
    target_event: str
    payload_template: dict[str, Any] = field(default_factory=dict)

    def apply(self, event: ZfEvent, ctx: ActionContext) -> None:
        derived = ZfEvent(
            type=self.target_event,
            actor="reactor",
            task_id=event.task_id,
            causation_id=event.id,
            payload=dict(self.payload_template),
        )
        try:
            ctx.event_log.append(derived)
        except Exception as e:
            log.warning("EmitAction failed for %s: %s", self.target_event, e)


@dataclass
class LogAction:
    """Record an observation to the zf logger. Useful for YAML-driven
    diagnostics without emitting new events."""
    level: str = "info"  # debug / info / warning / error
    message: str = ""

    def apply(self, event: ZfEvent, ctx: ActionContext) -> None:
        level_fn = getattr(log, self.level.lower(), log.info)
        try:
            msg = self.message.format(event=event)
        except (AttributeError, KeyError, IndexError):
            msg = f"{self.message} ({event.type})"
        level_fn(msg)


@dataclass
class NoOpAction:
    """Observe without reacting. Registering NoOp for an event is how
    YAML says 'I want this event in wake_patterns so orchestrator wakes
    and checks general state, but don't do anything specific'."""

    def apply(self, event: ZfEvent, ctx: ActionContext) -> None:
        return None


def build_action(spec: ActionSpec) -> Any:
    """Construct an Action instance from a YAML-parsed spec."""
    t = spec.type.lower()
    p = spec.params or {}
    if t == "emit":
        target = p.get("event")
        if not target:
            raise ValueError("emit action requires 'event' param")
        return EmitAction(
            target_event=target,
            payload_template=p.get("payload", {}),
        )
    if t == "log":
        return LogAction(
            level=p.get("level", "info"),
            message=p.get("message", ""),
        )
    if t == "noop":
        return NoOpAction()
    raise ValueError(f"Unknown action type: {spec.type!r}")


HandlerFn = Callable[[ZfEvent], Any]
