"""ZF-ROLECTX-001 — explicit role context dimension (doc 39 §4.5).

A role/instance can serve multiple semantic functions across a
campaign: ``coordinator`` vs ``worker`` vs ``reviewer`` vs
``verifier`` vs ``judge`` vs ``synthesizer``. zaofu used to
infer this from event source / role.name, which mis-attributes
events when (a) the same role.name plays different functions in
different stages, or (b) coordinator-on-leader-task
patterns appear.

This module declares the canonical enum and provides a small
helper to compute ``role_context`` from ``RoleConfig.role_kind`` +
event type (best-effort, used only as a hint — never to mutate
truth).
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable


class RoleContext(str, Enum):
    """Explicit role context (doc 39 §4.5).

    String values stable for serialization into events / State
    Packet payload. Forward-compat: callers MUST tolerate values
    outside this enum (future contexts).
    """

    COORDINATOR = "coordinator"
    PLANNER = "planner"
    WORKER = "worker"
    REVIEWER = "reviewer"
    VERIFIER = "verifier"
    JUDGE = "judge"
    SYNTHESIZER = "synthesizer"
    UNKNOWN = ""


_ROLE_NAME_HINTS: dict[str, RoleContext] = {
    "orchestrator": RoleContext.COORDINATOR,
    "arch": RoleContext.PLANNER,
    "dev": RoleContext.WORKER,
    "review": RoleContext.REVIEWER,
    "test": RoleContext.VERIFIER,
    "judge": RoleContext.JUDGE,
    "critic": RoleContext.REVIEWER,
}


def infer_role_context(
    *,
    role_name: str,
    role_kind: str = "",
    fanout_role: bool = False,
) -> RoleContext:
    """Best-effort inference of role_context from role.name +
    role.role_kind + fanout-membership flag.

    Hierarchy (highest wins):
    1. fanout child → ``worker`` (regardless of role.name)
    2. role.name hint via ``_ROLE_NAME_HINTS``
    3. role_kind=writer → worker; role_kind=reader → reviewer
    4. fallback ``unknown``
    """
    if fanout_role and role_name in _ROLE_NAME_HINTS:
        # Fanout children always act as workers even if the role
        # template is otherwise a reviewer / planner.
        return RoleContext.WORKER
    if role_name in _ROLE_NAME_HINTS:
        return _ROLE_NAME_HINTS[role_name]
    kind = (role_kind or "").lower()
    if kind == "writer":
        return RoleContext.WORKER
    if kind == "reader":
        return RoleContext.REVIEWER
    return RoleContext.UNKNOWN


def known_role_contexts() -> Iterable[str]:
    """All declared RoleContext string values (sans the empty UNKNOWN)."""
    return tuple(
        rc.value for rc in RoleContext if rc is not RoleContext.UNKNOWN
    )
