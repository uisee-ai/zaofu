"""Wake patterns — events that wake the orchestrator via EventWatcher.

Lives as a module-level constant (rather than a local list in start.py)
so validate / tests / topology checks can cross-reference it without
parsing source text.

Invariant: **every event with a reactor handler should either be in
WAKE_PATTERNS or explicitly documented as "observed only"**. See
`WorkflowTopology.handler_coverage()` for the automated check.
"""

# K2(2026-06-11,kernel 体重审计 H2):收窄唤醒面。以下 7 个事件从
# WAKE 列表移除(事件本体保留,Layer 1 sweep/投影照常消费;run_once
# 在下一次合法唤醒时批处理它们,level-triggered 语义不变):
#   memory.note / agent.usage —— run_once 内 housekeeping 批处理,
#     延迟到下次唤醒无正确性影响(cangjie 实测它们造成高频空唤醒);
#   gan.round.started/completed / discriminator.passed —— 纯发射,
#     reactor 无 handler(成功信号不需要决策);
#   fanout.aggregate.started/completed —— kernel 在 run_once 内自发,
#     唤醒自己 = 自唤醒税;recovery 扫描读历史不依赖唤醒。
# 保留:fanout.serialize(需 Layer 2 路由)、cost.budget.exceeded
# (kernel halt 路径未证明前不删)、fanout.timed_out(有 handler,
# 审计原表偏差)。回归基线:cangjie 3821 事件 142 次空唤醒。

from __future__ import annotations

from collections import Counter
from typing import Iterable


