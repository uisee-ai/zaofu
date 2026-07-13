"""Drift detector — 5 signals for agent quality degradation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


_OBSERVATION_EVENT_TYPES = {
    "agent.usage",
    "orchestrator.dispatch_skipped",
    "orchestrator.round.complete",
    "skills.materialized",
    "worker.context.warning",
    "worker.drift.detected",
    "worker.refresh.triggered",
    "worker.state.changed",
    # Codex/Claude hook events are high-volume runtime observations. A normal
    # turn often emits many pre/post tool hooks while the agent reads briefings,
    # skills, and tests; that is activity, not repeat decision drift.
    "codex.hook.pre_tool_use",
    "codex.hook.post_tool_use",
    "codex.hook.user_prompt_submit",
    "codex.hook.session_start",
    "codex.hook.stop",
    # B-NEW-5 (2026-05-16): kernel synthesis events that legitimately
    # repeat during fanout (one per task/feature) and must not trigger
    # drift refresh. P0 stage ④ orchestrator-driven backlog synthesis
    # produces N task.contract.update for N-way user.message fanout —
    # without this filter, 4-way fanout was misclassified as
    # repeat_decisions and refresh loop blocked the entire pipeline.
    "task.contract.update",
}

_INFRA_ACTORS = {
    # Drift `repeat_decisions` means an *agent* repeatedly making the same
    # decision. Infrastructure actors emit periodic/loop events by design
    # (run-manager ticks, supervisor attention, autoresearch loops, stall
    # redispatch) that legitimately repeat every few events — counting them
    # floods `worker.drift.detected` (2026-07-08 E2E: 80-120 false drifts per
    # fanout run, all "run.manager.tick.started by run-manager repeated 3x",
    # each waking the orchestrator + feeding supervisor/autoresearch noise).
    # Only zf-cli was excluded historically; the runtime grew more infra
    # actors that must all be excluded. Role-instance workers are NOT here —
    # their genuine repeat-decision loops are still detected.
    "zf-cli",
    "run-manager",
    "zf-supervisor",
    "zf-runtime",
    "zf-autoresearch",
    "zf-stall-redispatch",
    "operator",
}


@dataclass
class DriftSignal:
    signal: str  # repeat_decisions, diversity_drop, node_skip, escalate_anomaly, thrashing
    severity: str  # low, medium, high
    detail: str
    recommended_action: str  # refresh, escalate, split_task
    affected_role: str = ""


class DriftDetector:
    """Detect drift in agent behavior from events and pane output."""

    def __init__(
        self,
        repeat_threshold: int = 3,
        thrash_threshold: int = 3,
    ) -> None:
        self.repeat_threshold = repeat_threshold
        self.thrash_threshold = thrash_threshold

    def check(
        self,
        events: list[dict],
        *,
        expected_roles: list[str] | None = None,
    ) -> list[DriftSignal]:
        """Check for drift signals. Events are dicts with at least 'type' key."""
        signals: list[DriftSignal] = []

        signals.extend(self._check_repeat_decisions(events))
        signals.extend(self._check_thrashing(events))
        if expected_roles:
            signals.extend(self._check_node_skip(events, expected_roles))
        signals.extend(self._check_escalate_anomaly(events))

        return signals

    def _check_repeat_decisions(self, events: list[dict]) -> list[DriftSignal]:
        """Same actor repeatedly emits the same decision-shaped event."""
        if len(events) < self.repeat_threshold * 2:
            return []
        recent_keys = [
            (str(e.get("type", "")), str(e.get("actor", "")))
            for e in events[-20:]
            if not str(e.get("type", "")).startswith("agent.")
            and e.get("type") not in _OBSERVATION_EVENT_TYPES
            and str(e.get("actor", "")) not in _INFRA_ACTORS
            # B-NEW-5 (2026-05-16): events with empty/None actor are
            # kernel synthesis events (e.g. task.contract.update written
            # by orchestrator stage ④ pipeline), not agent decisions.
            # The drift signal "repeat_decisions" by definition means
            # *the same agent* repeatedly making the same decision — so
            # an event without an agent attribution should never count.
            and e.get("actor") not in (None, "")
        ]
        counts = Counter(recent_keys)
        for (event_type, actor), count in counts.items():
            if count >= self.repeat_threshold and event_type:
                actor_suffix = f" by {actor}" if actor else ""
                return [DriftSignal(
                    signal="repeat_decisions",
                    severity="medium",
                    detail=(
                        f"{event_type}{actor_suffix} repeated {count} times "
                        "in last 20 events"
                    ),
                    recommended_action="refresh",
                )]
        return []

    def _check_thrashing(self, events: list[dict]) -> list[DriftSignal]:
        """Same task rejected >= threshold times."""
        reject_counts: Counter[str] = Counter()
        for e in events[-30:]:
            if e.get("type") == "review.rejected" and e.get("task_id"):
                reject_counts[e["task_id"]] += 1

        for task_id, count in reject_counts.items():
            if count >= self.thrash_threshold:
                return [DriftSignal(
                    signal="thrashing",
                    severity="high",
                    detail=f"Task {task_id} rejected {count} times",
                    recommended_action="split_task",
                )]
        return []

    def _check_node_skip(self, events: list[dict], expected_roles: list[str]) -> list[DriftSignal]:
        """Expected role not triggered in recent events.

        Requires at least 10 events to avoid cold-start false positives:
        a fresh harness with only `loop.started` in the log shouldn't
        flag every worker role as missing.
        """
        if len(events) < 10:
            return []
        recent_actors = {str(e.get("actor", "")) for e in events[-30:]}
        for role in expected_roles:
            if role == "orchestrator":
                continue
            if _role_seen(role, recent_actors):
                continue
            if role not in recent_actors:
                return [DriftSignal(
                    signal="node_skip",
                    severity="low",
                    detail=f"Role '{role}' not active in recent events",
                    recommended_action="refresh",
                    affected_role=role,
                )]
        return []

    def _check_escalate_anomaly(self, events: list[dict]) -> list[DriftSignal]:
        """Never or always escalating."""
        if len(events) < 10:
            return []
        recent = events[-20:]
        escalate_count = sum(1 for e in recent if e.get("type") == "human.escalate")
        ratio = escalate_count / len(recent)
        if ratio > 0.5:
            return [DriftSignal(
                signal="escalate_anomaly",
                severity="medium",
                detail=f"Escalation rate {ratio:.0%} — over-escalating",
                recommended_action="refresh",
            )]
        return []


def _role_seen(role: str, actors: set[str]) -> bool:
    """Return true when a base role or one of its replicas is active."""
    if role in actors:
        return True
    prefix = f"{role}-"
    return any(actor.startswith(prefix) for actor in actors)
