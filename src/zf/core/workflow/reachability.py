"""Stage-event producibility check (ZF-E2E-RACING-P2, 2026-07-11).

Racing e2e root cause: stage_order declared review.approved / test.passed /
judge.passed, but no role had any ``triggers`` — the dispatch loop skips
roles whose triggers don't match the event (orchestrator_dispatch:
``event_type not in role.triggers``), so once the kernel-run static gate
passed, nothing could ever be woken to publish the next stage event.
``zf validate --cold-start`` scored 5/5: topology's orphan_events is
consumer-side and warning-only, and nothing checked producibility.

This module computes the fixpoint of "which events can this configuration
ever produce" and reports declared stage_order events outside it.

Mechanism model (kernel behaviors, no project knowledge):

- seed: ``task.assigned`` — the backlog scheduler assigns without any role
  trigger.
- entry stage: the first stage_order event published by a role is reachable
  through backlog dispatch itself (the scheduler briefs the assigned role
  directly), so its publishers fire trigger-free. Every later stage is
  trigger-driven.
- kernel transform: ``dev.build.done`` → ``static_gate.{passed,failed,
  skipped}`` — the static gate runner is kernel-owned.
- a role fires when any of its triggers is producible; firing makes all of
  its publishes producible.

Scope (fail-open by class, fail-closed within it): the model is only
faithful for trigger-driven task-model configs — exactly the class that
died in the racing e2e. Two other stage_order vocabularies are excluded:

- stage LABELS (``intake``/``implement``/``done`` — no dot): a different
  naming layer, not event types; the trigger model does not apply.
- fanout topologies (``task_map.ready``/``candidate.ready`` markers or
  roles publishing ``*.child.*``): the fanout runtime dispatches lanes and
  aggregates stage events without role triggers, so static role-trigger
  reachability cannot judge them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_SEED_EVENTS = frozenset({"task.assigned"})
_KERNEL_TRANSFORMS: dict[str, frozenset[str]] = {
    "dev.build.done": frozenset({
        "static_gate.passed",
        "static_gate.failed",
        "static_gate.skipped",
    }),
}
# Kernel fanout runtime contract events: their presence means lane dispatch
# and stage aggregation are fanout-owned, outside the role-trigger model.
_FANOUT_MARKER_EVENTS = frozenset({"task_map.ready", "candidate.ready"})


@dataclass(frozen=True)
class StageReachabilityReport:
    """unproducible_stage_events is the fatal set: declared in stage_order,
    impossible to ever produce under the configured trigger topology."""

    producible: frozenset[str] = frozenset()
    unproducible_stage_events: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unproducible_stage_events


def check_stage_reachability(config) -> StageReachabilityReport:
    workflow = getattr(config, "workflow", None)
    dag = getattr(workflow, "dag", None)
    if not bool(getattr(dag, "enabled", False)):
        return StageReachabilityReport()
    # Only dotted entries are event types; stage LABELS (intake/implement/
    # done) belong to a different vocabulary the trigger model cannot judge.
    stage_order = [
        str(ev)
        for ev in (getattr(dag, "stage_order", None) or [])
        if str(ev) and "." in str(ev)
    ]
    roles = list(getattr(config, "roles", None) or [])
    if not stage_order or not roles:
        return StageReachabilityReport()

    publishers: dict[str, list] = {}
    for role in roles:
        for ev in getattr(role, "publishes", None) or []:
            publishers.setdefault(str(ev), []).append(role)

    # Fanout topologies dispatch lanes / aggregate stage events without role
    # triggers — static role-trigger reachability cannot judge them.
    if _FANOUT_MARKER_EVENTS & set(stage_order):
        return StageReachabilityReport()
    if any(".child." in ev for ev in publishers):
        return StageReachabilityReport()

    producible: set[str] = set(_SEED_EVENTS)
    fired: set[str] = set()

    # Entry stage: first role-published stage event — its publishers are
    # briefed by the backlog scheduler without needing triggers.
    for ev in stage_order:
        if ev in _SEED_EVENTS:
            continue
        if any(ev in outs for outs in _KERNEL_TRANSFORMS.values()):
            continue
        for role in publishers.get(ev, []):
            fired.add(getattr(role, "name", ""))
            producible.update(
                str(out) for out in (getattr(role, "publishes", None) or [])
            )
        break

    changed = True
    while changed:
        changed = False
        for src, outs in _KERNEL_TRANSFORMS.items():
            if src in producible and not outs <= producible:
                producible |= outs
                changed = True
        for role in roles:
            name = getattr(role, "name", "")
            if name in fired:
                continue
            triggers = {
                str(t) for t in (getattr(role, "triggers", None) or [])
            }
            if triggers & producible:
                fired.add(name)
                producible.update(
                    str(out) for out in (getattr(role, "publishes", None) or [])
                )
                changed = True

    unproducible = [ev for ev in stage_order if ev not in producible]
    return StageReachabilityReport(
        producible=frozenset(producible),
        unproducible_stage_events=unproducible,
    )