# Events that cause orchestrator.run_once() to fire.
#
# Grouping comments mirror the historical groups in start.py so a future
# reader can see the provenance.
WAKE_PATTERNS: tuple[str, ...] = (
    # Layer 2 trigger events (wake Claude Code Orchestrator)
    "dev.build.done",
    "review.approved",
    "review.rejected",
    # B14 (doc 93): plan 审核门事件 — operator 的 approved 必须唤醒
    # 重入孵化;requested/rejected 供投影与通知。
    "plan.approval.requested",
    "plan.approved",
    "plan.rejected",
    "verify.passed",
    "verify.failed",
    "test.passed",
    "test.failed",
    "human.escalate",
    # doc 78 W2: candidate-rework sweep emits this when a candidate failure is
    # plan-level; it must wake the orchestrator agent to re-decompose the
    # task_map (re-implementing the same slices would just repeat the failure).
    "orchestrator.replan_requested",
    "gate.failed",
    "task.status_changed",
    "task.created",
    "feature.liveness.blocked",
    # Kernel-reactor liveness routes. These handlers mutate runtime state
    # or bridge bounded fanout work, so emitted events must wake run_once
    # instead of waiting for an unrelated periodic tick.
    "phase.progressed",
    "task.fanout.requested",
    "workflow.invoke.requested",
    "workflow.reconcile.requested",
    # Tier-2 诊断(task 2026-07-06-0930):requested 唤醒诊断 stage 派发,
    # completed 唤醒结论消费(rework feedback / needs_owner 升级)。
    "diagnosis.requested",
    "diagnosis.completed",
    # Run 11 fix: task.assigned must wake Layer 1 so _dispatch_ready
    # can send the briefing to the assigned worker. Without this the
    # task sat in backlog forever after Layer 2 called `zf kanban assign`.
    "task.assigned",
    # task.dispatched may be emitted by the kernel dispatcher or by older
    # operator/Layer 2 flows. Wake housekeeping so dispatch-derived role
    # heartbeat state is seeded without waiting for tick polling.
    "task.dispatched",
    # TR-DISPATCH-SILENT-STALL-001 follow-up (#G site 6, cangjie 2026-05-21):
    # dispatch_sweep emits this when task.assigned > threshold without a
    # matching task.dispatched. Must wake Layer 2 orchestrator agent so it
    # can reason about retry / respawn / escalate (via rework_routing).
    # Without this entry the sweep emits the event but EventWatcher never
    # fires run_once → orchestrator pane stays at 0 tokens (cangjie
    # 2026-05-21 08:42 post-resume observation).
    "dispatch.silent_stall",
    "dev.blocked",
    "dev.failed",
    "clarification.needed",
    "user.message",
    "arch.proposal.done",
    "design.critique.done",
    "static_gate.passed",
    "static_gate.failed",
    "static_gate.skipped",
    "judge.passed",
    "judge.failed",
    "task.done.blocked",
    "orchestrator.evidence_rework.requested",
    # G-LIFE-3: stuck detector emits when a worker's pane output stops
    # changing for too long. Wakes Layer 1 to process the escalation.
    "worker.stuck",
    # G-RESUME-4: watchdog-driven respawn events
    "worker.respawned",
    "worker.respawn.failed",
    # G-RECYCLE-7: context-window recycle lifecycle events
    "worker.context.warning",
    "worker.context.critical",
    # ZF-PWF-PRECOMPACT-001 (doc 41 §4.4): hook-derived precompact +
    # kernel-internal snapshot_requested. Reactor handler sees
    # precompact and emits snapshot_requested, which downstream
    # (SP-001 projector, recovery briefing) consumes.
    "worker.context.precompact",
    "worker.context.snapshot_requested",
    "worker.recycling",
    "worker.recycled",
    "worker.recycle.failed",
    # G-WIRE-1/2/3: scope + drift + refresh observation events
    "scope.violation",
    "worker.drift.detected",
    "worker.refresh.triggered",
    # G-GAN-1: GAN loop lifecycle
    # G-COST-BLOCK-1: hard budget block
    "cost.budget.exceeded",
    # G-DISC-4: discriminator AND closure result
    "discriminator.failed",
    # α-1 (2026-05-17): fanout.serialize signals that the kernel found
    # file-overlap among proposed fanout children. Layer 1 must wake to
    # route the affected tasks back through backlog scheduler serially.
    "fanout.serialize",
    # α-2 (2026-05-17): worker.heartbeat wakes Layer 1 so housekeeping
    # writes last_heartbeat_at to role_sessions.yaml. Without this wake,
    # heartbeats live in events.jsonl but never reach the registry, so
    # α-3 sweep would see stale data and falsely escalate workers as
    # silent / stuck.
    "worker.heartbeat",
    # 2026-06-01: channel reactor handlers (orchestrator_reactor._on_channel_*)
    # require wake events. Without these, the handlers are registered but
    # EventWatcher never pushes the event into run_once(), so raw
    # `zf emit channel.message.posted` or `channel.agent.reply.requested`
    # silently no-ops. Surfaced by L4 live runner timeout
    # (tests/longhorizon/run_zaofu_channel_real.py).
    "channel.message.posted",
    "channel.agent.reply.requested",
    # Doc-122 discussion driver events are also reactor-handled:
    # _on_channel_discussion_event ticks the discussion state machine when
    # async transports emit reply / question / consensus ledger events. Keep
    # them wakeable; otherwise strict cold-start sees registered handlers that
    # the live watcher will never deliver.
    "channel.agent.reply.completed",
    "channel.question.opened",
    "channel.question.resolved",
    "channel.question.merged",
    "channel.questions.frozen",
    "channel.consensus.proposed",
    "channel.consensus.signed",
    "channel.consensus.blocked",
    # α-3 (2026-05-17): worker.probe.silent wakes run_once so
    # downstream consumers (web UI badges, follow-up reaction) see the
    # signal in real time.
    "worker.probe.silent",
    # α-3+ (2026-05-17): worker.probe.idle wakes run_once so the
    # existing _dispatch_ready_tasks path attempts to assign a backlog
    # item to the idle worker (proactive dispatch path).
    "worker.probe.idle",
    # β-1 (2026-05-17): zaofu.bug.detected wakes Layer 1 so housekeeping
    # / web UI can surface the diagnosis immediately to the operator.
    "zaofu.bug.detected",
    # β-4 (2026-05-17): task.fix_spawned wakes Layer 1 so backlog
    # scheduler picks up the new fix-task in the next dispatch cycle.
    "task.fix_spawned",
    # ω-1.a (2026-05-18): baseline-sync results wake run_once so web UI /
    # housekeeping see the latest task ref state.
    "task.baseline_synced",
    "task.baseline_diverged",
    # B-NEW-10 (2026-05-17): candidate integration lifecycle events must
    # wake run_once so _apply_housekeeping fires _maybe_auto_ship.
    # Without this, B-NEW-3 fix (loader + _apply_housekeeping branch +
    # ship.failed defense emit) is reachable only via tick, which is
    # slow and unreliable. cangjie r-next-5/6/7 reproduced this: 5
    # candidate.integration.completed events → 0 ship.* events. Adding
    # to wake_patterns lets the auto-ship loop close in < 1s after
    # integration.
    "candidate.integration.completed",
    "candidate.integration.started",
    "candidate.conflict",
    "candidate.ready",
    # Refactor goal-convergence bridge events. They are emitted by workers or
    # deterministic bridges and must wake run_once so module parity and gap-plan
    # loops do not wait for an unrelated tick.
    "module.parity.scan.completed",
    # Legacy alias for historical Cangjie/Hermes refactor runs.
    "cangjie.module.parity.scan.completed",
    # Flow-neutral discovery bridge (doc 121 P0) — same family as the
    # parity-scan bridges above; without a wake the bridge only fires on
    # the 5s tick catch-up (test_wake_pattern_coverage flagged the gap).
    "flow.discovery.completed",
    "gap_plan.ready",
    "goal.gap_plan.ready",
    "flow.gap_plan.ready",
    "task.ref.updated",
    # Cost capture misses can prove a reader child dispatch was lost after a
    # provider session replacement; wake the fanout recovery sweep promptly.
    "cost.usage.capture_miss",
    # LH-3.T4 hotfix (2026-04-20): Tri-State SUSPEND must wake Layer 1
    # so _on_suspended can move task to blocked + emit human.escalate
    # + route to EscalationManager. Without this, suspended tasks
    # silently stall without escalation.
    "review.suspended",
    "test.suspended",
    # B3: per-worker state transitions (test-plan 2026-04-15-2342)
    "worker.state.changed",
    "worker.completed",
    "task.continuation_scheduled",
    "task.retry_scheduled",
    # 2026-06-03: stale task-capsule completion rejections are emitted by
    # the revision gate and must wake the reactor so same-lane rework can
    # be dispatched before orphan/max-iteration recovery takes over.
    "task.completion.stale_rejected",
    # Fanout aggregate lifecycle events are referenced by housekeeping.
    # Wake run_once so aggregate state transitions do not depend on an
    # unrelated periodic tick.
    "fanout.timed_out",
    # Fanout child result events are emitted by workers / stage children and
    # consumed by kernel aggregate handlers. They must wake run_once directly;
    # dynamic workflow stages add custom child result names separately.
    "fanout.child.completed",
    "fanout.child.failed",
    # Layer 1 housekeeping events (G-MEM-2/G-EVT-3): these don't wake
    # Layer 2 (orchestrator.triggers filter drops them) but they DO
    # need to trigger run_once so _apply_housekeeping fires and the
    # state files actually get written.
    #
    # Phase 2.5 Bug 2: agent.tool.use / agent.tool.result are tailer
    # telemetry. No housekeeping reacts to them. Waking run_once on
    # every tool call would make tailer emission amplify kernel
    # workload N× (one run_once per tool), and can trip drift/stuck
    # detectors on Phase 2-era installs. Removed from wake_patterns.
    "agent.api_blocked",
    "agent.timeout",
    # Structured repair action intents are external/supervisor-owned inputs.
    # They must wake run_once so Layer 1 can validate and apply/reject the
    # bounded deterministic action.
    "repair.action.requested",
    # Supervisor/autoresearch handoff: accepted trigger decisions should wake
    # runtime/L2 consumers. Skipped trigger decisions are observed-only audit
    # events and intentionally stay out of wake_patterns.
    "run.manager.autoresearch.requested",
    "autoresearch.invocation.requested",
    "autoresearch.trigger.accepted",
    "autoresearch.loop.requested",
    "plan.insight.discovered",
    "research.probe.requested",
    "research.probe.completed",
    "reflection.recorded",
    "replan.proposal.created",
    "replan.contract_eval.requested",
    "replan.contract_eval.completed",
    "replan.contract_eval.adoption_blocked",
    "replan.adoption.prepared",
    "replan.adoption.completed",
    "replan.adoption.stale_rejected",
    "autoresearch.inject.worker_stuck",
    "runtime.attention.needed",
    # ZF-ARTIFACT-MANIFEST-001 (2026-05-19): agent roles publish
    # handoff artifacts as external events. Wake Layer 1 so refs,
    # contract projection, and follow-up briefing context are updated
    # without waiting for a periodic tick.
    "artifact.manifest.published",
    "task.contract.update",
    "task.contract.invalid",
    # 1202-T3: Codex hook engine (`--enable hooks`) bridges
    # through hook_recv under the codex.hook.* namespace. All five wake
    # run_once so the reactor handler (even a no-op observational one)
    # fires and future handlers can be added without re-editing this list.
    "codex.hook.session_start",
    "codex.hook.user_prompt_submit",
    "codex.hook.pre_tool_use",
    "codex.hook.post_tool_use",
    "codex.hook.stop",
)


