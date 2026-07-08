"""Workflow topology — derive DAG from roles' triggers/publishes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from zf.core.config.schema import ZfConfig


# Events produced by kernel / external sources (CLI, hooks, Layer 1
# housekeeping), not by any role.publishes declaration. When checking
# topology integrity these should be excluded from "orphan" / "dead-end"
# sets — their producer/consumer lives outside the role graph.
EXTERNAL_EVENTS: frozenset[str] = frozenset({
    # User / human input
    "user.message", "human.escalate", "human.resolved", "human.note",
    # Kernel lifecycle
    "session.started", "loop.started", "loop.stopped",
    "loop.shutdown_requested", "loop.pause_requested", "loop.resume_requested",
    # Kernel-derived task events
    "task.created", "task.assigned", "task.dispatched",
    "task.status_changed", "task.files_touched", "task.contract.update",
    "task.contract.invalid", "task.cancel_requested", "task.retry_requested",
    "task.rework.capped", "task.requeued", "task.invalid_transition",
    "task.orphan_warning", "task.orphaned", "task.split_quality.blocked",
    # Kernel-derived feature events
    "feature.created", "feature.deleted", "feature.status_changed",
    "feature.decomposed", "feature.liveness.blocked",
    # Kernel-derived worker observability
    "worker.state.changed", "worker.stuck", "worker.stuck.recovered",
    "worker.stuck.recovery_failed", "worker.spawn_warning",
    "worker.refresh.triggered", "worker.recycled", "worker.recycling",
    "worker.recycle.failed", "worker.respawned", "worker.respawn.failed",
    "worker.restarted", "worker.context.warning", "worker.context.critical",
    "worker.policy.applied",
    "worker.runner.failed", "worker.drift.detected", "dispatch.silent_stall",
    # Kernel-derived scope / cost / GAN signals
    "scope.violation", "cost.budget.exceeded",
    "gan.round.started", "gan.round.completed",
    # Runtime fanout control outcomes
    "fanout.serialize", "fanout.cancelled", "fanout.timed_out",
    # Verification layer (produced by Layer 1 housekeeping)
    "discriminator.passed", "discriminator.failed",
    "gate.passed", "gate.failed",
    "static_gate.passed", "static_gate.failed", "static_gate.skipped",
    # Kernel-consumed role progress events. In strict DAG these are not
    # role-to-role edges: dev.build.done is consumed by static_gate, and
    # judge.passed is consumed by terminal done evaluation.
    "dev.build.done", "judge.passed",
    # Orchestrator-internal
    "orchestrator.dispatch_failed", "orchestrator.dispatch_skipped",
    "orchestrator.round.complete", "orchestrator.idle",
    # LH-3 Tri-State (suspended is published by review/test but the
    # clarification.needed flow is initiated by arch, so list here)
    "clarification.needed",
    # Hook subsystem (events injected via hook_recv CLI)
    "claude.hook.pre_tool_use", "claude.hook.post_tool_use", "claude.hook.stop",
    "codex.hook.session_start", "codex.hook.user_prompt_submit",
    "codex.hook.pre_tool_use", "codex.hook.post_tool_use", "codex.hook.stop",
    "hook.write_failed", "hook.orphan_event",
    # LH-4 error taxonomy
    "circuit.tripped", "circuit.closed", "role.suspended.circuit",
    # Agent telemetry (stream-json / tailer derived)
    "agent.thinking", "agent.text", "agent.tool.use", "agent.tool.result",
    "agent.usage", "agent.api_blocked", "agent.timeout",
    # Autoresearch supervisor diagnostic events
    "autoresearch.inject.worker_stuck",
    # Memory / progress
    "memory.note", "progress.note",
    # Misc
    "handoff.generated", "event.malformed",
})


@dataclass
class TopologyReport:
    """Result of a topology check against reactor handlers + wake_patterns."""

    orphan_events: list[str]
    dead_end_roles: list[str]
    unhandled_events: list[str]   # role.publishes but reactor has no handler
    unused_handlers: list[str]    # reactor handles event but no role publishes it
    unwoken_events: list[str]     # reactor handles event but not in wake_patterns

    def has_issues(self) -> bool:
        return bool(
            self.orphan_events or self.dead_end_roles
            or self.unhandled_events or self.unwoken_events
        )


_SUCCESS_SUFFIXES: frozenset[str] = frozenset({
    "done", "passed", "approved", "completed",
})
_FAILURE_SUFFIXES: frozenset[str] = frozenset({
    "failed", "rejected", "blocked",
})


@dataclass(frozen=True)
class WorkflowEventSets:
    """Single source of truth for pipeline-event classification.

    Previously these 4 frozensets were scattered across
    ``orchestrator_dispatch.py`` (3 sets) and ``rework_triage.py`` (1 set),
    causing the B-NEW-4 / B-NEW-9 / B-NEW-10 class of bug: adding a new
    pipeline stage event (e.g. ``static_gate.passed``) required editing
    several files; missing one made handoff silently strand.

    PREREQ-B (2026-05-18): a single baseline + cross-check against
    ``WorkflowTopology`` lets ``zf validate`` flag drift when a role
    publishes a success/failure-shaped event that is not yet classified.
    """

    handoff_success_events: frozenset[str]
    stage_progress_events: frozenset[str]
    rework_trigger_events: frozenset[str]
    rework_triage_trigger_events: frozenset[str]

    @classmethod
    def baseline(cls) -> "WorkflowEventSets":
        """Canonical baseline. Edit this once when adding a new pipeline
        event; all 4 historical hardcodes derive from it.
        """
        handoff_success = frozenset({
            "arch.proposal.done",
            "design.critique.done",
            "dev.build.done",
            # Controller/profile workflows close writer task slices with the
            # canonical stage-child event instead of legacy dev.build.done.
            "impl.child.completed",
            # B-NEW-4: P3 added static_gate as an independent DAG stage
            # between dev and review; without listing static_gate.passed
            # here the reconciler never auto-routes the handoff.
            "static_gate.passed",
            "review.approved",
            "verify.passed",
            "test.passed",
            "judge.passed",
        })
        rework_trigger = frozenset({
            "review.rejected",
            "verify.failed",
            "test.failed",
            "judge.failed",
            "gate.failed",
            "discriminator.failed",
            "task.done.blocked",
            # cangjie-mono drift fix (2026-05-18): dev publishes
            # dev.blocked when worker hits a blocker mid-build. Was
            # missing from baseline → I57 cross_check_topology
            # surfaced it on `zf validate --cold-start` of
            # cangjie-mono. Adding here closes the drift loop.
            "dev.blocked",
            # Affinity fanout runs publish task-scoped lane failures
            # directly. Treat them like canonical stage failures so the
            # reconciler can route bounded rework instead of stranding the
            # task after the child event is recorded.
            "dev.failed",
            "impl.child.failed",
            "review.child.failed",
            "verify.child.failed",
        })
        # rework_triage covers rework_trigger plus static_gate.failed
        # (P3/K5: rework_routing handler classifies as product_issue).
        rework_triage_trigger = rework_trigger | frozenset({"static_gate.failed"})
        equivalent_progress = frozenset({
            # Disabled / per-task-skipped static gate is still a kernel stage
            # result. It is not a required "success" event for audits, but the
            # reconciler must inspect it and route passed skips as equivalent
            # to static_gate.passed.
            "static_gate.skipped",
        })
        return cls(
            handoff_success_events=handoff_success,
            stage_progress_events=handoff_success | rework_trigger | equivalent_progress,
            rework_trigger_events=rework_trigger,
            rework_triage_trigger_events=rework_triage_trigger,
        )

    def cross_check_topology(
        self, topology: "WorkflowTopology"
    ) -> list[str]:
        """Detect drift between the baseline and what roles actually publish.

        Returns a list of human-readable drift descriptions. Empty list
        means the baseline covers every success/failure-suffixed event
        the topology produces.

        Heuristic: events whose final dotted segment is in
        ``_SUCCESS_SUFFIXES`` (done/passed/approved/completed) should be
        in ``handoff_success_events``; final segment in
        ``_FAILURE_SUFFIXES`` (failed/rejected/blocked) should be in
        ``rework_trigger_events``. Anything else is silently ignored
        (notes, hooks, telemetry, lifecycle events).
        """
        drift: list[str] = []
        published = set(topology._published)
        all_classified = (
            self.handoff_success_events
            | self.rework_trigger_events
            | self.rework_triage_trigger_events
        )
        for evt in sorted(published):
            tail = evt.rsplit(".", 1)[-1]
            if tail in _SUCCESS_SUFFIXES and evt not in self.handoff_success_events:
                drift.append(
                    f"role publishes {evt!r} (success suffix {tail!r}) but "
                    f"not in WorkflowEventSets.baseline().handoff_success_events"
                )
            elif tail in _FAILURE_SUFFIXES and evt not in all_classified:
                drift.append(
                    f"role publishes {evt!r} (failure suffix {tail!r}) but "
                    f"not in WorkflowEventSets.baseline().rework_trigger_events "
                    f"nor rework_triage_trigger_events"
                )
        return drift


@dataclass
class WorkflowTopology:
    """Directed graph of role-to-role edges via event types."""

    _edges: list[tuple[str, str, str]] = field(default_factory=list)
    _published: dict[str, list[str]] = field(default_factory=dict)  # event -> [publisher roles]
    _subscribed: dict[str, list[str]] = field(default_factory=dict)  # event -> [subscriber roles]
    _star_stages: list[object] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: ZfConfig) -> WorkflowTopology:
        published: dict[str, list[str]] = {}
        subscribed: dict[str, list[str]] = {}

        for role in config.roles:
            for event in role.publishes:
                published.setdefault(event, []).append(role.name)
            for event in role.triggers:
                subscribed.setdefault(event, []).append(role.name)

        # Runtime-owned workflow stages also produce/consume events. Before
        # this accounting, strict cold-start could falsely mark roles as
        # dead-end when they triggered on a fanout aggregate event such as
        # `zaofu.refactor.plan.ready` because no role declared it in
        # `publishes`.
        for stage in list(getattr(config.workflow, "stages", []) or []):
            stage_id = str(getattr(stage, "id", "") or "")
            stage_ref = f"stage:{stage_id}" if stage_id else "stage"
            trigger = str(getattr(stage, "trigger", "") or "")
            if trigger:
                subscribed.setdefault(trigger, []).append(stage_ref)

            aggregate = getattr(stage, "aggregate", None)
            if aggregate is None:
                continue
            producer_ref = f"{stage_ref}:aggregate"
            for attr in ("success_event", "failure_event"):
                event = str(getattr(aggregate, attr, "") or "")
                if event:
                    published.setdefault(event, []).append(producer_ref)

        edges: list[tuple[str, str, str]] = []
        for event, publishers in published.items():
            subscribers = subscribed.get(event, [])
            for pub in publishers:
                for sub in subscribers:
                    edges.append((pub, sub, event))

        return cls(
            _edges=edges,
            _published=published,
            _subscribed=subscribed,
            _star_stages=list(getattr(config.workflow, "stages", []) or []),
        )

    def edges(self) -> list[tuple[str, str, str]]:
        """Return list of (from_role, to_role, via_event) edges."""
        return list(self._edges)

    def orphan_events(self, include_external: bool = False) -> list[str]:
        """Events published by roles but never subscribed by any role.

        By default excludes events consumed by kernel-level subscribers
        (Layer 1 housekeeping, orchestrator) — those are not "orphans"
        even though no role triggers on them. Set include_external=True
        to see all.
        """
        result = [e for e in self._published if e not in self._subscribed]
        if not include_external:
            result = [e for e in result if e not in EXTERNAL_EVENTS]
        return sorted(result)

    def dead_end_roles(self, include_external: bool = False) -> list[str]:
        """Roles that trigger on events no one publishes.

        By default excludes roles that only trigger on EXTERNAL_EVENTS
        (kernel-injected) — those are legitimately "publisher-less".
        """
        all_published = set(self._published)
        dead = []
        for event, roles in self._subscribed.items():
            if event in all_published:
                continue
            if not include_external and event in EXTERNAL_EVENTS:
                continue
            dead.extend(role for role in roles if not str(role).startswith("stage:"))
        return sorted(set(dead))

    def handler_coverage(
        self,
        reactor_handlers: set[str],
        wake_patterns: set[str] | None = None,
    ) -> tuple[list[str], list[str], list[str]]:
        """Compare role publishes vs reactor handlers vs wake_patterns.

        Returns (unhandled, unused, unwoken):
          - unhandled: events a role publishes but reactor has no handler
          - unused: events the reactor handles but no role publishes
          - unwoken: events the reactor handles but not in wake_patterns
                    (silently broken route — this is exactly the LH-3
                    SUSPEND bug class). Empty list if wake_patterns=None.
        """
        publishable = set(self._published.keys())
        unhandled = sorted(
            publishable - reactor_handlers - EXTERNAL_EVENTS
        )
        unused = sorted(reactor_handlers - publishable - EXTERNAL_EVENTS)

        unwoken: list[str] = []
        if wake_patterns is not None:
            unwoken = sorted(reactor_handlers - wake_patterns)

        return unhandled, unused, unwoken

    def check(
        self,
        reactor_handlers: set[str] | None = None,
        wake_patterns: set[str] | None = None,
    ) -> TopologyReport:
        """Run all integrity checks in one call."""
        unhandled: list[str] = []
        unused: list[str] = []
        unwoken: list[str] = []
        if reactor_handlers is not None:
            unhandled, unused, unwoken = self.handler_coverage(
                reactor_handlers, wake_patterns
            )
        return TopologyReport(
            orphan_events=self.orphan_events(),
            dead_end_roles=self.dead_end_roles(),
            unhandled_events=unhandled,
            unused_handlers=unused,
            unwoken_events=unwoken,
        )

    def ascii_render(self) -> str:
        """Simple text visualization of the topology."""
        if not self._edges:
            return "(empty topology)"

        lines: list[str] = []
        for from_role, to_role, event in self._edges:
            lines.append(f"  {from_role} --[{event}]--> {to_role}")
        return "\n".join(lines)

    def star_ascii_render(self) -> str:
        if not self._star_stages:
            return "(no star stages)"
        lines: list[str] = []
        for stage in sorted(self._star_stages, key=lambda item: getattr(item, "id", "")):
            aggregate = getattr(stage, "aggregate", None)
            mode = getattr(aggregate, "mode", "")
            success = getattr(aggregate, "success_event", "")
            failure = getattr(aggregate, "failure_event", "")
            roles = ", ".join(sorted(getattr(stage, "roles", []) or []))
            target_ref = getattr(stage, "target_ref", "")
            task_map = getattr(stage, "task_map", "")
            suffix = []
            if target_ref:
                suffix.append(f"target={target_ref}")
            if task_map:
                suffix.append(f"task_map={task_map}")
            tail = f" ({'; '.join(suffix)})" if suffix else ""
            lines.append(
                f"  {getattr(stage, 'trigger', '')} --"
                f"[{getattr(stage, 'topology', '')}:{getattr(stage, 'id', '')}]"
                f"--> [{roles}] --[{mode}]--> "
                f"{success or '?'} / {failure or '?'}{tail}"
            )
        return "\n".join(lines)

    def full_ascii_render(self) -> str:
        return "\n".join([
            "Linear topology:",
            self.ascii_render(),
            "",
            "Star stages:",
            self.star_ascii_render(),
        ])

    def to_workflow_description(self) -> str:
        """Human-readable Markdown describing the workflow the current
        zf.yaml actually supports.

        Produced from role.publishes / role.triggers rather than any
        hardcoded "dev → review → test → judge" assumption, so Layer 2
        receives guidance matching its real team shape (works for
        code-assist's 3-role, design-first's 5-role with critic,
        safe-team's 6-role, or arbitrary custom YAML).

        Returns Markdown. Empty string if topology has no edges.
        """
        if not self._edges and not self._star_stages:
            return ""

        # Group edges by publisher so the output mirrors execution flow.
        by_publisher: dict[str, list[tuple[str, str]]] = {}
        for from_role, to_role, event in self._edges:
            by_publisher.setdefault(from_role, []).append((event, to_role))

        lines: list[str] = []
        lines.append("基于 zf.yaml 声明的 role.triggers / role.publishes,")
        lines.append("本项目的工作流拓扑如下:")
        lines.append("")
        if by_publisher:
            for publisher in sorted(by_publisher):
                lines.append(f"- **{publisher}** 完成后会发的事件 → 下一站:")
                for event, subscriber in sorted(by_publisher[publisher]):
                    lines.append(f"  - `{event}` → {subscriber}")
            lines.append("")
        else:
            lines.append("- 未声明 linear role.triggers / role.publishes 边。")
            lines.append("")

        # Orphan events (published but no role subscribes) are worth
        # calling out — Layer 2 should know what is a terminal signal
        # vs what expects a downstream action.
        orphans = self.orphan_events()
        if orphans:
            lines.append(
                f"以下事件在 YAML 中被发出但没有 role 订阅 (由 kernel 处理或无后续): "
                f"{', '.join(f'`{e}`' for e in orphans)}"
            )
            lines.append("")

        if self._star_stages:
            lines.append("YAML 还声明了以下 runtime-owned star stages:")
            for line in self.star_ascii_render().splitlines():
                lines.append(f"- `{line.strip()}`")
            lines.append(
                "Layer 2 不能在 YAML 之外发明 authoritative fanout; "
                "fanout 只能由 deterministic kernel 按这些 stage 执行。"
            )
            lines.append("")

        return "\n".join(lines)


# Candidate-level failures are recovered by the kernel candidate-rework sweep
# (runtime/candidate_rework.CANDIDATE_FAIL_EVENTS). Routing them to an agent
# spawned a competing re-plan loop (cangjie 2026-06-02/03 incident; see the
# warning comments in affected zf.yaml rework_routing blocks), so a missing
# route for these is the CORRECT configuration — inspection surfaces INFO,
# never STOP. tests/test_workflow_inspection.py asserts this set stays in
# sync with the runtime sweep tuple (layering forbids core importing runtime).
KERNEL_SWEPT_FAILURE_EVENTS = frozenset({
    "review.rejected",
    # verify.failed 同属 kernel candidate-rework sweep 兜底(在
    # CANDIDATE_FAIL_EVENTS 内);漏登会让 lane_pipeline._derive_rework_routing
    # 再铸一条竞争 lane-route,与 kernel sweep 双路重拆
    # (test_workflow_inspection 守护)。
    "verify.failed",
    "test.failed",
    "judge.failed",
    "integration.failed",
    "candidate.conflict",
    # B-93-06: kernel sweep(candidate_rework)恒兜 plan.rejected(B14-S6
    # operator 拒 → 回 synth replan),inspect 缺 route 应判 INFO 非 STOP。
    # 与 CANDIDATE_FAIL_EVENTS 保持同步(test_workflow_inspection 守护)。
    "plan.rejected",
})


def derive_kernel_swept_events(stages: Any, pipelines: Any = ()) -> frozenset[str]:
    r"""candidate 级失败事件集 = 图派生 ∪ 内建基线(G2,去点名化)。

    原则:**stage 级 aggregate failure_event 是 candidate/stage 级失败**
    (review.rejected/test.failed/judge.failed 即此形),由 kernel
    candidate-rework sweep 兜底;child 级失败(\*.child.failed)是
    lane/agent 级,走显式 rework 路由。自定义事件名的项目由此自动获得
    与 canonical 词汇同等的 sweep 豁免;与内建 frozen 集取并集保证
    兼容不收窄。"""
    derived: set[str] = set(KERNEL_SWEPT_FAILURE_EVENTS)
    for stage in list(stages or []):
        aggregate = getattr(stage, "aggregate", None)
        if aggregate is None and isinstance(stage, dict):
            aggregate = stage.get("aggregate")
        if aggregate is None:
            continue
        failure = (
            getattr(aggregate, "failure_event", None)
            if not isinstance(aggregate, dict)
            else aggregate.get("failure_event")
        )
        if failure:
            derived.add(str(failure))
        # v4 smoke:无返工语义 stage 的 child failure 处置权属 aggregate
        # (缺省 workflow.child.failed 同语义);空 BackedgeConfig 对象
        # truthy,按内容判定。
        def _backedge_active(obj) -> bool:
            if obj is None:
                return False
            if isinstance(obj, dict):
                return any(str(obj.get(k) or "") for k in
                           ("event", "restart_stage", "restart_role"))
            return any(str(getattr(obj, k, "") or "") for k in
                       ("event", "restart_stage", "restart_role"))

        on_fail = (getattr(stage, "on_fail", None)
                   if not isinstance(stage, dict) else stage.get("on_fail"))
        on_reject = (getattr(stage, "on_reject", None)
                     if not isinstance(stage, dict) else stage.get("on_reject"))
        has_rework = _backedge_active(on_fail) or _backedge_active(on_reject)
        child_failure = (
            getattr(aggregate, "child_failure_event", None)
            if not isinstance(aggregate, dict)
            else aggregate.get("child_failure_event")
        )
        if not child_failure:
            topology_kind = (
                getattr(stage, "topology", "")
                if not isinstance(stage, dict) else stage.get("topology", "")
            )
            if str(topology_kind).startswith("fanout"):
                child_failure = "workflow.child.failed"
        if child_failure and not has_rework:
            derived.add(str(child_failure))
    # v4 smoke(2026-06-12):lane spec 中无 rework_to 的 stage(写者首段)
    # 的 child failure = task 级失败,doc 79 candidate-rework sweep 兜 ——
    # 派生纳入,勿要求 agent 路由。
    for spec in list(pipelines or []):
        for stage in getattr(spec, "stages", ()) or ():
            if not getattr(stage, "rework_to", "") and getattr(
                stage, "failure_event", "",
            ):
                derived.add(str(stage.failure_event))
        if getattr(spec, "final_role", ""):
            # final 段终审者自身失败:sweep/escalate 域(物化器约定常量,
            # 内聚引用而非点名散落)。
            from zf.core.workflow.lane_pipeline_materialize import (
                FINAL_CHILD_FAILURE,
            )
            derived.add(FINAL_CHILD_FAILURE)
    return frozenset(derived)
