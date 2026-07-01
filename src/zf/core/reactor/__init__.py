"""Reactor event/action registry — replaces the hardcoded
`_event_handlers()` dict with a registerable mapping so YAML can add
event → action bindings without touching Python.

Pre-2026-04-20 reactor used a hardcoded dict in `EventReactorMixin`,
which meant events a user's custom YAML `role.publishes` would be
silently ignored (no handler, no dispatch). The registry closes that
gap by letting YAML append actions for custom events.
"""

from __future__ import annotations

from zf.core.reactor.actions import (
    ActionContext,
    ActionSpec,
    EmitAction,
    LogAction,
    NoOpAction,
    build_action,
)
from zf.core.reactor.registry import EventActionRegistry

__all__ = [
    "ActionContext",
    "ActionSpec",
    "EmitAction",
    "EventActionRegistry",
    "LogAction",
    "NoOpAction",
    "build_action",
]