# B6a (R25 ISSUE-006): telemetry/噪音事件 — 唤 Layer 1 reactor handler
# (observational)但硬性不唤 Layer 2 agent turn。一次 Layer 2 turn 是一次
# 完整 agent 推理;hook 流速 > 推理吞吐时 cursor 永久积压(R25: 2.9h)。
LAYER2_NOISE_EVENTS: frozenset[str] = frozenset({
    "codex.hook.session_start",
    "codex.hook.user_prompt_submit",
    "codex.hook.pre_tool_use",
    "codex.hook.post_tool_use",
    "codex.hook.stop",
    "hook.orphan_event",
    "agent.usage",
    "runtime.snapshot.recorded",
    "reconcile.decision.shadow",
    "orchestrator.decision.recorded",
    "task.doc.updated",
})


def reactor_handler_events() -> set[str]:
    """Return the set of event types the orchestrator reactor has
    built-in handlers for.

    P0-2 (2026-04-20): source of truth is the `_BUILTIN_HANDLER_METHODS`
    table in orchestrator_reactor.py. We read that tuple directly
    instead of instantiating a stub reactor — keeps this util free of
    the heavier Orchestrator dependencies.

    Used by `zf validate --cold-start` to detect the LH-3 SUSPEND bug
    class (handler exists but wake_pattern missing → silent route break).
    """
    from zf.runtime.orchestrator_reactor import _BUILTIN_HANDLER_METHODS
    return {event_type for event_type, _ in _BUILTIN_HANDLER_METHODS}


