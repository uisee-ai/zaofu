"""ZF-PWF-SESSION-ISO-001 — operator session-scoped target resolver.

Entry points (``zf chat``, web ``/api/chat``, Feishu sidecar) call
:func:`resolve_active_target` to figure out which task / dispatch
the operator is currently working with. The resolver implements a
4-level priority ladder and fails closed when nothing
unambiguously matches.
"""

from __future__ import annotations

from dataclasses import dataclass

from zf.core.state.operator_sessions import (
    OperatorSession,
    OperatorSessionRegistry,
)


@dataclass(frozen=True)
class TargetAmbiguous:
    """Sentinel returned when no unambiguous target resolves.

    ``candidates`` lists the existing operator-session bindings so the
    caller can render a "pick one" UI. ``reason`` summarises why
    resolution failed (no session, multiple active sessions, etc.) so
    error messages can be specific.
    """

    candidates: list[OperatorSession]
    reason: str


def resolve_active_target(
    *,
    registry: OperatorSessionRegistry,
    operator_session_id: str | None,
    explicit_task_id: str | None = None,
    explicit_run_id: str | None = None,
) -> OperatorSession | TargetAmbiguous:
    """Resolve the operator's current active target.

    Priority ladder (top wins, fall through on miss):

    1. **Explicit task / run id** supplied by the entry point
       (``--task`` / ``x-zf-task-id`` header). Builds an ad-hoc
       :class:`OperatorSession` from the explicit hint plus whatever
       the operator session has on file.
    2. **Operator session binding** — ``operator_session_id`` is
       known and has a registry entry.
    3. **Single active session in the registry** — if exactly one
       binding exists in total, return it (degenerate
       single-campaign install).
    4. **Fail closed** — return :class:`TargetAmbiguous` with all
       candidates so the caller can prompt for disambiguation.

    No fallback heuristic guesses ``last-active worker`` or
    ``most-recent task`` — closing this surface is the whole point.
    """
    # Level 1: explicit task / run id wins outright.
    if explicit_task_id or explicit_run_id:
        base: OperatorSession | None = None
        if operator_session_id:
            base = registry.resolve(operator_session_id)
        if base is not None:
            return _with_explicit_overrides(
                base, explicit_task_id, explicit_run_id
            )
        return OperatorSession(
            operator_session_id=operator_session_id or "",
            source="explicit",
            task_id=explicit_task_id or "",
            run_id=explicit_run_id or "",
        )

    # Level 2: known operator session.
    if operator_session_id:
        session = registry.resolve(operator_session_id)
        if session is not None:
            return session

    # Level 3: degenerate single-active fallback.
    bindings = registry.list_all()
    if len(bindings) == 1:
        return bindings[0]

    # Level 4: fail closed.
    if not bindings:
        reason = "no_operator_session_bound"
    else:
        reason = "multiple_operator_sessions_active"
    return TargetAmbiguous(candidates=bindings, reason=reason)


def _with_explicit_overrides(
    base: OperatorSession,
    explicit_task_id: str | None,
    explicit_run_id: str | None,
) -> OperatorSession:
    """Layer explicit task/run id over a known session binding."""
    from dataclasses import replace

    return replace(
        base,
        task_id=explicit_task_id or base.task_id,
        run_id=explicit_run_id or base.run_id,
    )