# ---- P3 (2026-04-20): configurable wake_patterns extensions ----

def compute_effective_wake_patterns(config) -> set[str]:
    """Merge base WAKE_PATTERNS with opt-in YAML extensions.

    Reads `config.workflow.wake_extensions.{hooks,agent}`. Each section,
    if enabled, contributes its `include` list. Disabled sections
    contribute nothing — identity behavior preserved for all existing
    YAMLs.

    YAML-declared star stages are deterministic kernel routes, so their
    trigger/result events must wake the watcher even when they are custom
    project events such as ``candidate.ready``.
    """
    result = set(WAKE_PATTERNS)
    workflow = getattr(config, "workflow", None)
    for stage in getattr(workflow, "stages", []) or []:
        trigger = getattr(stage, "trigger", "")
        if trigger:
            result.add(trigger)
        aggregate = getattr(stage, "aggregate", None)
        for event_type in (
            getattr(aggregate, "success_event", ""),
            getattr(aggregate, "failure_event", ""),
            # child result events must wake run_once too: the reader-fanout
            # handler ingests them to mark manifest children completed/failed
            # and advance the aggregate. Without these, review/verify/judge
            # children finish but the stage never progresses (silent stall).
            getattr(aggregate, "child_success_event", ""),
            getattr(aggregate, "child_failure_event", ""),
        ):
            if event_type:
                result.add(event_type)
        if getattr(aggregate, "synth_role", ""):
            result.add("fanout.synth.completed")

    # YAML-declared external_triggers are events the kernel reacts to from
    # outside the flow (entry points such as the light-topology
    # `prd.requested` synthesizer). They have builtin reactor handlers but
    # no owning stage, so without this fold-in the EventWatcher never wakes
    # run_once for them and the handler silently never fires (light baseline
    # 2026-07-06: prd.requested emitted but task_map never synthesized).
    dag = getattr(workflow, "dag", None)
    for trigger in getattr(dag, "external_triggers", []) or []:
        if trigger:
            result.add(trigger)

    ext = getattr(workflow, "wake_extensions", None)
    if ext is None:
        return result
    for section in (ext.hooks, ext.agent):
        if section.enabled:
            result.update(section.include)
    return result


def rate_limits_for_config(config) -> dict[str, int]:
    """Return event_type → per-minute-cap for all rate-limited wake
    extensions. Events not in the returned dict are unlimited."""
    limits: dict[str, int] = {}
    ext = getattr(config.workflow, "wake_extensions", None)
    if ext is None:
        return limits
    for section in (ext.hooks, ext.agent):
        if section.enabled and section.rate_limit_per_minute > 0:
            for event_type in section.include:
                limits[event_type] = section.rate_limit_per_minute
    return limits


class WakeRateLimiter:
    """Token-bucket-ish limiter: for each configured event_type, accept
    at most N occurrences per 60-second rolling window. Events not in
    `limits` pass through unlimited.

    Thread-unsafe (zf runs single-threaded event pumps); callers should
    use one instance per EventWatcher.
    """

    def __init__(self, limits: dict[str, int]) -> None:
        self._limits = dict(limits)
        self._timestamps: dict[str, list[float]] = {}

    def allow(self, event_type: str, now: float | None = None) -> bool:
        """Return True if the event should be allowed to wake.
        False means "drop this wake, the event still lives in
        events.jsonl — we just skip run_once for it right now"."""
        limit = self._limits.get(event_type)
        if not limit:
            return True
        import time as _time
        t = now if now is not None else _time.time()
        cutoff = t - 60.0
        dq = self._timestamps.setdefault(event_type, [])
        # Drop expired timestamps (linear scan; lists stay small since
        # limit ≤ a few dozen per minute).
        while dq and dq[0] < cutoff:
            dq.pop(0)
        if len(dq) >= limit:
            return False
        dq.append(t)
        return True


# K2:显式登记"不自唤醒、由 run_once 在下一次合法唤醒时批处理"的事件
# (level-triggered 语义;wake_patterns docstring 承诺的 observed-only
# 文档化机制)。invariant 测试消费本集合:housekeeping 分支事件 ∈
# WAKE_PATTERNS ∪ BATCH_PROCESSED_EVENTS。
BATCH_PROCESSED_EVENTS: frozenset[str] = frozenset({
    "memory.note",
    "agent.usage",
    "gan.round.started",
    "gan.round.completed",
    "discriminator.passed",
    "fanout.aggregate.started",
    "fanout.aggregate.completed",
    # 0d90879 retry helpers read these from history (level-triggered).
    # integration.failed is also a lane-pipeline stage failure_event and
    # enters the wake surface dynamically via
    # compute_effective_wake_patterns; workflow.resume.applied is emitted
    # by `zf recover workflow` alongside task.assigned /
    # task.status_changed, which already wake the loop.
    "integration.failed",
    "workflow.resume.applied",
    "fanout.child.dispatch_lost",
})


WAKE_CATEGORY_HANDLER_TRIGGERING = "handler-triggering"
WAKE_CATEGORY_LAYER1_WAKE_LAYER2_NOISE = "layer1-wake-layer2-noise"
WAKE_CATEGORY_PROJECTION_ONLY = "projection-only"
WAKE_CATEGORY_BATCH_PROCESSED = "batch-processed"
WAKE_CATEGORY_OBSERVED_ONLY = "observed-only"
WAKE_CATEGORY_UNCLASSIFIED = "unclassified"


PROJECTION_ONLY_EVENTS: frozenset[str] = frozenset({
    "memory.note",
    "agent.usage",
    "runtime.snapshot.recorded",
    "reconcile.decision.shadow",
    "orchestrator.decision.recorded",
    "task.doc.updated",
    "fanout.aggregate.started",
    "fanout.aggregate.completed",
})


def wake_event_category(event_type: str) -> str:
    if event_type in WAKE_PATTERNS and event_type in LAYER2_NOISE_EVENTS:
        return WAKE_CATEGORY_LAYER1_WAKE_LAYER2_NOISE
    if event_type in WAKE_PATTERNS:
        return WAKE_CATEGORY_HANDLER_TRIGGERING
    if event_type in PROJECTION_ONLY_EVENTS:
        return WAKE_CATEGORY_PROJECTION_ONLY
    if event_type in BATCH_PROCESSED_EVENTS:
        return WAKE_CATEGORY_BATCH_PROCESSED
    if event_type in LAYER2_NOISE_EVENTS:
        return WAKE_CATEGORY_OBSERVED_ONLY
    return WAKE_CATEGORY_UNCLASSIFIED


def wake_contract_diagnostics(
    wake_patterns: Iterable[str] | None = None,
) -> dict[str, object]:
    patterns = tuple(wake_patterns if wake_patterns is not None else WAKE_PATTERNS)
    counts = Counter(patterns)
    classified_events = (
        set(patterns)
        | set(BATCH_PROCESSED_EVENTS)
        | set(LAYER2_NOISE_EVENTS)
        | set(PROJECTION_ONLY_EVENTS)
    )
    categories: dict[str, list[str]] = {}
    for event_type in sorted(classified_events):
        category = wake_event_category(event_type)
        categories.setdefault(category, []).append(event_type)
    return {
        "wake_count": len(patterns),
        "duplicate_wake_patterns": sorted(
            event_type for event_type, count in counts.items() if count > 1
        ),
        "batch_processed_that_wake": sorted(
            set(patterns) & set(BATCH_PROCESSED_EVENTS)
        ),
        "unclassified": categories.get(WAKE_CATEGORY_UNCLASSIFIED, []),
        "category_counts": {
            category: len(events) for category, events in sorted(categories.items())
        },
        "categories": categories,
    }


def wake_worthy(event) -> bool:
    """avbs-r4 F3: 高频观察型 hook 事件不值一次 run_once 唤醒。

    3 个 codex writer 并行时 tool-use hook 流速远超单线程 watcher 消化
    速度(r4 实测 3.6s/事件 × 13k 事件,lag 峰值 4864s,三次 flush 重启)。
    而这两类唤醒是纯成本:

    - ``codex.hook.post_tool_use`` 的 reactor handler 是 no-op;
    - ``codex.hook.pre_tool_use`` 仅 permissionDecision=deny 时有意义
      (转发 agent.api_blocked 进熔断),正常放行占绝对多数。

    事件本体照常落 events.jsonl(hook-recv 侧效应在写入时已完成),
    只是不再逐条唤醒 run_once。session_start / user_prompt_submit /
    stop 是低频生命周期信号,保持唤醒(stop 还挂着 provider 恢复)。
    """
    event_type = str(getattr(event, "type", "") or "")
    if event_type == "codex.hook.post_tool_use":
        return False
    if event_type == "codex.hook.pre_tool_use":
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        return payload.get("permissionDecision") == "deny"
    return True


# FIX-5②(bizsim r4 $697 空转账单):同型触发指数退避参数与窗口计算。
# 判定逻辑在此 sibling;orchestrator._notify_orchestrator_agent 仅保留
# streak 记账与门控 call site。
LAYER2_SAME_TRIGGER_FREE = 3
LAYER2_STREAK_BASE_S = 5.0
LAYER2_STREAK_CAP_S = 300.0


def layer2_effective_wake_interval(
    *,
    interval: float,
    event_type: str,
    streak_type: str,
    streak_count: int,
) -> float:
    """Return the effective min-interval before the next Layer-2 turn.

    同型触发连续 commit 未超免额 → 维持基础窗;超过后窗口按 2^n 增长至
    上限。异型触发不受影响(调用方在 commit 时复位 streak)。
    """
    if event_type != streak_type or streak_count < LAYER2_SAME_TRIGGER_FREE:
        return interval
    return max(interval, min(
        max(interval, LAYER2_STREAK_BASE_S) * (
            2 ** (streak_count - LAYER2_SAME_TRIGGER_FREE)
        ),
        LAYER2_STREAK_CAP_S,
    ))
