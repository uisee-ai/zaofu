"""Orchestrator — deterministic dispatch loop, no LLM calls.

Interaction protocol:
  1. Orchestrator selects a ready task + idle role
  2. Writes task briefing to .zf/briefings/{role}-{task_id}.md
  3. Injects briefing into agent's tmux pane (agent sees it as prompt)
  4. Agent works, then runs: zf emit <event-type> --task <task_id>
  5. EventWatcher detects the event in events.jsonl
  6. Orchestrator.run_once() called → reacts to event → dispatches next
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from zf.core.config.schema import ZfConfig, RoleConfig
from zf.core.events.dedupe import BoundedIdSet
from zf.core.events.factory import event_log_from_project
from zf.core.events.module_parity import is_module_parity_scan_completed_event
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.task.store import TaskStore
from zf.core.task.wip import WipEnforcer
from zf.core.statemachine.task import TaskStateMachine
from zf.core.verification.discriminator import (
    ArchitectureRulesD,
    ContractQualityD,
    ContractD,
    DiscriminatorRunner,
    FunctionalD,
    PromotedRulesD,
)
from zf.core.verification.scope_ratchet import ScopeRatchet, ScopeSnapshot
from zf.runtime.drift import DriftDetector
from zf.runtime.escalation import EscalationManager
from zf.runtime.reader_child_task_resolution import resolve_reader_child_task_id
from zf.runtime.refresh import RefreshPolicy
from zf.runtime.call_result_runtime import hydrate_runtime_call_result_event
from zf.runtime.transport import (
    DispatchContext,
    TransportAdapter,
    transport_error_diagnostics,
)
from zf.runtime.orchestrator_lifecycle import LifecycleManagerMixin
from zf.runtime.orchestrator_fanout import FanoutCoordinationMixin
from zf.runtime.orchestrator_dispatch import DispatchMixin
from zf.runtime.orchestrator_reactor import EventReactorMixin
from zf.runtime.orchestrator_module_parity import ModuleParityBridgeMixin
from zf.runtime.agent_view_runtime import AgentViewRuntimeMixin
from zf.runtime.fanout_evidence_queries import FanoutEvidenceQueriesMixin
from zf.runtime.watcher import StuckDetector
from zf.runtime.orchestrator_briefing import build_orchestrator_briefing
from zf.runtime.progress import regenerate_progress
from zf.runtime.housekeeping import (
    apply_agent_usage_event,
    apply_circuit_breaker_failure,
    apply_memory_note_event,
    apply_rework_failure_event,
    apply_task_contract_event,
    apply_task_dispatched_heartbeat_seed,
    apply_worker_activity_heartbeat,
    apply_worker_heartbeat_event,
    apply_worker_state_changed_event,
    promote_to_memory_note_event,
)
# arch_proposal_contract_update_event + spec_ingest_suggested_event
# intentionally NOT imported here (P0/K1, docs/impl/22): these are utility
# synthesis helpers that orchestrator (LLM) may invoke via the new
# zf-harness-backlog-synthesis skill at stage ④ backlog, but the kernel
# must not auto-fire them on arch.proposal.done.
from zf.runtime.rework_triage import (
    REWORK_TRIAGE_TRIGGER_EVENTS,
    classify_rework_trigger,
    triage_from_payload,
)
from zf.runtime.terminal_events import is_stage_progress_event
from zf.runtime.worker_state_runtime import WorkerStateRuntimeMixin
from zf.runtime.writer_fanout_data import WriterFanoutDataMixin
from zf.core.cost.tracker import CostTracker
from zf.core.memory.store import MemoryStore
from zf.runtime.orchestrator_types import OrchestratorDecision


if TYPE_CHECKING:
    from zf.runtime.spawn_coordinator import SpawnCoordinator
    from zf.runtime.static_gate import StaticGateResult


_KERNEL_LIVENESS_EVENTS = frozenset({
    # Must not depend on Layer 2 remembering to recover. If terminal
    # verification rejects an agent/judge completion claim, Layer 1 turns
    # that blocked state into bounded rework.
    #
    # Kernel-owned flow-entry: light-topology `prd.requested` synthesizes the
    # single-task task_map (runtime/light_flow.py). It carries no task_id, so
    # it must fire its builtin primary regardless of Layer 1/2 mode.
    "prd.requested",
    # ZF-E2E-PRD-P1 (2026-07-11): the unified-intake entry (doc 123,
    # E1 bootstrap in _on_workflow_invoke_requested) is the same kernel-owned
    # flow-entry class as prd.requested / workflow.reconcile.requested — but
    # was never added here, so in Layer-2-active configs the invoke fell to
    # the "Layer 2 owns it" branch and, absent an orchestrator trigger, was
    # silently dropped (live: workflow-submit accepted, flow never started).
    "workflow.invoke.requested",
    "discriminator.failed",
    "agent.api_blocked",
    "agent.timeout",
    "worker.context.critical",
    "phase.progressed",
    "task.fanout.requested",
    # FIX-6(bizsim r4 F2):解锁后 level 重扫是纯机械的内核恢复动作,
    # 不依赖 Layer-2 记得恢复 —— 必须由 Layer-1 handler 直接消费。
    "workflow.reconcile.requested",
    "run.manager.autoresearch.requested",
    "task.continuation_scheduled",
    "task.retry_scheduled",
    "codex.hook.stop",
    "autoresearch.trigger.accepted",
    "autoresearch.invocation.requested",
    "autoresearch.inject.worker_stuck",
    "orchestrator.evidence_rework.requested",
    "worker.completed",
    "artifact.manifest.published",
    # 2026-06-01: channel routing is mechanical (mention → reply.requested →
    # dispatch). It must not depend on Layer 2 remembering to forward the
    # event back; without this fast-path, raw `zf emit channel.message.posted`
    # in an L2-active config (any yaml with an `orchestrator` role) silently
    # never routes. Surfaced by tests/longhorizon/run_zaofu_channel_real.py
    # against cj-mono. The deterministic handler is _on_channel_message_posted
    # in orchestrator_reactor.py and the inline dispatch chain is in
    # channel_router.route_channel_message.
    "channel.message.posted",
    "channel.agent.reply.requested",
    # 2026-07-03: channel discussion progression (blind-fanout ->
    # phase2_relay -> phase3_synthesis, doc 122) is likewise mechanical and
    # taskless — channel_discussion.py emits these with correlation_id=
    # channel_id, never task_id, so both the Layer-1 (task_id-gated) and
    # Layer-2-active (terminal-ownership-gated) dispatch paths skip the
    # builtin _on_channel_discussion_event handler, and every phase
    # transition ends up waiting for the periodic sweep_discussion_deadlines
    # tick fallback instead of firing immediately when the triggering event
    # lands (racing-codex e2e rounds 1-3: every `phase.changed` showed
    # source="deadline-sweep", even when all roster members had already
    # replied). Same bug class and same fix as judge.passed above.
    "channel.agent.reply.completed",
    "channel.question.opened",
    "channel.question.resolved",
    "channel.question.merged",
    "channel.questions.frozen",
    "channel.consensus.proposed",
    "channel.consensus.signed",
    "channel.consensus.blocked",
})


class BudgetExceededError(RuntimeError):
    """Raised by the charging primitive (``_send_transport_task``) when a paid
    dispatch would exceed the configured budget.

    Subclasses ``RuntimeError`` so the many dispatch call sites that already
    tolerate the primitive's transport-unavailable ``raise`` treat an
    over-budget block identically — they skip their post-send ``*.dispatched``
    bookkeeping instead of recording a dispatch that never happened.
    """


def _kernel_owns_liveness_event(config: object, event_type: str) -> bool:
    """Return True when Layer 1 must consume a taskless event mechanically.

    Most Layer-2-active events go to the orchestrator agent. Reader-stage
    failure events are an exception: they happen before a canonical task exists
    and must deterministically re-trigger the same reader fanout stage.
    """
    if event_type in _KERNEL_LIVENESS_EVENTS:
        return True
    try:
        from zf.runtime.stage_failure_replan import reader_stage_failure_events

        return event_type in reader_stage_failure_events(config)
    except Exception:
        return False

_KERNEL_TERMINAL_EVENTS = frozenset({
    "review.approved",
    "verify.passed",
    "test.passed",
    "judge.passed",
})

_CANDIDATE_REWORK_TRIGGER_EVENTS = frozenset({
    "review.rejected",
    "verify.failed",
    "test.failed",
    "judge.failed",
    "integration.failed",
    "plan.rejected",
    "candidate.conflict",
    "fanout.cancelled",
    "fanout.child.failed",
    # Reader fanout stages may use role-specific child failure events. If the
    # aggregate failure is emitted while handling one of these events, running
    # the sweep in the same cycle prevents the candidate from waiting on a
    # later idle tick.
    "review.child.failed",
    "verify.child.failed",
    "test.child.failed",
    "judge.child.failed",
    "workflow.child.failed",
})

# EVAL-DECISION-OUTCOME-001: enumerated reasons for orchestrator.decision.recorded.
# Each set is a closed taxonomy — adding a new reason requires updating the
# CLI render in zf metrics decision-ratio --by-reason.
_NO_ACTION_REASONS = frozenset({
    "idle_sweep",            # silent wake, no trigger
    "out_of_scope",          # trigger seen but irrelevant to current roles
    "awaiting_dependency",   # task blocked by upstream
    "all_steps_done",        # workflow completed
    "not_ready",             # contract / refs incomplete
    "inline_override_skip",  # ZF-LH-INLINE-001 skipped a stage
})

_FAILED_REASONS = frozenset({
    "dispatch_blocked_by_rework_cap",
    "circuit_open",
    "no_eligible_role",
    "gate_evidence_missing",
    "scope_violation",
})


def _classify_outcome_reason(
    *,
    decision_kind: str,
    decisions: list,
    triggers: list | None,
) -> str:
    """EVAL-DECISION-OUTCOME-001: derive a stable enum value for the
    decision.recorded outcome_reason payload field.

    Default '' for decisions where reason is self-evident from
    decision_kind (dispatch / escalate / wait).
    """
    # Build a lowercase joined-reason string for keyword matching.
    reason_blob = " ".join(
        (d.reason or "").lower()
        for d in (decisions or [])
    )
    if decision_kind == "no_action":
        if not triggers:
            return "idle_sweep"
        if "rework cap" in reason_blob or "rework_cap" in reason_blob:
            return "dispatch_blocked_by_rework_cap"
        if "not ready" in reason_blob or "not_ready" in reason_blob:
            return "not_ready"
        if "skip" in reason_blob and "stage" in reason_blob:
            return "inline_override_skip"
        if "depend" in reason_blob or "blocked by" in reason_blob:
            return "awaiting_dependency"
        if "all steps" in reason_blob or "loop_complete" in reason_blob:
            return "all_steps_done"
        return "out_of_scope"
    if decision_kind == "failed" or decision_kind == "blocked":
        if "rework cap" in reason_blob or "rework_cap" in reason_blob:
            return "dispatch_blocked_by_rework_cap"
        if "circuit" in reason_blob:
            return "circuit_open"
        if "no eligible" in reason_blob or "no role" in reason_blob:
            return "no_eligible_role"
        if "evidence" in reason_blob:
            return "gate_evidence_missing"
        if "scope" in reason_blob:
            return "scope_violation"
        return ""
    return ""


def _parse_event_ts(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class Orchestrator(
    FanoutCoordinationMixin,
    
    DispatchMixin,
    EventReactorMixin,
    ModuleParityBridgeMixin,
    LifecycleManagerMixin,
    AgentViewRuntimeMixin,
    FanoutEvidenceQueriesMixin,
    WriterFanoutDataMixin,
    WorkerStateRuntimeMixin,
):
    """Deterministic orchestrator — reads state, makes dispatch decisions.

    Communication with agents:
      Outbound: task briefings delivered via the TransportAdapter
      Inbound:  events in .zf/events.jsonl (agents run `zf emit`)
      Debug:    agent output captured to .zf/logs/{role}.log
    """

    def __init__(
        self,
        state_dir: Path,
        config: ZfConfig,
        transport: TransportAdapter,
        *,
        project_root: Path | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.config = config
        self.transport = transport
        # project_root is the on-disk project directory (where zf.yaml lives).
        # Falls back to state_dir.parent for legacy callers, but every CLI
        # entry point should resolve it explicitly via ProjectContext so
        # configurations like ``project.state_dir: .runtime/zf`` resolve to
        # the real project root rather than a nested directory.
        self.project_root = (project_root or state_dir.parent).resolve()
        # Runtime-only roles spawned by autoscale (or other runtime decisions)
        # are tracked here instead of mutating ``self.config.roles``. Keeping
        # the original ZfConfig.roles list pristine preserves the
        # "zf.yaml is the only control plane" invariant: any CLI / Web
        # reload reads the same RoleConfig list, and runtime-spawned
        # instances are surfaced separately via ``all_roles()`` /
        # ``role_sessions.yaml``.
        self._runtime_roles: dict[str, RoleConfig] = {}
        self.event_log = event_log_from_project(state_dir, config=config)
        # TR-EVENT-SCHEMA-LOCK-001 step 2/3 (doc 42 §11.3 A): wire event
        # schema validation into the EventWriter. Mode defaults to
        # ``disabled`` (legacy yaml behaviour); operators set
        # ``verification.event_schema.mode: warning`` or ``blocking``
        # in zf.yaml to opt in. Registry is built from
        # ``workflow.dag.event_schemas``; empty registry → effectively
        # disabled even if mode says otherwise.
        from zf.core.verification.event_schema import EventSchemaRegistry
        schema_registry = EventSchemaRegistry.from_config(config)
        schema_mode = getattr(
            getattr(config.verification, "event_schema", None),
            "mode",
            "disabled",
        )
        self.event_writer = EventWriter(
            self.event_log,
            schema_registry=schema_registry if schema_registry.rule_count() else None,
            schema_mode=schema_mode,
            default_origin="kernel",
        )
        self.task_store = TaskStore(state_dir / "kanban.json")
        self.session_store = SessionStore(state_dir / "session.yaml")
        self.cost_tracker = CostTracker(state_dir / "cost.jsonl")
        self.memory_store = MemoryStore(state_dir / "memory")
        self.escalation = EscalationManager(state_dir, config=config)
        self.sm = TaskStateMachine()
        self.wip = WipEnforcer(limit=1)
        # Bounded (2026-06-10 review): one entry per event with no cap grew
        # without bound over zero-touch runs; the offset cursor already
        # prevents old replay, so FIFO eviction of the oldest ids is safe.
        self._processed_event_ids = BoundedIdSet(max_size=50_000)
        # Memory auto-promote dedupe: which trigger event ids have already
        # been turned into a memory.note this process. Reset per process —
        # restart allows one duplicate at worst, TTL/decay handles it.
        self._promoted_causations = BoundedIdSet(max_size=50_000)
        self._restore_autoscaled_roles()
        # G-LIFE-3 + G-INST-4: one StuckDetector per worker instance
        # (keyed by instance_id). Threshold from RoleConfig.
        self._stuck_detectors: dict[str, StuckDetector] = {
            r.instance_id: StuckDetector(stale_threshold=r.stuck_threshold_seconds)
            for r in self.config.roles
            if r.name != "orchestrator"
        }
        self._stuck_already_reported: set[str] = set()
        # G-RESUME-4: watchdog counts consecutive is_alive=False results
        # per instance. >= _dead_threshold triggers respawn via coordinator.
        self._dead_counter: dict[str, int] = {}
        self._dead_threshold: int = 3
        self._respawn_success_circuit_opened: set[str] = set()
        # G-RESUME-3: SpawnCoordinator used for respawn after crash.
        # Built lazily with the resolved project_root so non-default
        # project.state_dir never changes the worker cwd.
        self._spawn_coordinator: "SpawnCoordinator | None" = None
        # G-RECYCLE-4: per-instance lifecycle state for context recycle.
        # Values: "healthy" (default implicit), "pending_recycle", "recycling".
        self._instance_state: dict[str, str] = {}
        # G-RECYCLE-8: dedup key for synthesized agent.usage events.
        # (instance_id, timestamp) → True. Prevents double-counting when
        # _check_context_thresholds reads the same session turn twice.
        self._synth_usage_seen: set[tuple[str, str]] = set()
        # G-WIRE-2: drift detector + per-(signal, role, dispatch) cooldown.
        # Runs once per run_once cycle on the recent event tail.
        self._drift_detector = DriftDetector()
        self._drift_last_emit: dict[tuple[str, str, str], float] = {}
        self._drift_cooldown_seconds: float = 30.0
        # G-GAN-1: per-task GAN round counter for architect↔critic loop.
        # When workflow.gan_rounds >= 2, arch.proposal.done events
        # increment the counter and route the task back through arch
        # for another round until the counter hits gan_rounds.
        self._gan_round: dict[str, int] = {}
        # G-COST-BLOCK-1: per-(scope, role) cooldown for budget exceeded
        # events to prevent every cycle re-emitting when budget stays over.
        self._cost_block_last_emit: dict[tuple[str, str], float] = {}
        self._cost_block_cooldown_seconds: float = 60.0
        # G-DISC-4: discriminator runner — AND closure verification gate
        # before task → done. ContractD verifies task contract has the
        # minimum evidence; FunctionalD wraps quality_gates; SemanticD
        # (LH-2, rule-based) enforces scope + exclusion fidelity when
        # opted-in via verification.semantic.enabled.
        _discs: list = [
            ContractD(
                require_contract=getattr(
                    self.config.verification.contract, "required", False,
                ),
            ),
            FunctionalD(quality_gates=self.config.quality_gates),
        ]
        if getattr(self.config.verification.contract, "quality_required", False):
            _discs.append(ContractQualityD())
        if getattr(self.config.verification.architecture, "enabled", False):
            _discs.append(ArchitectureRulesD())
        if getattr(self.config.verification.promoted, "enabled", False):
            _discs.append(PromotedRulesD())
        if getattr(self.config.verification.semantic, "enabled", False):
            from zf.core.verification.discriminators.semantic import (
                SemanticDiscriminator,
            )
            _discs.append(SemanticDiscriminator())
        self._discriminator_runner = DiscriminatorRunner(_discs)
        # G-WIRE-3: RefreshPolicy as observation layer composing the 5
        # refresh trigger types into a single worker.refresh.triggered
        # event. Does NOT auto-act — Sprint A's stuck path and Sprint E's
        # recycle path own the actual responses. max_turns=50 is a
        # production-safer default than the library's debug 10.
        self._refresh_policy = RefreshPolicy(max_turns=50, max_failures=3)
        self._turn_counter: dict[str, int] = {}
        self._failure_counter: dict[str, int] = {}
        self._refresh_already_emitted: set[tuple[str, str]] = set()
        # LH-0.T3: orphan-timeout tracking. _dispatch_epoch maps task_id
        # to the monotonic-ish timestamp of the most recent dispatch or
        # stage-progress event. _check_orphaned_tasks compares (now -
        # epoch) to RoleConfig.orphan_{warning,escalate}_seconds.
        # _orphan_warned is per-task dedup so warnings don't flood.
        self._dispatch_epoch: dict[str, float] = {}
        self._orphan_warned: set[str] = set()
        # LH-0.T4: context hard-cap gate. instance_id → timestamp when
        # ratio first crossed context_hard_cap. Used by
        # _find_available_role (skip dispatch) and _check_context_
        # thresholds (force recycle after drain_hold_seconds).
        self._hard_cap_exceeded: dict[str, float] = {}
        # B-COST-02: consecutive usage-capture misses per claude-code
        # instance. Debounces the cost.usage.capture_miss probe so a
        # transient/early-boot None doesn't false-alarm.
        self._usage_capture_misses: dict[str, int] = {}
        # LH-4.T3: circuit-tripped emission cooldown — per (role, task)
        # timestamp of the last circuit.tripped emission. Without this
        # every run_once loop would spam the log while the breaker is
        # open. 60s cooldown is applied in _emit_circuit_tripped.
        self._circuit_tripped_last: dict[tuple[str, str], float] = {}
        # ω-1.c (2026-05-18): per-(instance_id, signal_type) cooldown
        # for heartbeat-sweep emissions (worker.stuck, worker.probe.silent).
        # Without this, _run_heartbeat_sweep re-emits the same signal every
        # 60s tick for every still-silent worker — r-next-10 saw
        # worker.stuck × 5 per minute for ~5min causing event spam.
        # Cleared by apply_worker_heartbeat_event when the worker recovers
        # (housekeeping wraps post-call hook).
        self._sweep_signal_last_emit_at: dict[tuple[str, str], float] = {}
        # B11 (Run 3 post-mortem): Layer 2 cool-down after agent.api_blocked
        # / agent.timeout. While time.time() < _layer2_blocked_until,
        # _notify_orchestrator_agent skips dispatch — prevents wake storm
        # while Claude API is rate-limited or timing out.
        self._layer2_blocked_until: float = 0.0
        # ZF-E2E-MINI-P2: one stall/attention wake per budget-freeze episode
        # (see _notify_orchestrator_agent budget-freeze silence gate).
        self._layer2_freeze_wake_fired: bool = False
        self._layer2_cooldown_s: float = self.config.orchestrator.rate_limit_cooldown_s
        # Wake coalescing (2026-05-28, docs/records/2026-05-28-orchestrator-wake-coalescing.md):
        # a burst of trigger events within wake_min_interval_s collapses into a
        # leading wake + one trailing flush instead of N back-to-back briefings.
        # The orchestrator rebuilds full state from disk each wake, so a
        # suppressed event loses no context — it is remembered and flushed once
        # the interval elapses (never dropped).
        self._layer2_wake_min_interval_s: float = (
            self.config.orchestrator.wake_min_interval_s
        )
        self._layer2_last_wake_at: float = 0.0
        # Idle-gated batch coalescing (doc 66 §14.0, 2026-05-29): trigger events
        # within one run_once reaction batch — and rapid cross-batch bursts
        # within wake_min_interval_s — accumulate here (deduped per §14.3) and
        # fire as ONE multi-trigger turn. Never dropped: drained by
        # _flush_layer2_batch (batch end) or _flush_pending_layer2_wake
        # (run_once top / idle tick). Replaces the single-event pending so a
        # whole burst's triggers reach the briefing, not just the last.
        self._layer2_pending: list[ZfEvent] = []
        self._layer2_in_batch: bool = False
        # FIX-5②(bizsim r4 $697 空转账单):同型触发连发的指数退避状态。
        # 纯瞬态节流(重启归零即恢复默认节奏),真相不依赖它。
        self._layer2_streak_type: str = ""
        self._layer2_streak_count: int = 0
        self._init_worker_state_tracking()

        # B-NEW-6 (2026-05-16): clear blocked_human on fresh Orchestrator
        # init. blocked_human is the "operator escalation required" parking
        # state set when a worker's respawn cooldown trips (3 failures in
        # 120s window). Running ``zf start`` IS the operator intervention
        # — without this clear, restart after a respawn-failure cascade
        # leaves dispatch silently dead (cangjie r-next-5: B-NEW-5 drift
        # storm → respawn-fail cascade → all 12 workers blocked_human →
        # post-restart dispatch silently rejects every task with no event,
        # because _worker_dispatchable returns False before any
        # dispatch_skipped emit can fire).
        #
        # We emit a real worker.state.changed → idle event so persistence
        # is consistent and future restarts won't replay the block.
        blocked_instances = [
            inst for inst, st in self._last_worker_state.items()
            if st == "blocked_human"
        ]
        if blocked_instances:
            import sys
            print(
                f"⚠ zf start: clearing blocked_human on "
                f"{len(blocked_instances)} worker(s) "
                f"({', '.join(sorted(blocked_instances))}). "
                f"Treat ``zf start`` as the operator escalation that "
                f"resolved the prior respawn-cap cooldown.",
                file=sys.stderr,
            )
            for instance_id in blocked_instances:
                try:
                    self.event_writer.append(ZfEvent(
                        type="worker.state.changed",
                        actor=instance_id,
                        payload={
                            "instance_id": instance_id,
                            "from": "blocked_human",
                            "to": "idle",
                            "reason": (
                                "blocked_human cleared on fresh "
                                "Orchestrator init (zf start = operator "
                                "intervention)"
                            ),
                        },
                    ))
                except Exception:
                    pass
                self._last_worker_state[instance_id] = "idle"
        # 2026-06-10 review P1-4: make restart-as-operator-intervention
        # symmetric. The block above un-parks blocked_human workers, but a
        # remediation safe-halt also emitted dispatch.paused — without the
        # matching resume, restart left workers alive while every task was
        # skipped with reason=dispatch_paused and no obvious cause. Only the
        # safe-halt pause auto-resumes; a maintenance pause stays owned by
        # the maintenance flow (exit_maintenance emits its own resume).
        try:
            latest_pause: ZfEvent | None = None
            for event in reversed(self.event_log.read_all()):
                if event.type == "dispatch.resumed":
                    latest_pause = None
                    break
                if event.type == "dispatch.paused":
                    latest_pause = event
                    break
            if (
                latest_pause is not None
                and isinstance(latest_pause.payload, dict)
                and latest_pause.payload.get("source") == "remediation_cascade"
            ):
                import sys
                print(
                    "⚠ zf start: resuming dispatch paused by a prior "
                    "remediation safe-halt (zf start = operator "
                    "intervention).",
                    file=sys.stderr,
                )
                self.event_writer.append(ZfEvent(
                    type="dispatch.resumed",
                    actor="zf-cli",
                    payload={
                        "reason": (
                            "safe_halt dispatch.paused cleared on fresh "
                            "Orchestrator init (zf start = operator "
                            "intervention)"
                        ),
                        "source": "orchestrator_init",
                        "paused_event_id": latest_pause.id,
                    },
                    causation_id=latest_pause.id,
                ))
        except Exception:
            pass
        # G-WIRE-1: per-task workspace snapshots for scope ratchet.
        # Captured on dispatch, consumed on *.done event. Ignore zaofu
        # internal paths and common build artifacts so dispatch's own
        # writes (.zf/briefings/...) don't trip the violation check.
        scope_ignore_prefixes = [
            ".zf",
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            "node_modules",
            ".venv",
            "venv",
            ".tox",
            "dist",
            "build",
        ]
        try:
            configured_state_prefix = self.state_dir.resolve().relative_to(
                self.project_root
            ).as_posix()
        except ValueError:
            configured_state_prefix = ""
        if (
            configured_state_prefix
            and configured_state_prefix != "."
            and configured_state_prefix not in scope_ignore_prefixes
        ):
            scope_ignore_prefixes.append(configured_state_prefix)
        self._scope_ratchet = ScopeRatchet(
            workspace=self.project_root,
            ignore_prefixes=scope_ignore_prefixes,
        )
        self._scope_snapshots: dict[str, ScopeSnapshot] = {}
        # ZF-HOUSEKEEPING-VISIBLE-001 (doc 42 §2.12, sprint
        # 2026-05-18-1314): per-step dedup map for
        # ``kernel.housekeeping.failed`` events. Without this, a
        # recurring drift / progress-render error would emit one
        # failure event every run_once cycle (~1/s under load) and
        # bury events.jsonl. 60 s/step is enough granularity for an
        # operator to notice and act on, while keeping the log clean
        # if the failure persists.
        self._housekeeping_failure_last: dict[str, float] = {}
        self._housekeeping_failure_dedup_seconds: float = 60.0
        # GAP-1: record HEAD SHA at dispatch time so task completion can
        # compute git log + files_touched for TaskEvidence.
        self._dispatch_heads: dict[str, str] = {}
        self._active_dispatch_ids: dict[str, str] = {}
        # B-STUCK-1: bounded history of recent dispatch_ids per task so a
        # respawn-rotated dispatch_id does not reject a valid in-flight
        # completion (false-stuck livelock). Grace-accept the last few.
        self._recent_dispatch_ids: dict[str, list[str]] = {}
        try:
            for task in self.task_store.list_all():
                if task.active_dispatch_id:
                    self._active_dispatch_ids[task.id] = task.active_dispatch_id
                    self._recent_dispatch_ids[task.id] = [task.active_dispatch_id]
        except Exception:
            pass
        # G-RECYCLE-4: backend → reader. Injected lazily; tests may patch.
        from zf.runtime.backend_session_reader import get_reader_for_backend
        self._session_readers: dict[str, object] = {}
        for r in self.config.roles:
            if r.name == "orchestrator":
                continue
            reader = get_reader_for_backend(r.backend)
            if reader is not None and r.backend not in self._session_readers:
                self._session_readers[r.backend] = reader
        # G-INST-9: migrate legacy kanban.json entries where assigned_to
        # is a bare role.name but the config has expanded it into
        # instance_ids. Pick the first matching instance. No-op for
        # single-instance configs (instance_id == name already).
        self._migrate_legacy_assigned_to()

        # P0-2 (2026-04-20): build the reactor event→action registry.
        # Must run AFTER all instance fields the built-in _on_* handlers
        # depend on are set (task_store, event_log, sm, _discriminator
        # _runner, etc.) — they're referenced when handlers fire, not at
        # registration time, but some may be queried eagerly in testing.
        self.event_registry = self._build_event_registry()

    def _remember_dispatch_id(self, task_id: str, dispatch_id: str) -> None:
        """Set active dispatch_id + keep a bounded recent history (B-STUCK-1)."""
        self._active_dispatch_ids[task_id] = dispatch_id
        if not dispatch_id:
            return
        recent = self._recent_dispatch_ids.setdefault(task_id, [])
        if dispatch_id in recent:
            recent.remove(dispatch_id)
        recent.append(dispatch_id)
        del recent[:-3]  # keep the last 3 dispatch_ids

    def _global_budget_frozen(self) -> bool:
        """Non-emitting global-cap probe (ZF-E2E-MINI-P2).

        _budget_exceeded emits cost.budget.exceeded as a side effect; the
        freeze-silence gate needs a pure read.
        """
        cap = getattr(self.config, "global_budget_usd", None)
        if cap is None or not getattr(
            self.config, "budget_enforcement_enabled", True
        ):
            return False
        try:
            return self.cost_tracker.total_usd() >= float(cap)
        except Exception:
            return False

    def _rollback_inflight_dispatch(self, context: "DispatchContext | None") -> None:
        """ZF-E2E-RACING-P1 (2026-07-11): undo in-flight bookkeeping when a
        paid dispatch fails at the charging primitive.

        active_dispatch_id is persisted before _send_transport_task runs; the
        main _dispatch_task path rolls back in its own except handler, but the
        rework and evidence-reissue paths did not — a budget block (or any
        send failure) left the task claiming an in-flight worker forever, so
        the scheduler, the silent-stall sweep (assigned-without-dispatched
        shape only) and restart reconciliation all skipped it. Rolling back at
        the primitive covers every caller, current and future.
        """
        task_id = getattr(context, "task_id", None)
        dispatch_id = getattr(context, "dispatch_id", None)
        if not task_id or not dispatch_id:
            return
        if self._active_dispatch_ids.get(task_id) == dispatch_id:
            self._active_dispatch_ids.pop(task_id, None)
        try:
            task = self.task_store.get(task_id)
        except Exception:
            return
        if task is not None and (task.active_dispatch_id or "") == dispatch_id:
            self.task_store.update(task_id, active_dispatch_id="")

    def all_roles(self) -> list[RoleConfig]:
        """Return the union of declared roles and runtime-spawned roles.

        ``self.config.roles`` is the immutable view of ``zf.yaml``. Roles
        spawned at runtime (autoscale, etc.) live in ``_runtime_roles``
        keyed by ``instance_id``. Use this helper anywhere code needs to
        see the full live worker set.
        """
        out: list[RoleConfig] = list(self.config.roles)
        seen = {role.instance_id for role in out}
        for instance_id, role in self._runtime_roles.items():
            if instance_id in seen:
                continue
            out.append(role)
            seen.add(instance_id)
        return out

    def _safe_housekeeping(self, step: str, fn: Callable[[], None]) -> None:
        """Run one housekeeping step in ``run_once`` with observable failure.

        Replaces the legacy ``try: fn() except Exception: pass`` pattern.
        Loop integrity (I6 — any plane can recover) is preserved by still
        swallowing the exception, but the failure is **emitted as a
        ``kernel.housekeeping.failed`` event** (I9 — any plane failure
        must be observable). Without this, drift detector / progress
        renderer / orphan sweep regressions stayed silently broken.

        ZF-HOUSEKEEPING-VISIBLE-001 (doc 42 §2.12).
        """
        try:
            fn()
        except Exception as exc:
            self._emit_housekeeping_failure(step, exc)

    def _emit_housekeeping_failure(self, step: str, exc: BaseException) -> None:
        """Emit ``kernel.housekeeping.failed`` (60 s per-step dedup).

        Last-line defence: if event_writer itself crashes, fall back to
        stderr so the failure is not completely invisible. We never
        re-raise — the housekeeping helper's contract is that the
        orchestration loop continues.
        """
        now = time.time()
        last = self._housekeeping_failure_last.get(step, 0.0)
        if now - last < self._housekeeping_failure_dedup_seconds:
            return
        self._housekeeping_failure_last[step] = now
        try:
            self.event_writer.append(ZfEvent(
                type="kernel.housekeeping.failed",
                actor="orchestrator",
                payload={
                    "step": step,
                    "exc_type": type(exc).__name__,
                    # Truncate repr to keep events.jsonl rows bounded —
                    # long Python tracebacks have leaked entire stack
                    # frames into the event log in the past.
                    "exc_repr": repr(exc)[:500],
                },
            ))
        except Exception as emit_exc:
            import sys
            print(
                f"[kernel.housekeeping.failed] step={step} "
                f"exc={exc!r} emit_failed={emit_exc!r}",
                file=sys.stderr,
            )

    def run_once(self, events: list[ZfEvent] | None = None) -> list[OrchestratorDecision]:
        """Run one orchestration cycle.

        If `events` is given (push from EventWatcher), react to those.
        Otherwise read the tail of events.jsonl since the last persisted
        offset stored in session.yaml — never re-react to old events on restart.
        """
        decisions: list[OrchestratorDecision] = []
        # Flush a wake suppressed by coalescing in a prior cycle once its
        # interval has elapsed (driven by either a new event or the idle tick).
        self._flush_pending_layer2_wake()
        decisions.extend(self._capture_logs())
        decisions.extend(self._react_to_events(events))
        decisions.extend(self._reconcile_pending_handoffs())
        self._safe_housekeeping(
            "writer_fanout_task_bindings",
            self._recover_writer_fanout_task_bindings,
        )
        self._safe_housekeeping(
            "writer_fanout_result_replay",
            self._recover_unrecorded_writer_fanout_results,
        )
        self._safe_housekeeping(
            "reader_fanout_trigger_replay",
            self._recover_unstarted_reader_fanouts,
        )
        self._safe_housekeeping(
            "reader_fanout_result_replay",
            self._recover_unrecorded_reader_fanout_results,
        )
        if events is None or any(
            event.type in _CANDIDATE_REWORK_TRIGGER_EVENTS
            for event in events
        ):
            self._safe_housekeeping(
                "candidate_rework",
                self._run_candidate_rework_sweep,
            )
        if events is None or any(
            event.type in ("human.escalate", "diagnosis.completed")
            for event in events
        ):
            self._safe_housekeeping(
                "diagnosis",
                self._run_diagnosis_sweep,
            )
        decisions.extend(self._handle_worker_action_requests())
        decisions.extend(self._autoscale_workers())
        # Context recycle must run before dispatch. Otherwise an idle worker
        # already over the recycle threshold can receive a fresh task and only
        # then be marked pending_recycle.
        self._safe_housekeeping("context_thresholds", self._check_context_thresholds)
        self._safe_housekeeping("pending_recycles", self._check_pending_recycles)
        self._safe_housekeeping("budget_sweep", self._run_budget_sweep)
        decisions.extend(self._dispatch_ready())
        decisions.extend(self._sweep_feature_liveness())
        # G-EVT-2: drain any transport-side events (e.g. StreamJsonTransport's
        # agent.tool.use / agent.usage / agent.text) into events.jsonl. These
        # are produced inline by a Layer 2 dispatch but only buffered in the
        # transport; runtime must explicitly pull them out and route them
        # through the same append + housekeeping pipeline as any other event.
        self._drain_transport_events()
        # doc 69 S-f: emit delivery.phase.* milestones for active features
        # (deterministic, idempotent; counts kernel verdicts, never re-judges).
        self._safe_housekeeping("phase_milestones", self._emit_phase_milestones)
        # G-RECYCLE-4/5: context/recycle is already checked once before
        # dispatch above. Reactor handlers for `agent.usage` trigger the
        # same housekeeping inline when transport drain produces fresh
        # usage events, so a second blanket sweep here is redundant.
        # LH-0.T3: orphan-timeout sweep — in_progress tasks that stalled.
        self._safe_housekeeping("orphaned_tasks", self._check_orphaned_tasks)
        # B18: 看板直建任务的防死卡 SLA(doc 93 §7.4)
        self._safe_housekeeping(
            "unclaimed_new_tasks", self._check_unclaimed_new_tasks,
        )
        self._safe_housekeeping("fanout_timeouts", self._check_fanout_timeouts)
        # 2026-07-03 audit B1: channel replies must not dead-end — bounded
        # redispatch for failed/stuck replies, exhausted event at the cap.
        self._safe_housekeeping(
            "channel_reply_remediation", self._check_channel_reply_remediation,
        )
        # G-WIRE-2: drift detection observation
        self._safe_housekeeping("drift", self._check_drift)
        # G-WIRE-3: refresh policy observation
        self._safe_housekeeping("refresh", self._check_refresh_triggers)
        # Layer 1 housekeeping: refresh progress.md after any state changes
        # this cycle may have caused. Free for Layer 2 to read on its next wake.
        self._safe_housekeeping(
            "regenerate_progress",
            lambda: regenerate_progress(self.state_dir),
        )
        # ZF-ORCH-ACT-001 (doc 39 §4.2): emit one summary event per
        # wake so the decision ledger captures every wake outcome
        # (dispatch / no_action / blocked / escalate / etc.). Closes
        # the "why didn't the orchestrator do anything?" debugging
        # gap. Defensive — emit failure never breaks the wake.
        self._safe_housekeeping(
            "decision_recorded",
            lambda: self._emit_decision_recorded(events, decisions),
        )
        return decisions

    def _emit_decision_recorded(
        self,
        triggers: list[ZfEvent] | None,
        decisions: list[OrchestratorDecision],
    ) -> None:
        """ZF-ORCH-ACT-001: emit one ``orchestrator.decision.recorded``
        event summarising this wake's outcome.

        Decision classification (best-effort, derived from the actions
        of accumulated OrchestratorDecisions):

        - ``dispatch``: at least one decision.action is dispatch / move /
          route-equivalent
        - ``blocked``: at least one decision.action is block
        - ``escalate``: at least one decision.action is escalate /
          respawn — work happened but needs human attention
        - ``no_action``: no decisions accumulated (silent wake)

        Idle-noise filter (stricter): only emit when one of the
        following holds:
        - one or more OrchestratorDecisions actually accumulated
          (real work happened, classify it)
        - the trigger event is in WAKE_PATTERNS (kernel wake — even
          a no-op response deserves an audit row)

        Custom user-injected events that are not in WAKE_PATTERNS
        and produce no decisions are treated as probes and skipped
        — these arrive in test fixtures and ad-hoc tooling and we
        don't want to spam events.jsonl with summary rows for them.

        Trigger context (best-effort): if any pushed event triggered
        this wake, take the first as ``trigger_event_id`` /
        ``trigger_event_type`` for cross-reference. Otherwise the
        wake was a periodic sweep with no specific trigger.
        """
        if not decisions:
            if not triggers:
                return
            from zf.runtime.wake_patterns import WAKE_PATTERNS as _WP
            if not any(
                (ev.type or "") in _WP for ev in triggers
            ):
                return
        trigger_event_id = ""
        trigger_event_type = ""
        trigger_lag_s = None
        if triggers:
            first = triggers[0]
            trigger_event_id = first.id or ""
            trigger_event_type = first.type or ""
            # B6 (R25 ISSUE-006 沉淀④): 决策滞后可观测 — trigger 事件
            # ts 与处理时刻之差。R25 积压 2.9h 当时只能靠人工对时间戳
            # 才发现;有此字段后 lag 进 metrics 可告警。
            try:
                from datetime import datetime

                event_ts = datetime.fromisoformat(str(first.ts))
                trigger_lag_s = round(
                    max(0.0, time.time() - event_ts.timestamp()), 1,
                )
            except (ValueError, TypeError):
                trigger_lag_s = None
        # avbs-r4 F3: 积压自监控。r4 三次死亡螺旋(峰值 lag 4864s)当时
        # 只能靠 operator 手算时间戳发现;超阈值主动发告警事件,自身
        # 10 分钟节流防止告警反哺积压。
        if trigger_lag_s is not None and trigger_lag_s > 300:
            now_monotonic = time.monotonic()
            last_warned = getattr(self, "_last_lag_warning_monotonic", 0.0)
            if now_monotonic - last_warned > 600:
                self._last_lag_warning_monotonic = now_monotonic
                try:
                    self.event_writer.append(ZfEvent(
                        type="runtime.watcher.lag_warning",
                        actor="zf-cli",
                        payload={
                            "trigger_lag_s": trigger_lag_s,
                            "threshold_s": 300,
                            "trigger_event_type": trigger_event_type,
                            "recommendation": (
                                "event backlog growing; consider zf stop/start "
                                "flush at the next safe window"
                            ),
                        },
                    ))
                except Exception:
                    pass
        # Classify the overall decision from collected actions.
        actions = [d.action for d in decisions if d and d.action]
        if any(a in {"dispatch", "move", "route", "spawn"} for a in actions):
            decision_kind = "dispatch"
        elif any(a == "block" for a in actions):
            decision_kind = "blocked"
        elif any(a in {"escalate", "respawn"} for a in actions):
            decision_kind = "escalate"
        elif actions:
            # "capture" / "skip" / other non-trivial bookkeeping
            decision_kind = "wait"
        else:
            decision_kind = "no_action"
        # Pick the first task_id / role from decisions for the
        # primary-target hint. Multi-decision wakes still emit one
        # summary event — the underlying detail is in events.jsonl.
        task_id = ""
        target_role = ""
        for d in decisions:
            if d.task_id and not task_id:
                task_id = d.task_id
            if d.role and not target_role:
                target_role = d.role
            if task_id and target_role:
                break
        # EVAL-DECISION-OUTCOME-001: derive outcome_reason from
        # collected decision reasons + classification context.
        outcome_reason = _classify_outcome_reason(
            decision_kind=decision_kind,
            decisions=decisions,
            triggers=triggers,
        )
        payload = {
            "trigger_event_id": trigger_event_id,
            "trigger_event_type": trigger_event_type,
            "trigger_lag_s": trigger_lag_s,
            "decision": decision_kind,
            "decision_count": len(actions),
            "actions": actions[:20],  # cap list length
            "task_id": task_id,
            "target_role": target_role,
            "reasons": [d.reason for d in decisions[:10] if d.reason],
            "outcome_reason": outcome_reason,
        }
        self.event_writer.append(ZfEvent(
            type="orchestrator.decision.recorded",
            actor="zf-cli",
            task_id=task_id,
            payload=payload,
        ))
        # X15:orchestrator 作为首个注册 consumer,记录续读位(纯投影,
        # 删除即归零重读;不替代 events.jsonl)。
        try:
            if triggers:
                from zf.runtime.consumer_cursor import ConsumerCursorStore

                last = triggers[-1]
                ConsumerCursorStore(self.state_dir).advance(
                    "orchestrator",
                    consumer_kind="kernel",
                    event_id=str(getattr(last, "id", "") or ""),
                    event_ts=str(getattr(last, "ts", "") or ""),
                    seen_delta=len(triggers),
                )
        except Exception:
            pass

        # W3-P0(doc 87 影子):expected_next 的 missing 以 shadow 事件
        # 并行发射,零执行 —— 与现行 13 sweep 的决策对比留给真实 round
        # 归档;分歧即 P1 退场的阻塞证据。best-effort,绝不影响主路。
        try:
            self._emit_reconcile_shadow()
        except Exception:
            pass

        # K5(审计 Q2:'单轮禁循环'是无门 prose):单 wake 决策动作数超
        # 宽松阈值 → WARN(两段式:先观察一周,不杀)。
        if len(actions) > 25:
            self.event_writer.append(ZfEvent(
                type="orchestrator.turn_budget.warn",
                actor="zf-cli",
                payload={
                    "decision_count": len(actions),
                    "threshold": 25,
                    "trigger_event_type": trigger_event_type,
                    "note": "单 wake 决策数异常偏高 —— 检查 Layer 2 是否"
                            "在循环而非'一轮决策后退出'",
                },
            ))

    def _emit_reconcile_shadow(self) -> None:
        """W3-P0:doc 87 reconciler 影子(每 wake 至多一次,3 条 missing 上限)。"""
        import time as _t

        last = getattr(self, "_reconcile_shadow_last_ts", 0.0)
        now = _t.time()
        if now - last < 30.0:
            return
        self._reconcile_shadow_last_ts = now
        from zf.core.workflow.reconcile_expected import (
            approval_hold_keys,
            contract_from_config,
            expected_next,
            fold_state,
        )

        contract = contract_from_config(self.config)
        if not contract.stages:
            return
        from zf.runtime.event_window import read_runtime_events

        events = list(read_runtime_events(self.event_log, self.state_dir))
        traces = fold_state(contract, events)
        missing = expected_next(
            contract, traces, now=now,
            holds=approval_hold_keys(events),
        )
        if not missing:
            return
        self.event_writer.append(ZfEvent(
            type="reconcile.decision.shadow",
            actor="zf-cli",
            payload={
                "mode": "shadow_only",
                "missing": [
                    {
                        "trace_id": m.trace_id,
                        "stage_id": m.stage_id,
                        "expected": list(m.expected),
                        "age_s": round(m.age_s, 1),
                        "rearms": m.rearms,
                    }
                    for m in missing[:3]
                ],
                "missing_total": len(missing),
                "note": "doc 87 P0 影子 —— 与现行 sweep 决策对比;"
                        "零分歧后 P1 退场开闸",
            },
        ))

    def _sweep_feature_liveness(self) -> list[OrchestratorDecision]:
        try:
            from zf.runtime.feature_liveness import sweep_feature_liveness

            events = sweep_feature_liveness(
                state_dir=self.state_dir,
                task_store=self.task_store,
                event_log=self.event_log,
                event_writer=self.event_writer,
            )
        except Exception:
            return []
        decisions: list[OrchestratorDecision] = []
        for event in events:
            if event.type == "feature.status_changed":
                decisions.append(OrchestratorDecision(
                    action="move",
                    task_id=event.task_id,
                    reason="feature liveness sweep closed delivered feature",
                ))
            elif event.type == "feature.liveness.blocked":
                decisions.append(OrchestratorDecision(
                    action="block",
                    task_id=event.task_id,
                    reason=event.payload.get(
                        "reason", "feature liveness blocked",
                    ),
                ))
        return decisions

    def _emit_phase_milestones(self) -> None:
        """doc 69 S-f: emit delivery.phase.{started,evaluated,completed} for
        active features. Pure projection of kernel verdicts → milestone markers
        (mirrors the unconditional ``_sweep_feature_liveness`` precedent);
        ``_safe_housekeeping`` wraps this so an emit failure never breaks the wake.
        """
        from zf.runtime.phase_milestone_emit import emit_phase_milestones
        emit_phase_milestones(
            state_dir=self.state_dir,
            task_store=self.task_store,
            event_log=self.event_log,
            event_writer=self.event_writer,
        )

    def _maybe_auto_ship(self, event: ZfEvent) -> None:
        """Auto-ship candidate when integration completes (r-next B-3).

        Disabled by default; cangjie / autoresearch turn it on via
        ``runtime.git.auto_ship_on_candidate_complete: true``.
        """
        git_config = getattr(self.config.runtime, "git", None)
        if git_config is None:
            return
        if (
            event.type == "judge.passed"
            and isinstance(event.payload, dict)
            and str(event.payload.get("authority") or "") == "compat_projection"
        ):
            return
        # Two triggers, two flags:
        #  - judge.passed → auto_ship_on_judge_passed (terminal gate; the cj-min
        #    safe point — fires AFTER candidate-level review→verify→judge)
        #  - candidate.integration.completed → auto_ship_on_candidate_complete
        #    (cangjie r-next B-3; fires pre-judge, gated on quality_status)
        is_judge = event.type == "judge.passed"
        flag = (
            "auto_ship_on_judge_passed"
            if is_judge
            else "auto_ship_on_candidate_complete"
        )
        if not getattr(git_config, flag, False):
            return
        payload = self._fanout_result_payload(event)
        # candidate.integration.completed real payload shape:
        #   pdd_id: F-xxxxxxxx, branch: candidate/F-xxxxxxxx,
        #   status / event_type / commit / quality_status / failed_task_id
        prefix = git_config.candidate_branch_prefix or "candidate"
        # judge.passed's `target_ref` is flow-dependent: cangjie puts the
        # candidate branch there, but the PRD/issue fanout flow puts the ship
        # DESTINATION (e.g. "main"). Only trust it as the candidate source when
        # it actually names a candidate branch; otherwise fall through to the
        # `candidate/{feature_id}` derivation below (LB-1: PRD light judge.passed
        # carried target_ref="main", which auto-ship wrongly took as the source).
        raw_target_ref = str(payload.get("target_ref") or "").strip()
        target_ref = (
            str(payload.get("branch") or "").strip()
            or str(payload.get("candidate_branch") or "").strip()
            or str(payload.get("candidate_ref") or "").strip()
            or (raw_target_ref if raw_target_ref.startswith(f"{prefix}/") else "")
            or str(payload.get("feature_branch") or "").strip()
        )
        feature_id = (
            str(payload.get("pdd_id") or "").strip()
            or str(payload.get("feature_id") or "").strip()
        )
        if not target_ref and feature_id:
            target_ref = f"{prefix}/{feature_id}"
        if not target_ref:
            return
        # candidate.integration.completed fires pre-judge, so it must gate on
        # the integration's quality_status. judge.passed IS the terminal quality
        # gate (after review→verify→judge), so no extra check is applied there.
        if not is_judge:
            quality_status = str(payload.get("quality_status") or "").strip().lower()
            if quality_status and quality_status not in {"passed", "ok", "success"}:
                return
        try:
            from zf.runtime.ship import ShipService
            project_root = getattr(
                self, "project_root", self.state_dir.parent,
            ).resolve()
            ShipService(
                state_dir=self.state_dir,
                project_root=project_root,
                config=self.config,
                event_log=self.event_log,
            ).ship(
                target_ref=target_ref,
                event_writer=self.event_writer,
                causation_id=event.id,
                correlation_id=event.correlation_id,
            )
        except Exception as exc:
            # Auto-ship is opportunistic — never block the dispatch loop on a
            # ship failure. ShipService itself emits ship.blocked / ship.conflict
            # for known failure paths, but defense-in-depth: any uncaught
            # exception escaping ship() (e.g. unexpected git error, bad
            # config, ImportError) must still surface as a ship.failed event so
            # operators see it instead of a silent stall.
            try:
                self.event_writer.append(ZfEvent(
                    type="ship.failed",
                    actor="zf-cli",
                    payload={
                        "target_ref": target_ref,
                        "feature_id": feature_id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "source": "auto_ship_exception",
                    },
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                ))
            except Exception:
                pass

    def _maybe_complete_run_goal(self, event: ZfEvent) -> None:
        from zf.runtime.goal_completion_gate import maybe_complete_run_goal

        maybe_complete_run_goal(self, event)

    def _run_zaofu_bug_scan(self) -> None:
        """β-1 (2026-05-17): periodic scan for known zaofu kernel failure
        signatures over the recent event tail. On match, emit
        ``zaofu.bug.detected`` so operator playbook (β-2) and
        ``zf bug-fix-cycle`` CLI (β-3) can drive the fix cycle.

        Cheap; safe to call from every tick. Deduplicates by
        evidence_event_ids signature so we don't spam the same match.
        """
        from zf.runtime.zaofu_bug_signatures import scan_zaofu_bugs

        try:
            from zf.runtime.event_window import read_runtime_events

            recent = read_runtime_events(self.event_log, self.state_dir)
        except Exception:
            return
        try:
            matches = scan_zaofu_bugs(recent)
        except Exception:
            return

        # Dedup against previously-emitted detections in the same window:
        seen_evidence: set[frozenset[str]] = set()
        try:
            for ev in recent:
                if ev.type != "zaofu.bug.detected":
                    continue
                p = ev.payload if isinstance(ev.payload, dict) else {}
                evidence = frozenset(str(x) for x in (p.get("evidence_event_ids") or []))
                if evidence:
                    seen_evidence.add(evidence)
        except Exception:
            pass

        for match in matches:
            sig_evidence = frozenset(match.evidence_event_ids)
            if sig_evidence in seen_evidence:
                continue
            try:
                self.event_writer.append(ZfEvent(
                    type="zaofu.bug.detected",
                    actor="zf-cli",
                    payload={
                        "signature": match.signature,
                        "confidence": match.confidence,
                        "evidence_event_ids": list(match.evidence_event_ids),
                        "suggested_fix_area": match.suggested_fix_area,
                        "run_state_snapshot": match.run_state_snapshot,
                    },
                ))
            except Exception:
                pass

    def _run_budget_sweep(self) -> None:
        """FIX-13①(bizsim r4 F13):spent≥cap 时主动发 cost.budget.exceeded,
        不等下一次派发——在途 turns 的燃烧会静默穿透上限($1150→$1166 实锚),
        sweep 让越限即刻可见。复用 _emit_cost_block 的 (scope, role) 冷却。
        """
        if not getattr(self.config, "budget_enforcement_enabled", True):
            return
        cap = getattr(self.config, "global_budget_usd", None)
        if cap is None:
            return
        try:
            total = self.cost_tracker.total_usd()
        except Exception:
            return
        if total >= cap:
            import time as _time

            self._emit_cost_block(
                scope="global_sweep",
                role_name="*",
                budget=cap,
                current=total,
                now=_time.time(),
            )

    def _run_heartbeat_sweep(self) -> None:
        """α-3 (2026-05-17): periodic sweep over role_sessions.yaml.

        Called from EventWatcher.on_tick (~60s cadence). Emits
        ``worker.probe.silent`` for busy instances past the silent
        threshold; the existing stuck-recovery path consumes any
        ``worker.stuck`` upgrades. Idle instances surface as candidates
        for proactive dispatch (caller decides via backlog scheduler).

        Errors swallowed: sweep is opportunistic background work and
        must not break the dispatch loop.
        """
        from zf.core.state.role_sessions import RoleSessionRegistry
        from zf.runtime.heartbeat_sweep import sweep_heartbeats

        try:
            registry = RoleSessionRegistry(
                self.state_dir / "role_sessions.yaml",
                project_root=str(self.project_root),
            )
            result = sweep_heartbeats(
                registry=registry,
                stuck_thresholds_s={
                    role.instance_id: float(role.stuck_threshold_seconds)
                    for role in self.all_roles()
                },
            )
        except Exception:
            return

        # ω-1.c (2026-05-18): per-(instance, signal) cooldown to prevent
        # 60s-tick stuck spam. 300s window means a genuinely stuck worker
        # still surfaces ≤1 event / 5min for ops dashboards.
        import time as _periodic_time
        _SWEEP_DEDUP_COOLDOWN_S = 300.0
        _now_mono = _periodic_time.monotonic()

        for instance_id in result.silent_instances:
            if not self._heartbeat_current_task_still_owned(
                registry, instance_id,
            ):
                self._sweep_signal_last_emit_at.pop(
                    (instance_id, "worker.probe.silent"), None
                )
                continue
            key = (instance_id, "worker.probe.silent")
            last = self._sweep_signal_last_emit_at.get(key, 0.0)
            if _now_mono - last < _SWEEP_DEDUP_COOLDOWN_S:
                continue
            self._sweep_signal_last_emit_at[key] = _now_mono
            try:
                age = result.examined.get(instance_id)
                self.event_writer.append(ZfEvent(
                    type="worker.probe.silent",
                    actor="zf-cli",
                    payload={
                        "instance_id": instance_id,
                        "heartbeat_age_s": round(age, 1) if age else None,
                        "source": "heartbeat_sweep",
                    },
                ))
            except Exception:
                pass

        for instance_id in result.stuck_instances:
            if not self._heartbeat_current_task_still_owned(
                registry, instance_id,
            ):
                self._sweep_signal_last_emit_at.pop(
                    (instance_id, "worker.stuck"), None
                )
                continue
            if self._heartbeat_worker_finished_current_dispatch(instance_id):
                # A worker that already produced a result for its current
                # dispatch (e.g. a writer that emitted dev.build.done and is
                # awaiting review/integration) has no active work and
                # legitimately stops heartbeating. Don't heartbeat-stuck it —
                # else a completed writer respawn-cascades and can safe-halt the
                # run on a process death (R18: 20× false worker.stuck). Mirrors
                # the pane-output stuck exemption (fix 0886466).
                self._sweep_signal_last_emit_at.pop(
                    (instance_id, "worker.stuck"), None
                )
                continue
            active_turn = self._active_provider_turn(instance_id)
            if active_turn:
                turn_age_s = active_turn.get("age_s")
                grace_s = self._provider_turn_stuck_grace_seconds(instance_id)
                if turn_age_s is None or turn_age_s < grace_s:
                    self._sweep_signal_last_emit_at.pop(
                        (instance_id, "worker.stuck"), None
                    )
                    self._emit_provider_turn_probe(
                        instance_id=instance_id,
                        heartbeat_age_s=result.examined.get(instance_id),
                        turn=active_turn,
                        grace_s=grace_s,
                        now_mono=_now_mono,
                    )
                    continue
            key = (instance_id, "worker.stuck")
            last = self._sweep_signal_last_emit_at.get(key, 0.0)
            if _now_mono - last < _SWEEP_DEDUP_COOLDOWN_S:
                continue
            self._sweep_signal_last_emit_at[key] = _now_mono
            try:
                age = result.examined.get(instance_id)
                self.event_writer.append(ZfEvent(
                    type="worker.stuck",
                    actor="zf-cli",
                    payload={
                        "instance_id": instance_id,
                        "heartbeat_age_s": round(age, 1) if age else None,
                        "source": "heartbeat_sweep",
                        "reason": (
                            f"no heartbeat in {round(age, 1) if age else '?'}s "
                            "(α-3 heartbeat-driven, replaces B-NEW-15 "
                            "4-min wall-clock fallback)"
                        ),
                    },
                ))
            except Exception:
                pass

        # α-3+ (2026-05-17): proactive dispatch path. For each idle
        # worker observed, if the task store has ≥1 ready backlog item,
        # emit one fleet-level worker.probe.idle so the next run_once cycle
        # picks it up via the existing _dispatch_ready_tasks path. One
        # run_once already scans every ready task/worker; emitting one event
        # per idle pane creates an O(workers) wake backlog that can starve the
        # actual workflow event on multi-kind projects.
        if result.idle_instances:
            try:
                ready_count = len(self.task_store.ready())
            except Exception:
                ready_count = 0
            if ready_count > 0:
                idle_instances = sorted(set(result.idle_instances))
                key = ("fleet", "worker.probe.idle")
                last = self._sweep_signal_last_emit_at.get(key, 0.0)
                if _now_mono - last < _SWEEP_DEDUP_COOLDOWN_S:
                    return
                self._sweep_signal_last_emit_at[key] = _now_mono
                try:
                    self.event_writer.append(ZfEvent(
                        type="worker.probe.idle",
                        actor="zf-cli",
                        payload={
                            "instance_id": idle_instances[0],
                            "idle_instances": idle_instances,
                            "idle_worker_count": len(idle_instances),
                            "ready_backlog_count": ready_count,
                            "source": "heartbeat_sweep",
                        },
                    ))
                except Exception:
                    pass

    def _provider_turn_stuck_grace_seconds(self, instance_id: str) -> float:
        """Extra grace while a provider turn is visibly in flight.

        During a single long model turn the worker cannot emit heartbeat
        commands. Treat the provider turn lifecycle as liveness evidence,
        but still retain a hard upper bound so a truly hung provider can
        recover eventually.
        """
        threshold_s = 0.0
        for role in self.all_roles():
            if role.instance_id == instance_id:
                try:
                    threshold_s = float(role.stuck_threshold_seconds)
                except (TypeError, ValueError):
                    threshold_s = 0.0
                break
        if threshold_s <= 0:
            threshold_s = 300.0
        return max(threshold_s * 4.0, 900.0)

    def _active_provider_turn(
        self,
        instance_id: str,
    ) -> dict[str, object] | None:
        """Return the current provider turn without trusting worker heartbeats.

        Codex exposes an explicit prompt/stop lifecycle. Claude Code does not
        publish that lifecycle through the current hook contract, so its
        kernel dispatch remains the bounded turn anchor until the worker emits
        a result. This covers long model-only thinking windows while the hard
        provider grace still lets a genuinely hung turn recover.
        """
        codex_turn = self._active_codex_turn(instance_id)
        if codex_turn is not None:
            codex_turn["provider"] = "codex"
            return codex_turn

        role = next(
            (role for role in self.all_roles() if role.instance_id == instance_id),
            None,
        )
        if role is None or str(role.backend or "") not in {"claude", "claude-code"}:
            return None
        task = self._active_task_for_instance(instance_id)
        if task is None or self._heartbeat_worker_finished_current_dispatch(instance_id):
            return None
        dispatch = self._latest_dispatch_event_for_task(task.id)
        if dispatch is None:
            return None
        payload = dispatch.payload if isinstance(dispatch.payload, dict) else {}
        started_at = _parse_event_ts(dispatch.ts)
        age_s = None
        if started_at is not None:
            age_s = (datetime.now(timezone.utc) - started_at).total_seconds()
        return {
            "provider": str(role.backend or "claude-code"),
            "session_id": "",
            "turn_id": str(
                payload.get("run_id")
                or payload.get("dispatch_id")
                or dispatch.id
            ),
            "started_at": dispatch.ts,
            "age_s": age_s,
        }

    def _active_codex_turn(self, instance_id: str) -> dict[str, object] | None:
        """Return the newest open Codex turn for a worker, if any."""
        try:
            from zf.runtime.event_window import read_runtime_events

            events = read_runtime_events(self.event_log, self.state_dir)
        except Exception:
            return None

        active: dict[tuple[str, str], ZfEvent] = {}
        for event in events:
            if event.actor != instance_id:
                continue
            if event.type not in {
                "codex.hook.user_prompt_submit",
                "codex.hook.stop",
            }:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            session_id = str(payload.get("session_id") or "").strip()
            turn_id = str(payload.get("turn_id") or "").strip()
            if not session_id:
                continue
            if event.type == "codex.hook.user_prompt_submit":
                if turn_id:
                    active[(session_id, turn_id)] = event
                continue
            if turn_id:
                active.pop((session_id, turn_id), None)
            else:
                for key in list(active):
                    if key[0] == session_id:
                        active.pop(key, None)

        if not active:
            return None

        latest_key, latest_event = max(
            active.items(),
            key=lambda item: _parse_event_ts(item[1].ts) or datetime.min.replace(
                tzinfo=timezone.utc
            ),
        )
        started_at = _parse_event_ts(latest_event.ts)
        age_s = None
        if started_at is not None:
            age_s = (datetime.now(timezone.utc) - started_at).total_seconds()
        return {
            "session_id": latest_key[0],
            "turn_id": latest_key[1],
            "started_at": latest_event.ts,
            "age_s": age_s,
        }

    def _emit_provider_turn_probe(
        self,
        *,
        instance_id: str,
        heartbeat_age_s: float | None,
        turn: dict[str, object],
        grace_s: float,
        now_mono: float,
    ) -> None:
        key = (instance_id, "worker.probe.silent")
        last = self._sweep_signal_last_emit_at.get(key, 0.0)
        if now_mono - last < 300.0:
            return
        self._sweep_signal_last_emit_at[key] = now_mono
        try:
            self.event_writer.append(ZfEvent(
                type="worker.probe.silent",
                actor="zf-cli",
                payload={
                    "instance_id": instance_id,
                    "heartbeat_age_s": (
                        round(heartbeat_age_s, 1)
                        if heartbeat_age_s is not None else None
                    ),
                    "source": "heartbeat_sweep",
                    "reason": "provider_turn_in_flight",
                    "provider": turn.get("provider", "codex"),
                    "session_id": turn.get("session_id", ""),
                    "turn_id": turn.get("turn_id", ""),
                    "turn_age_s": (
                        round(float(turn["age_s"]), 1)
                        if isinstance(turn.get("age_s"), int | float)
                        else None
                    ),
                    "stuck_grace_s": grace_s,
                },
            ))
        except Exception:
            pass

    def _run_candidate_rework_sweep(self) -> None:
        """Self-healing: close the candidate-level rework loop.

        review/verify/judge reject the WHOLE candidate (task_id=None), so the
        per-task rework path (`_on_review_rejected`) no-ops and the run stalls
        forever after the first rejection. Re-trigger the implementation stage
        (re-emit task_map.ready with a fresh event so _maybe_start_writer_fanout
        re-dispatches writers) carrying the reviewers' findings, capped at
        max_attempts (then escalate). Time-driven so a missed event wake still
        recovers. Best-effort: never break the tick.
        """
        try:
            from zf.runtime.event_window import read_runtime_events
            from zf.runtime.run_manager import (
                _pending_candidate_recovery_actions,
                run_manager_tick,
            )

            events = read_runtime_events(self.event_log, self.state_dir)
            if self._resume_unrecorded_writer_fanout_results(events):
                events = read_runtime_events(self.event_log, self.state_dir)
            self._resume_unstarted_rework_task_maps(events)
            events = read_runtime_events(self.event_log, self.state_dir)
            # ``run_once()`` invokes this sweep from the five-second idle
            # tick.  Calling the full Run Manager unconditionally here
            # bypasses TickServiceState coalescing and creates a permanent
            # run.manager.tick feedback loop even for a healthy run.  The
            # candidate action builder already deduplicates applied actions;
            # only enter the bounded Run Manager path while it reports an
            # unresolved candidate-level recovery action.
            pending_actions = _pending_candidate_recovery_actions(
                self.state_dir,
                self.config,
                events,
                project_root=self.project_root,
            )
            if pending_actions:
                run_manager_tick(
                    state_dir=self.state_dir,
                    writer=self.event_writer,
                    config=self.config,
                    project_root=self.project_root,
                    event_log=self.event_log,
                    auto_execute=True,
                    spawn_repairs=False,
                )
            events = read_runtime_events(self.event_log, self.state_dir)
            self._resume_unstarted_rework_task_maps(events)
            return
        except Exception as exc:
            self._emit_housekeeping_failure("candidate_rework", exc)
            return

    def _run_diagnosis_sweep(self) -> None:
        """Tier-2 诊断性介入(doc 131 §5;task 2026-07-06-0930)。

        不收敛升级信号(judge_nonconvergence / rework exhausted)按 stall
        指纹判重铸 diagnosis.requested——诊断 reader stage(项目配置)据此
        派发;diagnosis.completed 判 needs_owner 的结论升级 owner。
        propose-only:kernel 不执行诊断报告里的 proposed_commands。
        """
        try:
            from zf.runtime.diagnosis import (
                plan_diagnosis_requests,
                plan_needs_owner_escalations,
            )
            from zf.runtime.event_window import read_runtime_events

            events = read_runtime_events(self.event_log, self.state_dir)
            for payload in plan_diagnosis_requests(events):
                source_id = str(payload.get("source_event_id") or "") or None
                self.event_writer.append(ZfEvent(
                    type="diagnosis.requested",
                    actor="zf-cli",
                    payload=payload,
                    causation_id=source_id,
                ))
            for payload in plan_needs_owner_escalations(events):
                self.event_writer.append(ZfEvent(
                    type="human.escalate",
                    actor="zf-cli",
                    payload=payload,
                    causation_id=str(
                        payload.get("diagnosis_event_id") or "",
                    ) or None,
                ))
            return
        except Exception as exc:
            self._emit_housekeeping_failure("diagnosis", exc)
            return

    def _maybe_resynth_on_replan(self, plan, events: list[ZfEvent]) -> None:
        """Re-emit the configured plan-synth trigger for plan-level rework."""
        from zf.runtime.replan_resynth import build_replan_resynth_event

        event = build_replan_resynth_event(
            plan=plan,
            events=events,
            config=self.config,
        )
        if event is not None:
            self.event_writer.append(event)

    def _resume_unstarted_rework_task_maps(self, events: list[ZfEvent]) -> None:
        """Start rework task maps that were emitted by an older sweep.

        Before the sweep synchronously started writer fanout, it could append
        ``task_map.ready`` and persist the handling marker while no fanout ever
        consumed it. A later restart must not consider that rework complete
        unless the ready event produced a fanout outcome.
        """
        outcomes = {"fanout.started", "fanout.cancelled", "fanout.serialize"}
        outcomes_by_trigger: dict[str, list[ZfEvent]] = {}
        for event in events:
            if event.type not in outcomes or not isinstance(event.payload, dict):
                continue
            trigger_event_id = str(event.payload.get("trigger_event_id") or "")
            if trigger_event_id:
                outcomes_by_trigger.setdefault(trigger_event_id, []).append(event)
        for event in events:
            if event.type != "task_map.ready" or not isinstance(event.payload, dict):
                continue
            if not event.payload.get("rework_of"):
                continue
            event_outcomes = outcomes_by_trigger.get(event.id, [])
            if event_outcomes and not self._rework_task_map_stale_retryable(
                event,
                event_outcomes,
            ):
                continue
            self._maybe_start_writer_fanout(event)

    def _rework_task_map_stale_retryable(
        self,
        event: ZfEvent,
        outcomes: list[ZfEvent],
    ) -> bool:
        latest = outcomes[-1]
        latest_payload = latest.payload if isinstance(latest.payload, dict) else {}
        if (
            latest.type != "fanout.cancelled"
            or latest_payload.get("reason") != "stale_task_map"
        ):
            return False
        try:
            from zf.runtime.writer_fanout_admission import (
                admit_writer_fanout,
                load_writer_task_map,
            )

            pdd_id = self._fanout_pdd_id(event)
            for stage in getattr(self.config.workflow, "stages", []):
                if (
                    stage.topology != "fanout_writer_scoped"
                    or stage.trigger != event.type
                ):
                    continue
                loaded = load_writer_task_map(
                    stage=stage,
                    event=event,
                    pdd_id=pdd_id,
                    state_dir=self.state_dir,
                    project_root=self.project_root,
                    pipeline_spec=self._lane_pipeline_for_trigger(
                        getattr(event, "type", ""),
                    ),
                    candidate_quality_source=str(getattr(
                        self.config.workflow,
                        "candidate_quality_source",
                        "auto",
                    ) or "auto"),
                    work_units_config=getattr(
                        self.config.workflow, "work_units", None,
                    ),
                )
                if admit_writer_fanout(
                    task_store=self.task_store,
                    loaded=loaded,
                ).passed:
                    return True
        except Exception:
            return False
        return False

    def _run_dispatch_sweep(self) -> None:
        """TR-DISPATCH-SILENT-STALL-001 (2026-05-21): periodic sweep over
        recent events to detect task.assigned without matching
        task.dispatched within the threshold window (#G site 6).

        Complements 4de1ff7 (5 _dispatch_ready internal silent-stall
        sites): when the kernel reactor never re-ticks after writing
        task.assigned (no further events arrive to fire it), the task
        sits indefinitely. This sweep is time-driven, not event-driven,
        so it surfaces the stall and emits dispatch.silent_stall →
        operator/agent visibility + orchestrator wake.

        Per-(task_id, assignee) cooldown reuses the ω-1.c
        `_sweep_signal_last_emit_at` map to prevent re-emit spam on
        every 60s sweep.

        Errors swallowed: opportunistic background work; must not
        break the main dispatch loop.

        Cangjie evidence: /path/to/example-project/.zf/events-
                          r1-final.jsonl.bak (L179 task.assigned
                          role=dev @ 06:56:00, no follow-up dispatch).
        """
        from zf.runtime.dispatch_sweep import (
            sweep_dead_dispatches,
            sweep_silent_dispatches,
        )

        try:
            # Read recent events (tail window). EventLog can grow large;
            # we only need the tail to detect recent silent stalls.
            events = list(self.event_log.read_all())
            # Window: only last ~500 events are relevant for a 30s threshold
            # (assuming a typical cangjie round has < 100 events per minute).
            if len(events) > 500:
                events = events[-500:]
            result = sweep_silent_dispatches(events=events)
        except Exception:
            return

        # Reuse ω-1.c dedup pattern (5min cooldown per signal key).
        import time as _periodic_time
        _DISPATCH_SWEEP_DEDUP_COOLDOWN_S = 300.0
        _now_mono = _periodic_time.monotonic()

        for task_id, assignee, age_seconds in result.silent_stalls:
            key = (assignee, "dispatch.silent_stall", task_id)
            last = self._sweep_signal_last_emit_at.get(key, 0.0)
            if _now_mono - last < _DISPATCH_SWEEP_DEDUP_COOLDOWN_S:
                continue
            self._sweep_signal_last_emit_at[key] = _now_mono
            try:
                self.event_writer.append(ZfEvent(
                    type="dispatch.silent_stall",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "task_id": task_id,
                        "assignee": assignee,
                        "assigned_age_s": round(age_seconds, 1),
                        "source": "dispatch_sweep",
                        "reason": (
                            f"task.assigned for {task_id} → {assignee} "
                            f"observed {round(age_seconds, 1)}s ago "
                            f"with no matching task.dispatched "
                            f"(TR-DISPATCH-SILENT-STALL-001, follow-up to "
                            f"commit 4de1ff7's 5 _dispatch_ready sites)"
                        ),
                    },
                ))
            except Exception:
                pass

        # ZF-E2E-RACING-P1: second stall shape — task.dispatched exists but
        # the worker session died (restart / pane reset). Same event window,
        # same per-key cooldown; a distinct source lets consumers tell the
        # two shapes apart.
        try:
            inflight = [
                (task.id, task.assigned_to or "", task.active_dispatch_id or "")
                for task in self.task_store.filter(status="in_progress")
                if task.active_dispatch_id
            ]
            from zf.runtime.shutdown import _STAGE_PROGRESS_EVENTS

            # Progress evidence must be authoritative, not window-bound: the
            # 500-event tail floods long before in-flight bookkeeping clears
            # (live: dev.build.done rolled out of the window and the false
            # stall returned, re-arming RM resume). events_for_task is
            # index-backed and cheap for the handful of in-flight tasks.
            progressed = set()
            for task_id, _assignee, _dispatch in inflight:
                try:
                    for ev in self.event_log.events_for_task(task_id):
                        if ev.type in _STAGE_PROGRESS_EVENTS:
                            progressed.add(task_id)
                            break
                except Exception:
                    continue
            dead_result = sweep_dead_dispatches(
                inflight=inflight, events=events,
                progressed_task_ids=progressed,
                assignee_thresholds_s=self._thinking_dispatch_thresholds(),
            )
        except Exception:
            return
        for task_id, assignee, dispatch_id, age_seconds in dead_result.dead_dispatches:
            key = (assignee, "dispatch.dead_dispatch", task_id)
            last = self._sweep_signal_last_emit_at.get(key, 0.0)
            if _now_mono - last < _DISPATCH_SWEEP_DEDUP_COOLDOWN_S:
                continue
            self._sweep_signal_last_emit_at[key] = _now_mono
            try:
                self.event_writer.append(ZfEvent(
                    type="dispatch.silent_stall",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "task_id": task_id,
                        "assignee": assignee,
                        "dispatch_id": dispatch_id,
                        "silent_age_s": round(age_seconds, 1),
                        "source": "dead_dispatch_sweep",
                        "reason": (
                            f"in-flight dispatch {dispatch_id} for {task_id} → "
                            f"{assignee} shows no worker activity for "
                            f"{round(age_seconds, 1)}s after task.dispatched "
                            f"(ZF-E2E-RACING-P1: worker session likely dead)"
                        ),
                    },
                ))
            except Exception:
                pass

    def _thinking_dispatch_thresholds(self) -> dict[str, float]:
        """Per-worker inactivity thresholds for long-thinking providers."""
        try:
            lease_grace = float(
                getattr(self.config.workflow, "attempt_lease_grace_s", 900.0)
                or 900.0
            )
        except (TypeError, ValueError):
            lease_grace = 900.0
        thresholds: dict[str, float] = {}
        for role in self.config.roles:
            if str(role.backend or "") not in {"claude", "claude-code", "codex"}:
                continue
            threshold = max(
                float(role.stuck_threshold_seconds or 0.0),
                lease_grace,
            )
            thresholds[role.instance_id] = threshold
            thresholds.setdefault(role.name, threshold)
        return thresholds

    def _heartbeat_current_task_still_owned(
        self,
        registry: RoleSessionRegistry,
        instance_id: str,
    ) -> bool:
        """Return false only for a confirmed stale handoff heartbeat."""
        try:
            _, payload = registry.get_last_heartbeat(instance_id)
        except Exception:
            return True
        if not isinstance(payload, dict):
            return True
        current_task_id = str(
            payload.get("current_task_id") or payload.get("task_id") or ""
        ).strip()
        if not current_task_id:
            return True
        fanout_id = str(payload.get("fanout_id") or "").strip()
        child_id = str(payload.get("child_id") or "").strip()
        if current_task_id.startswith("fanout:") or (fanout_id and child_id):
            try:
                active = self._active_fanout_child_for_instance(instance_id)
            except Exception:
                return True
            if not active:
                return False
            if fanout_id and active.get("fanout_id") != fanout_id:
                return False
            if child_id and active.get("child_id") != child_id:
                return False
            return True
        try:
            tasks = self.task_store.list_all()
        except Exception:
            return True
        task = next((t for t in tasks if t.id == current_task_id), None)
        if task is None:
            return False
        if task.status != "in_progress":
            return False
        if (task.assigned_to or "") == instance_id:
            return True
        try:
            latest_dispatched = self._latest_dispatched_per_task()
        except Exception:
            latest_dispatched = {}
        return latest_dispatched.get(task.id, "") == instance_id

    def _heartbeat_worker_finished_current_dispatch(self, instance_id: str) -> bool:
        """True if the worker already produced a result for its current dispatch.

        A writer that emitted dev.build.done (whose task sits in_progress
        awaiting review/integration) has no active work and legitimately stops
        heartbeating. The heartbeat-age stuck sweep must skip it — mirrors the
        pane-output stuck exemption (fix 0886466) so both stuck mechanisms agree.
        """
        task = self._active_task_for_instance(instance_id)
        if task is None:
            return False
        dispatch = self._latest_dispatch_event_for_task(task.id)
        dispatch_id = ""
        if dispatch is not None and isinstance(dispatch.payload, dict):
            dispatch_id = str(
                dispatch.payload.get("dispatch_id")
                or getattr(task, "active_dispatch_id", "")
                or ""
            )
        return self._latest_unrejected_progress_event_for_dispatch(
            task.id, dispatch_id,
        ) is not None

    def _maybe_run_static_gate(self, event: ZfEvent) -> None:
        """P3/K5 (docs/impl/22): run quality_gates.static between
        ⑤ implement and ⑦ code_review.

        Triggered from ``_apply_housekeeping`` on ``dev.build.done``.
        Opt-in via ``workflow.dag.enabled`` (defaults to disabled so old
        projects keep current behavior, where static checks only run at
        candidate integration time in ``candidates.py``).

        Emits one of:
          - ``static_gate.passed``  — all required_checks exited 0
          - ``static_gate.failed``  — at least one check failed; rework_routing
                                     handles dispatch back to dev
          - ``static_gate.skipped`` — DAG disabled / static gate disabled /
                                     no checks configured
        """
        from zf.runtime.static_gate import (
            build_static_gate_event,
            run_static_gate,
        )

        # #J fix (TR-STATIC-GATE-DISABLED-EMIT-SKIPPED-001, cangjie
        # 2026-05-21 observation-J): only short-circuit when DAG is
        # disabled (legacy mode) or event is not dev.build.done. If
        # DAG is enabled, always run static_gate — `run_static_gate`
        # itself handles the enabled=False case by returning
        # skipped=True, and `build_static_gate_event` then emits
        # static_gate.skipped so downstream roles (review.triggers=
        # static_gate.passed) wake correctly even when the gate is
        # toggled off for spec-only baseline.
        if event.type != "dev.build.done":
            return
        dag = getattr(getattr(self.config, "workflow", None), "dag", None)
        if dag is None or not getattr(dag, "enabled", False):
            return  # legacy mode (no DAG): silent no-op (backwards compat)
        if self._run_workflow_graph_static_gate(event):
            return

        # #E fix (TR-STATIC-GATE-PER-TASK-OVERRIDE-001, cangjie
        # 2026-05-21 observation-E): check task contract per-task
        # quality_gates_override before running yaml-level gate.
        # When task.contract.quality_gates_override.static.enabled=False,
        # skip yaml required_checks and emit static_gate.skipped directly.
        # Empty override dict preserves yaml-default behavior.
        override_skipped_result = self._maybe_static_gate_override_skipped(
            event,
        )
        if override_skipped_result is not None:
            gate_project_root = self._static_gate_project_root(event)
            gate_event = build_static_gate_event(
                override_skipped_result, trigger_event=event,
            )
            gate_event.payload["workdir"] = str(gate_project_root)
            try:
                self.event_writer.append(gate_event)
            except Exception:
                pass
            return

        gate_project_root = self._static_gate_project_root(event)
        result = run_static_gate(
            config=self.config,
            project_root=gate_project_root,
        )
        gate_event = build_static_gate_event(result, trigger_event=event)
        gate_event.payload["workdir"] = str(gate_project_root)
        try:
            self.event_writer.append(gate_event)
        except Exception:
            pass

    def _static_gate_project_root(self, event: ZfEvent) -> Path:
        """Return the tree that static_gate should verify for dev output.

        In worktree mode, `dev.build.done` is first normalized into a
        task-ref entry. Static checks must run against that writer worktree,
        not the operator project root, otherwise they may validate stale
        source while also scanning runtime workdirs under `.zf*`.
        """
        if event.task_id:
            entry = self._task_ref_entry(event.task_id)
            payload = event.payload if isinstance(event.payload, dict) else {}
            raw_workdir = str(entry.get("workdir") or payload.get("workdir") or "")
            if raw_workdir:
                path = Path(raw_workdir)
                if not path.is_absolute():
                    path = self.project_root / path
                try:
                    resolved = path.resolve()
                    allowed_root = (self.state_dir / "workdirs").resolve()
                    if (
                        resolved.is_relative_to(allowed_root)
                        and (resolved / ".git").exists()
                    ):
                        return resolved
                except Exception:
                    pass
        return self.project_root

    def _maybe_static_gate_override_skipped(
        self, event: ZfEvent,
    ) -> "StaticGateResult | None":
        """#E fix: return a skipped StaticGateResult if task contract
        explicitly overrides static gate to disabled. Otherwise None
        (caller proceeds with yaml-level gate).
        """
        from zf.runtime.static_gate import StaticGateResult

        task_id = event.task_id
        if not task_id:
            return None
        try:
            task = self.task_store.get(task_id)
        except Exception:
            return None
        if task is None or task.contract is None:
            return None
        override = getattr(task.contract, "quality_gates_override", {}) or {}
        static_override = override.get("static") or {}
        if not isinstance(static_override, dict):
            return None
        if static_override.get("enabled") is False:
            return StaticGateResult(
                passed=True,
                skipped=True,
                skip_reason=(
                    "per-task contract.quality_gates_override.static.enabled=False "
                    "(#E cangjie 2026-05-21 doc-type task opt-out)"
                ),
            )
        return None

    def _drain_transport_events(self) -> None:
        try:
            pending = self.transport.poll_events()
        except Exception:
            pending = []
        for ev in pending:
            try:
                self.event_writer.append(ev)
            except Exception:
                continue
            # B11: arm Layer 2 cool-down on transport-emitted block signals.
            if ev.type in {"agent.api_blocked", "agent.timeout"}:
                self._layer2_blocked_until = (
                    time.time() + self._layer2_cooldown_s
                )
            # Mechanical state writes (cost/memory/contract) for the new event.
            self._apply_housekeeping(ev)
            # E1 fix: mark as processed so _react_to_events on the next
            # cycle doesn't housekeep the same event a second time
            # (inflated cost.jsonl by 2× before this).
            self._processed_event_ids.add(ev.id)

    def _record_skill_provenance(
        self,
        *,
        role: RoleConfig,
        task_id: str | None = None,
    ) -> list:
        if not role.skills:
            return []
        try:
            from zf.core.skills import (
                build_skill_lock_entries,
                materialize_role_skills,
                upsert_skills_lockfile,
            )

            materialized = materialize_role_skills(
                config=self.config,
                project_root=self.project_root,
                state_dir=self.state_dir,
                role=role,
                task_id=task_id,
            )
            materialized_paths = (
                materialized.materialized_paths_under(self.project_root)
                if materialized is not None else {}
            )
            entries = build_skill_lock_entries(
                project_root=self.project_root,
                state_dir=self.state_dir,
                role=role,
                config=self.config,
                task_id=task_id,
                run_id=self._current_run_id(),
                materialized_paths=materialized_paths,
            )
            upsert_skills_lockfile(state_dir=self.state_dir, entries=entries)
            if materialized is not None:
                self.event_writer.append(ZfEvent(
                    type="skills.materialized",
                    actor="zf-cli",
                    task_id=task_id,
                    payload=materialized.to_payload(),
                ))
            return entries
        except Exception:
            return []

    def _send_transport_task(
        self,
        role_name: str,
        briefing_path: Path,
        prompt: str,
        context: DispatchContext | None,
    ) -> None:
        if not self._transport_dispatch_enabled():
            reason = "transport dispatch unavailable for mutating dispatch"
            try:
                self.event_writer.append(ZfEvent(
                    type="orchestrator.dispatch_failed",
                    actor="zf-cli",
                    task_id=getattr(context, "task_id", None),
                    payload={
                        "role": role_name,
                        "instance_id": role_name,
                        "briefing_path": str(briefing_path),
                        "reason": reason,
                        "transport": type(self.transport).__name__,
                        "diagnostic_mode": True,
                    },
                    correlation_id=getattr(context, "trace_id", None),
                ))
            except Exception:
                pass
            raise RuntimeError(reason)
        role = (
            self._find_role_by_instance(role_name)
            or self._find_role_by_name(role_name)
        )
        # P0-1 (2026-07-09): budget gate at the charging primitive. Every paid
        # dispatch funnels through here — the main _dispatch_ready loop AND the
        # fanout child / synth / writer-rework / reader-rebind paths. Gating only
        # _dispatch_ready let in-flight fanout burn past the cap (RB1) because
        # those paths call this primitive directly. _budget_exceeded emits
        # cost.budget.exceeded (per-scope cooldown); raising a RuntimeError
        # subclass makes callers skip their post-send *.dispatched bookkeeping the
        # same way they already handle the transport-unavailable raise below.
        # ZF-E2E-MINI-P2 correction (2026-07-11): the old claim "role is None
        # for the orchestrator agent's own dispatch" only holds when no
        # orchestrator role is configured — and then _notify_orchestrator_agent
        # no-ops, so there is nothing to exempt. With an agent orchestrator,
        # being wakeable and being budget-gated resolve the same role: a frozen
        # budget DOES block Layer-2 self-wakes (observed live). The freeze-
        # silence gate in _notify_orchestrator_agent keeps that from becoming
        # a briefing/dispatch_failed churn loop.
        if role is not None and self._budget_exceeded(role):
            # ZF-E2E-RACING-P1: roll back in-flight bookkeeping before
            # raising — callers without their own rollback (rework path)
            # otherwise wedge the task permanently.
            self._rollback_inflight_dispatch(context)
            raise BudgetExceededError(
                f"dispatch to {role_name} blocked: budget exceeded"
            )
        # DID-7 (2026-06-19 e2e): wait for the worker's agent prompt to be READY
        # before sending the briefing. transport.send_task only checks the pane's
        # process is *alive*, not that claude is at its input prompt — under
        # multi-pane / worktree cold-boot contention the send-keys raced ahead of
        # the prompt and the briefing was lost, leaving the worker idle (0 tokens)
        # → worker.drift.detected "not active" → respawn loop (e2e dev-core /
        # prd-author, systemic across flows). _wait_role_ready returns fast when
        # the prompt is already up, so already-ready workers pay nothing.
        if role is not None and getattr(self, "_wait_role_ready", None) is not None:
            try:
                self._wait_role_ready(role)
            except Exception:
                pass
        try:
            try:
                self.transport.send_task(
                    role_name,
                    briefing_path,
                    prompt,
                    context=context,
                )
            except TypeError as exc:
                # Compatibility for older test/custom TransportAdapter
                # implementations that predate DispatchContext.
                if "context" not in str(exc):
                    raise
                self.transport.send_task(role_name, briefing_path, prompt)
        except Exception:
            # ZF-E2E-RACING-P1: send failed — the dispatch never reached the
            # worker, so the in-flight claim must not survive.
            self._rollback_inflight_dispatch(context)
            raise
        try:
            if role is not None:
                self._get_spawn_coordinator().notify_first_dispatch(role)
        except Exception:
            pass

    def _transport_dispatch_enabled(self) -> bool:
        transport = getattr(self, "transport", None)
        if transport is None:
            return False
        for attr in ("dispatch_enabled", "supports_dispatch"):
            value = getattr(transport, attr, None)
            if value is False:
                return False
        name = type(transport).__name__.lower()
        if "nooptransport" in name:
            return bool(getattr(transport, "allow_dispatch", False))
        return True

    def _load_offset(self) -> int:
        try:
            return self.session_store.load().latest_event_offset
        except Exception:
            return 0

    def _persist_offset(self, offset: int) -> None:
        try:
            self.session_store.update(latest_event_offset=offset)
        except Exception:
            pass

    def _react_to_events(
        self, pushed: list[ZfEvent] | None = None
    ) -> list[OrchestratorDecision]:
        """React to events.

        Two modes (chosen by config):

        - **Layer 2 active** (config has an `orchestrator` role): every event
          is delivered to Layer 2 via `_notify_orchestrator_agent`. The Python
          deterministic handlers are NOT fired. Layer 2 makes all routing
          decisions and writes state changes back via `zf kanban update`.

        - **Layer 1 only** (no orchestrator role): the legacy deterministic
          `_on_*` handlers fire. This preserves backward compatibility with
          configs that don't yet use the three-layer architecture.
        """
        decisions: list[OrchestratorDecision] = []
        if pushed is not None:
            recent = pushed
            new_offset = self.event_log.current_offset()
        else:
            offset = self._load_offset()
            recent, new_offset = self.event_log.read_from_offset(offset)

        layer2_active = self._find_role_by_name("orchestrator") is not None
        reacted_tasks: set[str] = set()
        # doc 66 §14.0: enter Layer 2 batch — per-event _notify_orchestrator_agent
        # accumulates into _layer2_pending; the batch fires ONE multi-trigger turn
        # at its end, avoiding N back-to-back briefings under blocking send_task.
        # Set fresh each call (self-corrects a stale flag from a prior crash).
        self._layer2_in_batch = layer2_active
        for event in recent:
            if event.id in self._processed_event_ids:
                continue

            if (hydrated := hydrate_runtime_call_result_event(self, event)) is None:
                self._processed_event_ids.add(event.id)
                continue
            event = hydrated

            rejected = self._reject_invalid_lifecycle_event(event)
            if rejected:
                decisions.append(rejected)
                self._processed_event_ids.add(event.id)
                continue

            # avbs-r4 F8: reader child failures arrive agent-emitted without
            # task_id; resolve from the fanout manifest so rework triage and
            # same_lane backedges can fire (in-memory only, log untouched).
            if not event.task_id:
                resolved_task_id = resolve_reader_child_task_id(
                    event, manifest_loader=self._fanout_manifest,
                )
                if resolved_task_id:
                    event.task_id = resolved_task_id

            # Layer 1 housekeeping: mechanical state writes from emitted events
            self._apply_housekeeping(event)

            # ZF-LH-INLINE-001 (doc 26 §3.3): scan user.message for
            # operator inline-override keywords. Emits audit event when
            # matched. Pure scan + side-effecting audit emit; downstream
            # dispatch enforcement (skip_stages) is left to a follow-up
            # sprint that wires the audit into TaskContract.
            self._scan_inline_overrides(event)

            # ZF-PWF-PRECOMPACT-001 (doc 41 §4.4): observe Claude Code
            # PreCompact hook events. When a worker is about to compact
            # its context, emit a snapshot_requested so SP-001 projector
            # + recovery briefing can rebuild before chat history is
            # lost. Hook never blocks compaction.
            self._handle_precompact_signal(event)

            # Candidate/PDD-level judge.passed (PRD/issue/refactor fanout)
            # carries no task_id, so both the Layer 2 terminal-ownership check
            # and the Layer 1 primary-handler path — each gated on task_id —
            # skip it, leaving its canonical task cards stuck in_progress despite
            # judge.passed (ledger PRD e2e 2026-06-20: delivery reached
            # judge.passed but the kanban projection never moved cards to done).
            # Task/card closure is a mechanical Layer 1 responsibility, so settle
            # the candidate's tasks here deterministically in both modes. No-op
            # unless the event is candidate-level (no task_id + pdd_id +
            # feature_id), so per-task judge.passed is unaffected.
            if (
                not event.task_id
                and event.type == "judge.passed"
                and not (
                    isinstance(event.payload, dict)
                    and str(event.payload.get("authority") or "")
                    == "compat_projection"
                )
            ):
                evidence_gap = self._reject_flow_judge_evidence_gap(event)
                if evidence_gap is not None:
                    decisions.append(evidence_gap)
                    self._processed_event_ids.add(event.id)
                    continue
                settle = self._settle_candidate_tasks_done(event)
                if settle is not None:
                    decisions.append(settle)

            if not event.task_id and event.type == "run.goal.completed":
                settle = self._settle_candidate_tasks_done(event)
                if settle is not None:
                    decisions.append(settle)

            if not event.task_id and event.type in {
                "verify.passed",
                "test.passed",
                "candidate.ready",
            }:
                decision = self._bridge_verify_passed_to_parity_scan(event)
                if decision:
                    decisions.append(decision)
                decision = self._bridge_verify_passed_to_flow_discovery(event)
                if decision:
                    decisions.append(decision)

            if (
                not event.task_id
                and is_module_parity_scan_completed_event(event.type)
            ):
                decision = self._bridge_module_parity_scan_completed(event)
                if decision:
                    decisions.append(decision)
                self._processed_event_ids.add(event.id)
                continue

            if not event.task_id and event.type == "flow.discovery.completed":
                decision = self._bridge_flow_discovery_completed(event)
                if decision:
                    decisions.append(decision)
                self._processed_event_ids.add(event.id)
                continue

            if (
                not event.task_id
                and event.type == "flow.discovery.failed"
                and self._parity_gap_tasks(
                    event.payload if isinstance(event.payload, dict) else {}
                )
            ):
                decision = self._bridge_flow_discovery_completed(event)
                if decision:
                    decisions.append(decision)
                self._processed_event_ids.add(event.id)
                continue

            if not event.task_id and event.type in {
                "gap_plan.ready",
                "goal.gap_plan.ready",
                "flow.gap_plan.ready",
            }:
                decision = self._bridge_gap_plan_ready_to_task_map(event)
                if decision:
                    decisions.append(decision)
                self._processed_event_ids.add(event.id)
                continue

            if layer2_active:
                if self._layer1_owns_rework_triage_event(event):
                    task = self.task_store.get(event.task_id)
                    if task is not None:
                        decision = self._route_rework_trigger(
                            task,
                            event,
                            reason=f"{event.type} triage → Layer 1",
                        )
                        if decision:
                            decisions.append(decision)
                    self._processed_event_ids.add(event.id)
                    continue
                if self._layer1_owns_terminal_event(event):
                    entries = self.event_registry.resolve(event.type)
                    terminal_decision = None
                    if entries:
                        terminal_decision = entries[0].handler(event)
                        if terminal_decision:
                            decisions.append(terminal_decision)
                        for entry in entries[1:]:
                            try:
                                entry.handler(event)
                            except Exception:
                                pass
                    if (
                        terminal_decision is not None
                        and terminal_decision.action == "block"
                    ):
                        orch_role = self._find_role_by_name("orchestrator")
                        will_dispatch = (
                            orch_role is not None
                            and (
                                not orch_role.triggers
                                or event.type in orch_role.triggers
                            )
                        )
                        if will_dispatch:
                            self._notify_orchestrator_agent(event)
                            decisions.append(OrchestratorDecision(
                                action="notify",
                                task_id=event.task_id,
                                role="orchestrator",
                                reason=(
                                    f"{event.type} terminal evidence blocked "
                                    "→ Layer 2"
                                ),
                            ))
                    self._processed_event_ids.add(event.id)
                    continue
                if _kernel_owns_liveness_event(self.config, event.type):
                    entries = self.event_registry.resolve(event.type)
                    if entries:
                        decision = entries[0].handler(event)
                        if decision:
                            decisions.append(decision)
                        for entry in entries[1:]:
                            try:
                                entry.handler(event)
                            except Exception:
                                pass
                    self._processed_event_ids.add(event.id)
                    continue
                # Layer 2 owns all decisions. Layer 1 routes the event and
                # forgets it. Layer 2 will call back via `zf kanban update`
                # if state changes are needed.
                # Note: _notify_orchestrator_agent applies the orchestrator
                # role's triggers filter — events not in triggers are no-op.
                orch_role = self._find_role_by_name("orchestrator")
                will_dispatch = (
                    orch_role is not None
                    and (not orch_role.triggers or event.type in orch_role.triggers)
                )
                if will_dispatch:
                    self._notify_orchestrator_agent(event)
                    decisions.append(OrchestratorDecision(
                        action="notify",
                        task_id=event.task_id,
                        role="orchestrator",
                        reason=f"{event.type} → Layer 2",
                    ))
                self._processed_event_ids.add(event.id)
                continue

            # Layer 1 fallback: deterministic handlers via registry.
            # P0-2: resolve() returns all registered handlers (primary
            # built-in first, YAML actions after). Only the first
            # handler's return value feeds the decision stream —
            # side-effect handlers run for their effects only.
            if not event.task_id or event.task_id in reacted_tasks:
                # Still run side-effect handlers (log/emit) — they may
                # not need a task_id. But skip built-in primary to
                # match pre-P0-2 behavior that gated on task_id.
                #
                # Exception: kernel-owned entry/liveness events legitimately
                # carry no task_id (e.g. light-topology `prd.requested` → the
                # task_map synthesizer). The `_KERNEL_LIVENESS_EVENTS`
                # fast-path above only fires in Layer-2-active configs, so in
                # Layer-1-only mode their builtin primary must fire here or it
                # never runs (light baseline 2026-07-06: prd.requested woke
                # run_once but the synthesizer handler was skipped as
                # task_id-less).
                # FIX-6(bizsim r4 F2): 内核活性事件(如 workflow.reconcile
                # .requested)天然无 task_id, primary 照跑。保留枚举式
                # fallback，避免先跑 primary 后又在 side-effect loop 中重复。
                entries = self.event_registry.resolve(event.type)
                fire_builtin = (
                    not event.task_id
                    and _kernel_owns_liveness_event(self.config, event.type)
                )
                for idx, entry in enumerate(entries):
                    if entry.source == "yaml":
                        try:
                            entry.handler(event)
                        except Exception:
                            pass
                    elif fire_builtin and idx == 0:
                        decision = entry.handler(event)
                        if decision:
                            decisions.append(decision)
                        self._processed_event_ids.add(event.id)
                continue
            entries = self.event_registry.resolve(event.type)
            if entries:
                # Primary handler first — drives OrchestratorDecision
                primary = entries[0]
                decision = primary.handler(event)
                if decision:
                    decisions.append(decision)
                    reacted_tasks.add(event.task_id)
                    self._processed_event_ids.add(event.id)
                # Remaining handlers run as side effects
                for entry in entries[1:]:
                    try:
                        entry.handler(event)
                    except Exception:
                        pass

        # End of batch: fire one coalesced Layer 2 turn for everything the loop
        # accumulated (doc 66 §14.0). Cross-batch timer gating is re-applied by
        # _notify_orchestrator_agent.
        if layer2_active:
            self._flush_layer2_batch()
        if new_offset:
            self._persist_offset(new_offset)
        return decisions

    def _layer1_owns_terminal_event(self, event: ZfEvent) -> bool:
        """Return true when a progress event is the terminal claim.

        With Layer 2 enabled, routing decisions stay agent-owned, but terminal
        verification and task closure are mechanical Layer 1 responsibilities.
        A progress event is terminal only when no non-orchestrator role
        subscribes to it.
        """
        if not event.task_id or event.type not in _KERNEL_TERMINAL_EVENTS:
            return False
        try:
            return not self._non_orchestrator_subscribers(event.type)
        except Exception:
            return event.type == "judge.passed"

    def _layer1_owns_rework_triage_event(self, event: ZfEvent) -> bool:
        """Keep task rework in L1 until Run Manager requests L2 advice."""
        return bool(event.task_id and event.type in REWORK_TRIAGE_TRIGGER_EVENTS)

    def _migrate_legacy_assigned_to(self) -> None:
        """G-INST-9: rewrite legacy ``assigned_to=<role.name>`` entries to
        the first matching instance_id. Idempotent + no-op for configs
        where instance_id == name (single-instance deployments)."""
        # Build a map: role.name → first instance_id. Only keep entries
        # where they differ (i.e. multi-instance expansions).
        first_instance: dict[str, str] = {}
        valid_instance_ids: set[str] = set()
        for role in self.config.roles:
            valid_instance_ids.add(role.instance_id)
            if role.instance_id != role.name and role.name not in first_instance:
                first_instance[role.name] = role.instance_id
        if not first_instance:
            return  # nothing to migrate
        try:
            tasks = self.task_store.list_all()
        except Exception:
            return
        for task in tasks:
            if not task.assigned_to:
                continue
            # Already a valid instance_id → skip
            if task.assigned_to in valid_instance_ids:
                continue
            # Legacy bare role.name that now has expanded instances.
            # B-MULTIREPLICA-01 (2026-04-23): only migrate tasks that
            # are already in_progress — those are actively tied to a
            # specific worker. Backlog / queued tasks keep the role.name
            # so ``_find_role_by_instance``'s fallback can pick a
            # WIP-available replica at dispatch time; otherwise every
            # queued task gets stamped with the same first replica and
            # work never spreads across the pool.
            if task.status != "in_progress":
                continue
            if task.assigned_to in first_instance:
                try:
                    self.task_store.update(
                        task.id,
                        assigned_to=first_instance[task.assigned_to],
                    )
                except Exception:
                    continue

    def _validate_completion_payload_contract(self, event: ZfEvent) -> None:
        """EVAL-PAYLOAD-CONTRACT-001 (doc 43 §2.4): if event is a
        completion event (dev.build.done / review.approved /
        test.passed / judge.passed / arch.proposal.done /
        design.critique.done), check its payload against the 6-field
        contract. Missing required fields → emit task.contract.invalid
        audit event (non-blocking).
        """
        from zf.core.events.payload_schemas import (
            SUCCESS_EVENT_TYPES,
            build_invalid_event_payload,
            validate_completion_payload,
            warn_completion_payload,
        )
        if event.type not in SUCCESS_EVENT_TYPES:
            return
        fanout_payload = getattr(self, "_fanout_result_payload", None)
        payload = fanout_payload(event) if callable(fanout_payload) else event.payload
        if payload.get("fanout_id") and (
            payload.get("child_id")
            or event.type == "fanout.synth.completed"
            or event.actor == "zf-cli"
        ):
            return
        missing = validate_completion_payload(event)
        warnings = warn_completion_payload(event)
        if not missing and not warnings:
            return
        # Only emit audit when there are missing required fields.
        # WARN-only is surfaced by handoff-score / kanban-health
        # readers downstream, not via per-event audit (would be too noisy).
        if not missing:
            return
        try:
            payload = build_invalid_event_payload(
                event, missing, warnings=warnings,
            )
            self.event_writer.append(ZfEvent(
                type="task.contract.invalid",
                actor="zf-cli",
                task_id=event.task_id or "",
                payload=payload,
            ))
        except Exception:
            pass

    def _handle_precompact_signal(self, event: ZfEvent) -> None:
        """ZF-PWF-PRECOMPACT-001 (doc 41 §4.4): when a worker raises
        ``worker.context.precompact`` (Claude Code PreCompact hook),
        emit a kernel-internal ``worker.context.snapshot_requested``
        targeting the worker's active task.

        Downstream consumers:
        - SP-001 StatePacketProjector regenerates state-packet.json
        - PWF-MEM-001 projection regenerator regenerates the 4 files
        - CONTEXT-REC-001 recovery briefing picks up the fresh snapshot

        Discipline:
        - Hook always exits 0 — never block compaction.
        - If the worker has no active task, no snapshot is requested
          (no-op).
        - Idempotent: emitting twice for the same precompact event is
          tolerable (downstream regenerators are atomic).
        - Defensive try/except: precompact is a hint, not a critical
          path.
        """
        try:
            if event.type != "worker.context.precompact":
                return
            instance_id = event.actor or ""
            if not instance_id:
                return
            task = self._active_task_for_instance(instance_id)
            if task is None:
                return
            payload = event.payload if isinstance(event.payload, dict) else {}
            self.event_writer.append(ZfEvent(
                type="worker.context.snapshot_requested",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "instance_id": instance_id,
                    "trigger": "precompact",
                    "source_event_id": event.id,
                    "session_id": str(payload.get("session_id", "")),
                    "transcript_path": str(payload.get("transcript_path", "")),
                },
            ))
        except Exception:
            pass

    def _scan_inline_overrides(self, event: ZfEvent) -> None:
        """ZF-LH-INLINE-001 (doc 26 §3.3): if the event is a
        ``user.message`` from a human actor and matches any configured
        inline-override pattern, emit a ``workflow.inline_override``
        audit event so the operator action is recorded in the
        append-only ledger.

        Discipline:
        - Pure pre-check happens in
          ``zf.runtime.inline_overrides.scan_inline_overrides``;
          agent-emitted events that quote a keyword are rejected
          there before this method ever emits.
        - Audit is always informational (``human_initiated: true``).
        - Errors swallowed defensively — inline overrides are a
          quality-of-life feature, not a critical path.
        """
        try:
            from zf.runtime.inline_overrides import (
                _HUMAN_ACTORS,
                _extract_message_text,
                build_audit_payload,
                scan_inline_overrides,
            )

            matches = scan_inline_overrides(event, self.config)
            if matches:
                overrides = self.config.workflow.inline_overrides
                self.event_writer.append(ZfEvent(
                    type=overrides.audit_event or "workflow.inline_override",
                    actor="zf-cli",
                    payload=build_audit_payload(event, matches),
                ))
                return

            # Observability: a human-actor user.message with text but no
            # pattern match landed and produced no downstream events.
            # Emit user.message.unrouted so the orphan signal is visible
            # in events.jsonl instead of a silent no-op (bypass-loop
            # meta-bug 2026-05-31).
            if event.type != "user.message":
                return
            if (event.actor or "").lower() not in _HUMAN_ACTORS:
                return
            text = _extract_message_text(event)
            if not text:
                return
            overrides = getattr(
                getattr(self.config, "workflow", None),
                "inline_overrides",
                None,
            )
            scanned = sorted((overrides.patterns or {}).keys()) if overrides else []
            self.event_writer.append(ZfEvent(
                type="user.message.unrouted",
                actor="zf-cli",
                payload={
                    "message_id": event.id,
                    "reason": "no_inline_override_match",
                    "scanned_patterns": scanned,
                    "actor_hint": event.actor or "",
                    "text_excerpt": text[:160],
                },
            ))
        except Exception:
            # Defensive: never break event processing because of an
            # inline-override scan failure.
            pass

    def _apply_artifact_manifest_published(self, event: ZfEvent) -> None:
        from zf.runtime.task_refs import TaskRefManager

        result = TaskRefManager(
            state_dir=self.state_dir,
            project_root=self.project_root,
            config=self.config,
        ).process_artifact_manifest_published(event)
        if result is None:
            return
        task_id = str(result.payload.get("task_id") or event.task_id or "")
        event_type = (
            "task.artifact_refs.updated"
            if result.status == "updated"
            else "artifact.manifest.rejected"
        )
        emitted = self.event_writer.append(ZfEvent(
            type=event_type,
            actor="zf-cli",
            task_id=task_id or None,
            payload=result.payload,
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        if result.status != "updated":
            return
        contract_refs = result.payload.get("contract_refs")
        if not isinstance(contract_refs, dict) or not contract_refs:
            return
        contract = dict(contract_refs)
        artifact_paths = [
            str(ref.get("path") or "")
            for ref in result.payload.get("artifact_refs", [])
            if isinstance(ref, dict) and str(ref.get("path") or "").strip()
        ]
        if artifact_paths:
            contract["handoff_artifacts"] = artifact_paths
        update_event = self.event_writer.append(ZfEvent(
            type="task.contract.update",
            actor="zf-cli",
            task_id=task_id or None,
            payload={
                "source": "task.artifact_refs.updated",
                "artifact_refs_event_id": emitted.id,
                "contract": contract,
            },
            causation_id=emitted.id,
            correlation_id=event.correlation_id,
        ))
        apply_task_contract_event(self.task_store, update_event)

    def _fanout_liveness_events_for_agent_usage(self) -> list[ZfEvent]:
        event_types = {
            "fanout.child.dispatched",
            "fanout.child.completed",
            "fanout.child.failed",
            "fanout.child.dispatch_lost",
            "fanout.cancelled",
            "fanout.timed_out",
        }
        cache = getattr(self, "_agent_usage_fanout_liveness_events", None)
        if isinstance(cache, list):
            return cache
        try:
            events = []
            path = getattr(self.event_log, "path", None)
            if path is None:
                raise OSError("event log path unavailable")
            markers = tuple(f'"type":"{event_type}"' for event_type in event_types)
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not any(marker in line for marker in markers):
                        continue
                    try:
                        event = ZfEvent.from_json(line)
                    except Exception:
                        continue
                    events.append(event)
        except Exception:
            try:
                events = [
                    event
                    for event in self.event_log.read_days(1)
                    if event.type in event_types
                ]
            except Exception:
                events = []
        self._agent_usage_fanout_liveness_events = events
        return events

    def _apply_housekeeping(self, event: ZfEvent) -> None:
        """Layer 1 mechanical state writes — never decisions."""
        if event.type in {
            "fanout.child.dispatched",
            "fanout.child.completed",
            "fanout.child.failed",
            "fanout.child.dispatch_lost",
            "fanout.cancelled",
            "fanout.timed_out",
        }:
            cache = getattr(self, "_agent_usage_fanout_liveness_events", None)
            if isinstance(cache, list) and all(
                getattr(cached, "id", "") != event.id for cached in cache
            ):
                cache.append(event)
        # EVAL-PAYLOAD-CONTRACT-001 (doc 43 §2.4): when worker emits a
        # completion event, validate payload against 6-field contract.
        # Missing required → emit task.contract.invalid audit (non-blocking).
        try:
            self._validate_completion_payload_contract(event)
        except Exception:
            pass
        # Auto-promote system events (candidate.conflict / dev.blocked) into
        # memory.note so cross-round learnings make it into .zf/memory even
        # when workers don't emit memory.note themselves. The promoted note
        # is appended to event_writer and housekept inline so it lands in
        # MemoryStore in the same tick.
        if event.type != "memory.note" and event.id not in self._promoted_causations:
            promoted = promote_to_memory_note_event(event)
            if promoted is not None:
                self._promoted_causations.add(event.id)
                try:
                    self.event_writer.append(promoted)
                except Exception:
                    pass
                try:
                    apply_memory_note_event(self.memory_store, promoted)
                except Exception:
                    pass
        try:
            if event.type == "task_map.ready":
                self._pin_goal_claim_set(event)
            if event.type == "agent.usage":
                apply_agent_usage_event(
                    self.cost_tracker, event,
                    role_backends=self._role_backends(),
                )
                try:
                    from zf.core.state.role_sessions import RoleSessionRegistry
                    from zf.runtime.usage_liveness import (
                        apply_agent_usage_liveness,
                    )

                    registry = RoleSessionRegistry(
                        self.state_dir / "role_sessions.yaml",
                        project_root=str(self.project_root),
                    )
                    apply_agent_usage_liveness(
                        registry,
                        event,
                        tasks=self.task_store.list_all(),
                        events=self._fanout_liveness_events_for_agent_usage(),
                    )
                except Exception:
                    # B-STUCK-2: the rich path (task/event scan) failing must
                    # NOT strand liveness — a worker emitting agent.usage is
                    # demonstrably alive. Guaranteed minimal touch as fallback
                    # so it is never false-stuck mid coding-turn (livelock root).
                    try:
                        from zf.core.state.role_sessions import (
                            RoleSessionRegistry,
                        )
                        inst = str(event.actor or "").strip()
                        if inst:
                            RoleSessionRegistry(
                                self.state_dir / "role_sessions.yaml",
                                project_root=str(self.project_root),
                            ).record_heartbeat(inst, {
                                "instance_id": inst,
                                "last_action_ts": event.ts,
                                "source": "agent.usage",
                                "event_id": event.id,
                            })
                    except Exception:
                        pass
            elif event.type == "memory.note":
                apply_memory_note_event(self.memory_store, event)
            elif event.type == "task.contract.update":
                apply_task_contract_event(self.task_store, event)
            elif event.type in {"task.dispatched", "fanout.child.dispatched"}:
                # Seed a fresh busy heartbeat from kernel dispatch truth.
                # Without this, a rework/fanout dispatch can inherit an old
                # worker heartbeat and the next sweep falsely reports
                # worker.stuck before the agent has a chance to emit its own
                # heartbeat.
                from zf.core.state.role_sessions import RoleSessionRegistry
                try:
                    registry = RoleSessionRegistry(
                        self.state_dir / "role_sessions.yaml",
                        project_root=str(self.project_root),
                    )
                    apply_task_dispatched_heartbeat_seed(registry, event)
                except Exception:
                    pass
            elif event.type == "artifact.manifest.published":
                self._apply_artifact_manifest_published(event)
            elif event.type in (
                "review.approved", "verify.passed", "test.passed", "judge.passed",
            ):
                # EVAL-ACCEPTANCE-CRITERIA-001 (doc 43 §2.5): merge
                # any acceptance_evidence_update payload field into
                # task.contract.acceptance_evidence.
                try:
                    from zf.runtime.housekeeping import (
                        apply_acceptance_evidence_event,
                    )
                    apply_acceptance_evidence_event(self.task_store, event)
                except Exception:
                    pass
            elif event.type == "worker.heartbeat":
                # α-2 (2026-05-17): persist worker liveness to
                # role_sessions.yaml so α-3 sweep can sense idle / silent
                # / stuck states from real signals (not 4-min timeouts).
                from zf.core.state.role_sessions import RoleSessionRegistry
                try:
                    registry = RoleSessionRegistry(
                        self.state_dir / "role_sessions.yaml",
                        project_root=str(self.project_root),
                    )
                    apply_worker_heartbeat_event(registry, event)
                except Exception:
                    pass
                # ω-1.c (2026-05-18): worker recovered → clear sweep dedup
                # so the next stuck/silent condition emits immediately
                # without waiting for the 5min cooldown to elapse.
                instance_id = (event.actor or "").strip()
                if instance_id:
                    for sig in ("worker.stuck", "worker.probe.silent"):
                        self._sweep_signal_last_emit_at.pop(
                            (instance_id, sig), None,
                        )
            elif event.type == "worker.state.changed":
                # Mirror kernel worker-state truth into role_sessions. A
                # completed gate may move a role busy -> idle without another
                # heartbeat; the sweep must see that idle state rather than the
                # previous busy heartbeat.
                from zf.core.state.role_sessions import RoleSessionRegistry
                try:
                    registry = RoleSessionRegistry(
                        self.state_dir / "role_sessions.yaml",
                        project_root=str(self.project_root),
                    )
                    apply_worker_state_changed_event(registry, event)
                except Exception:
                    pass
            elif event.type.startswith("codex.hook.") or event.type.startswith("claude.hook."):
                # Universal activity liveness (2026-07-09): per-tool-call hooks
                # prove a worker is alive during long turns where agent.usage
                # is sparse (agent.usage keeps its own richer handler above).
                # Throttled inside the helper; backend- and workflow-agnostic.
                from zf.core.state.role_sessions import RoleSessionRegistry
                try:
                    registry = RoleSessionRegistry(
                        self.state_dir / "role_sessions.yaml",
                        project_root=str(self.project_root),
                    )
                    apply_worker_activity_heartbeat(registry, event)
                except Exception:
                    pass
            elif event.type == "repair.action.requested":
                self._apply_repair_action_request(event)
            elif event.type in {
                "repair.action.applied",
                "repair.action.rejected",
                "autoresearch.loop.completed",
                "autoresearch.loop.failed",
                "replan.contract_eval.completed",
                "worker.stuck.recovered",
                "static_gate.passed",
                "verify.passed",
                "test.passed",
                "judge.passed",
            }:
                try:
                    from zf.runtime.loop_closure import append_loop_closure_events

                    append_loop_closure_events(
                        events=self.event_log.read_all(),
                        source_event=event,
                        writer=self.event_writer,
                        state_dir=self.state_dir,
                        project_id=getattr(self.config.project, "name", "") or "",
                    )
                except Exception:
                    pass
            elif event.type == "arch.proposal.done":
                # P0/K1 (docs/impl/22): kernel does NOT auto-project arch's
                # proposal into the kanban contract or decompose tasks. Per
                # workflow.dag.design_to_backlog_owner=orchestrator, the
                # stage ④ "backlog" synthesis is an orchestrator decision
                # that fires on design.critique.done verdict=approve. The
                # previous auto-apply (faa62a9 + fe13d99) bypassed critic
                # review and polluted kanban with a pre-approval contract,
                # which in turn confused rework_triage. Both calls removed.
                # task.ref git-side handling stays — it's mechanical git
                # state, not contract decision.
                from zf.runtime.task_refs import TaskRefManager

                result = TaskRefManager(
                    state_dir=self.state_dir,
                    project_root=self.project_root,
                    config=self.config,
                ).process_arch_proposal_done(event)
                if result is not None and result.status in {"updated", "rejected"}:
                    event_type = (
                        "task.ref.updated"
                        if result.status == "updated"
                        else "task.ref.rejected"
                    )
                    self.event_writer.append(ZfEvent(
                        type=event_type,
                        actor="zf-cli",
                        task_id=event.task_id,
                        payload=result.payload,
                        causation_id=event.id,
                        correlation_id=event.correlation_id,
                    ))
                    # r-next backlog B-4: ref.rejected on the arch path also
                    # contributes to the dispatch-cooldown counter so a worker
                    # that keeps emitting unverifiable refs eventually parks.
                    if result.status == "rejected" and event.task_id:
                        try:
                            self._record_dispatch_failure(event.task_id)
                        except Exception:
                            pass
            elif event.type == "dev.build.done":
                if self._writer_completion_identity_already_terminal(event):
                    return
                from zf.runtime.task_refs import TaskRefManager

                result = TaskRefManager(
                    state_dir=self.state_dir,
                    project_root=self.project_root,
                    config=self.config,
                ).process_dev_build_done(event)
                if result is not None and result.status in {"updated", "rejected"}:
                    event_type = (
                        "task.ref.updated"
                        if result.status == "updated"
                        else "task.ref.rejected"
                    )
                    self.event_writer.append(ZfEvent(
                        type=event_type,
                        actor="zf-cli",
                        task_id=event.task_id,
                        payload=result.payload,
                        causation_id=event.id,
                        correlation_id=event.correlation_id,
                    ))
                    # r-next backlog B-4 (cangjie dev-4 死循环): a dev that
                    # keeps emitting dev.build.done with a degenerate payload
                    # (e.g. only dispatch_id, no artifact/evidence_refs)
                    # would otherwise infinite-loop through
                    # stuck_recovery → respawn → re-dispatch → reject. Hook
                    # into the same dispatch_failure counter so the cooldown
                    # gate (_dispatch_recent_failure_cooldown_active) parks
                    # the task after N rejected in the window.
                    if result.status == "rejected" and event.task_id:
                        try:
                            self._record_dispatch_failure(event.task_id)
                        except Exception:
                            pass
                # P3/K5 (docs/impl/22): static_gate as independent DAG
                # stage between ⑤ implement and ⑦ code_review. Runs the
                # configured quality_gates.static.required_checks on the
                # dev's working tree and emits static_gate.passed |
                # static_gate.failed | static_gate.skipped. Failure routes
                # back to dev via workflow.rework_routing.static_gate.failed
                # (configurable per project). Opt-in via workflow.dag.enabled
                # so projects without the new DAG keep their existing
                # post-judge quality_gates behavior in candidates.py.
                try:
                    self._maybe_run_static_gate(event)
                except Exception:
                    pass  # static_gate never blocks the dispatch loop
            elif event.type == "candidate.integration.completed":
                # Auto-ship the validated candidate into ship_target_branch so
                # operators stop running `git merge candidate/<id>` by hand
                # (cangjie r-next B-3; pre-judge, quality_status-gated). The
                # judge.passed path is handled by a standalone `if` below —
                # judge.passed matches the earlier acceptance-evidence elif so it
                # can never reach this elif branch.
                self._maybe_auto_ship(event)
            elif event.type in REWORK_TRIAGE_TRIGGER_EVENTS:
                triage = self._ensure_rework_triage(event)
                task_for_rework = (
                    self.task_store.get(event.task_id) if event.task_id else None
                )
                valid_rework_state = (
                    task_for_rework is None
                    or self._rework_trigger_valid_for_task_state(
                        task_for_rework,
                        event,
                    )
                )
                if triage.should_increment_retry and valid_rework_state:
                    # LH-0.T1: product/design rework counter bump (runs in
                    # both Layer 1 legacy and Layer 2 modes; cap enforced
                    # separately in _dispatch_rework / _dispatch_ready).
                    # avbs-r4 F12: 带事件窗做同 fanout 去重,echo 重放不计账。
                    try:
                        from zf.runtime.event_window import read_runtime_events

                        window = read_runtime_events(self.event_log, self.state_dir)
                    except Exception:
                        window = None
                    apply_rework_failure_event(
                        self.task_store, event, events=window,
                    )
                    # LH-4.T3: circuit breaker failure counter. Evidence,
                    # harness, and environment classifications do not count as
                    # product failures.
                    # P1-7 (2026-07-09): key the breaker on the role that will
                    # actually be re-dispatched (the producer), not the gate that
                    # emitted the failure — otherwise the dispatch-time check
                    # (which keys on the dispatched role) never sees these
                    # failures and the breaker never trips.
                    rework_role = None
                    try:
                        if task_for_rework is not None:
                            rework_role = self._resolve_rework_role(
                                task_for_rework, event,
                            )
                    except Exception:
                        rework_role = None
                    apply_circuit_breaker_failure(
                        event, self.state_dir / "circuits.json",
                        role_name=(rework_role.name if rework_role else None),
                    )
            # LB-1: judge.passed matches the earlier acceptance-evidence elif
            # (review.approved/verify.passed/test.passed/judge.passed) so it never
            # reaches the auto-ship elif in this chain. Run terminal ship + run-goal
            # completion here as a standalone `if` so both fire regardless of the
            # elif shadow (the reason auto_ship_on_judge_passed had no effect).
            if event.type == "goal.closure.synthesized":
                from zf.runtime.goal_closure_runtime import (
                    process_goal_closure_result,
                )

                process_goal_closure_result(self, event)
            elif event.type in {
                "run.goal.completion.claimed",
                "workflow.operation.settled",
                "workflow.operation.failed",
                "workflow.operation.blocked",
                "rework.feedback.verified_closed",
                "rework.feedback.residual",
                "attempt.handoff.acknowledged",
                "attempt.handoff.closed",
                "human.decision.resolved",
                "run.manager.human_decision.applied",
                "run.manager.action.applied",
                "run.manager.action.failed",
                "task.done",
                "verify.passed",
                "test.passed",
                "review.approved",
                "lane.stage.completed",
                "candidate.ready",
                "candidate.integration.completed",
                "ship.completed",
                "ship.failed",
                "ship.blocked",
                "ship.conflict",
                "run.delivery.settled",
                "run.delivery.failed",
                "run.delivery.blocked",
            }:
                self._maybe_complete_run_goal(event)
            if event.type == "judge.passed":
                self._maybe_auto_ship(event)
                self._maybe_complete_run_goal(event)
            # LH-0.T3: stage-progress events reset the orphan clock so
            # a task that's advancing but slow doesn't wrongly trip the
            # warning. Both success and rework-trigger events count as
            # "the worker is alive" — only silence fires orphan.
            if is_stage_progress_event(event.type) and event.task_id:
                self._release_stage_actor_liveness(event)
                self._dispatch_epoch[event.task_id] = self._now()
                self._orphan_warned.discard(event.task_id)
            if event.type in {
                "review.approved",
                "review.rejected",
                "verify.passed",
                "verify.failed",
                "test.passed",
                "test.failed",
                "judge.passed",
                "judge.failed",
            } and event.task_id:
                self._check_reader_write_violation(event)
            if event.type in {
                "review.approved",
                "verify.passed",
                "test.passed",
                "judge.passed",
            } and event.task_id:
                self._rebuild_candidate_for_event(event)
            self._maybe_start_reader_fanout(event)
            self._maybe_start_writer_fanout(event)
            self._maybe_update_reader_fanout(event)
            self._maybe_update_writer_fanout(event)
            # LH-0.T4: clear hard-cap mark when the worker successfully
            # recycled (new session = fresh context).
            if event.type == "worker.recycled" and event.actor:
                self._hard_cap_exceeded.pop(event.actor, None)
        except Exception:
            pass  # housekeeping never blocks the loop

    def _ensure_rework_triage(self, event: ZfEvent):
        existing = self._rework_triage_for_event(event, existing_only=True)
        if existing is not None:
            return existing
        # P1/K2: thread self.config so yaml workflow.rework_routing
        # takes priority over heuristic classifiers.
        result = classify_rework_trigger(event, self.config)
        try:
            self.event_writer.append(ZfEvent(
                type="task.rework.triage.completed",
                actor="zf-cli",
                task_id=event.task_id,
                payload=result.to_payload(event),
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass
        return result

    def _rework_triage_for_event(
        self,
        event: ZfEvent,
        *,
        existing_only: bool = False,
    ):
        trigger_id = event.id
        try:
            for candidate in reversed(self.event_log.read_all()):
                if (
                    candidate.type != "task.rework.triage.completed"
                    or candidate.task_id != event.task_id
                    or not isinstance(candidate.payload, dict)
                ):
                    continue
                if str(candidate.payload.get("failed_event_id") or "") != trigger_id:
                    continue
                parsed = triage_from_payload(candidate.payload)
                if parsed is not None:
                    return parsed
        except Exception:
            pass
        if existing_only:
            return None
        # P1/K2: same priority rule as _ensure_rework_triage.
        return classify_rework_trigger(event, self.config)

    def _check_reader_write_violation(self, event: ZfEvent) -> None:
        role = None
        actor = event.actor or ""
        if actor:
            role = self._find_role_by_instance(actor) or self._find_role_by_name(actor)
        if role is None:
            task = self.task_store.get(event.task_id)
            if task and task.assigned_to:
                role = (
                    self._find_role_by_instance(task.assigned_to)
                    or self._find_role_by_name(task.assigned_to)
                )
        if role is None:
            return
        try:
            from zf.runtime.workdirs import WorkdirManager

            manager = WorkdirManager(
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
            )
            plan = manager.plan(role)
            if plan.role_kind != "reader":
                return
            status = manager.reset_reader_if_dirty(role)
            status_classification = manager.classify_reader_status(status)
        except Exception:
            return
        if not status.strip():
            return
        self.event_writer.append(ZfEvent(
            type="reader.write_violation",
            actor="zf-cli",
            task_id=event.task_id,
            payload={
                "role": role.name,
                "instance_id": role.instance_id,
                "trigger_event": event.type,
                "status": status,
                **status_classification,
                **self._reader_write_policy_payload(status),
                "reset": True,
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))

    def _reader_write_policy_payload(self, status: str) -> dict[str, object]:
        dirty_paths: list[str] = []
        for line in status.splitlines():
            raw = line.rstrip()
            if not raw.strip():
                continue
            path = raw[3:].strip() if len(raw) > 3 and raw[2] == " " else raw.strip()
            if " -> " in path:
                dirty_paths.extend(
                    part.strip() for part in path.split(" -> ") if part.strip()
                )
            elif path:
                dirty_paths.append(path)
        validation_paths = [
            path for path in dirty_paths
            if path == "docs/validation" or path.startswith("docs/validation/")
        ]
        policy = "source_mutation_forbidden"
        recommended = "reset_reader_worktree_and_route_outputs_to_fanout_artifacts"
        if dirty_paths and len(validation_paths) == len(dirty_paths):
            policy = "reader_artifact_policy_missing"
            recommended = (
                "declare artifact output policy or relocate validation output "
                "to fanout child artifacts"
            )
        return {
            "dirty_paths": dirty_paths,
            "policy": policy,
            "recommended_fix": recommended,
            "artifact_output_root": ".zf/fanouts/<fanout_id>/children/<child_id>/",
        }

    def _rebuild_candidate_for_event(self, event: ZfEvent) -> None:
        workdirs = self.config.runtime.workdirs
        if not workdirs.enabled or workdirs.mode != "worktree":
            return
        try:
            from zf.runtime.candidates import CandidateRebuilder

            CandidateRebuilder(
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
                event_log=self.event_log,
            ).rebuild_for_event(event, event_writer=self.event_writer)
        except Exception:
            return


    def _ensure_writer_tasks_canonical(self, loaded) -> None:
        """Register writer task-map tasks and refresh replan refs."""
        from zf.core.task.schema import Task, TaskContract
        from zf.runtime.verification_commands import (
            task_map_contract_verification_fields,
        )
        from zf.runtime.writer_task_map_supersede import apply_explicit_task_supersedes
        apply_explicit_task_supersedes(
            task_store=self.task_store, event_writer=self.event_writer, loaded=loaded
        )

        feature_id = str(loaded.feature_id or loaded.pdd_id or "")
        default_owner_role = next(
            (
                role.name
                for role in self.config.roles
                if getattr(role, "role_kind", "") == "writer"
            ),
            "dev",
        )
        pending_new_tasks = []
        for item in loaded.task_items:
            task_id = str(item.get("task_id") or "")
            if not task_id:
                continue
            existing = self.task_store.get(task_id)
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            raw = item.get("raw_task") if isinstance(item.get("raw_task"), dict) else {}
            scope = str(item.get("scope") or task_id)
            verification_tiers = self._writer_task_contract_list(
                raw.get("verification_tiers")
                or item.get("verification_tiers")
                or payload.get("verification_tiers")
            ) or ["runtime"]
            source_refs = {
                "task_map_ref": loaded.task_map_ref,
            }
            if loaded.source_index_ref:
                source_refs["source_index_ref"] = loaded.source_index_ref
            from zf.runtime.writer_task_materialization import bind_plan_package_source_refs
            bind_plan_package_source_refs(source_refs, loaded)
            module_id = self._first_nonempty(
                raw.get("module_id"),
                payload.get("module_id"),
            )
            gap_kind = self._first_nonempty(
                raw.get("gap_kind"),
                payload.get("gap_kind"),
            )
            parent_task_id = self._first_nonempty(
                raw.get("parent_task_id"),
                payload.get("parent_task_id"),
            )
            affinity_tag = self._first_nonempty(
                raw.get("affinity_tag"),
                item.get("affinity_tag"),
            )
            raw_evidence_contract = (
                raw.get("evidence_contract")
                if isinstance(raw.get("evidence_contract"), dict)
                else {}
            )
            goal_id = self._first_nonempty(
                raw.get("goal_id"),
                payload.get("goal_id"),
                raw_evidence_contract.get("goal_id"),
            )
            goal_kind = self._first_nonempty(
                raw.get("goal_kind"),
                payload.get("goal_kind"),
                raw_evidence_contract.get("goal_kind"),
            )
            gap_category = self._first_nonempty(
                raw.get("gap_category"),
                payload.get("gap_category"),
                raw_evidence_contract.get("gap_category"),
            )
            product_contract_ref = self._first_nonempty(
                raw.get("product_contract_ref"),
                raw.get("spec_ref"),
                raw.get("plan_ref"),
                loaded.task_map_ref,
            )
            # Carry the task_map's behavior, verification, and source lineage onto
            # the contract. Dispatch preflight requires these fields before any
            # writer task can enter the normal worker path.
            behavior = str(payload.get("instruction") or item.get("behavior") or scope)
            verification, validation = task_map_contract_verification_fields(
                raw, item,
            )
            acceptance_criteria = (
                self._writer_acceptance_criteria(raw.get("acceptance_criteria"))
                or self._writer_acceptance_criteria(raw.get("acceptance"))
                or self._writer_acceptance_criteria(item.get("acceptance_criteria"))
                or self._writer_acceptance_criteria(item.get("acceptance"))
            )
            blocked_by = (
                self._writer_task_contract_list(raw.get("blocked_by"))
                or self._writer_task_contract_list(item.get("blocked_by"))
            )
            raw_owner_role = self._first_nonempty(
                raw.get("owner_role"),
                item.get("owner_role"),
                default_owner_role,
            )
            raw_owner_instance = self._first_nonempty(
                raw.get("owner_instance"),
                item.get("owner_instance"),
            )
            owner_role = self._task_map_runtime_owner_role(
                raw_owner_role,
                default_owner_role,
            )
            owner_instance = self._task_map_runtime_owner_instance(
                raw_owner_instance,
                raw_owner_role,
            )
            evidence_contract = dict(raw_evidence_contract)
            evidence_contract.update({
                "source": "refactor_task_map",
                "source_refs": source_refs,
                "module_id": module_id,
                "gap_kind": gap_kind,
                "affinity_tag": affinity_tag,
                "parent_task_id": parent_task_id,
            })
            if goal_id:
                evidence_contract["goal_id"] = goal_id
            if goal_kind:
                evidence_contract["goal_kind"] = goal_kind
            if gap_category:
                evidence_contract["gap_category"] = gap_category
            for key in (
                "affected_tasks",
                "gate_changes",
                "replan_history_ref",
                "acceptance_id",
                "repro_ref",
            ):
                value = raw.get(key, raw_evidence_contract.get(key))
                if value not in (None, "", []):
                    evidence_contract[key] = value
            if raw_owner_role and raw_owner_role not in {owner_role, owner_instance}:
                evidence_contract["semantic_owner_role"] = raw_owner_role
            contract = TaskContract(
                feature_id=feature_id,
                parent_task_id=parent_task_id,
                behavior=behavior,
                verification=verification,
                validation=validation,
                verification_tiers=verification_tiers,
                # allowed_paths first: in the refactor task_map dialect `scope`
                # is prose while `allowed_paths` carries path globs for task_refs.
                scope=(
                    self._writer_task_contract_list(item.get("allowed_paths"))
                    or self._writer_task_contract_list(raw.get("scope"))
                    or [scope]
                ),
                product_contract_ref=product_contract_ref,
                spec_ref=str(raw.get("spec_ref") or "").strip(),
                plan_ref=str(raw.get("plan_ref") or "").strip(),
                source_ref=self._first_nonempty(raw.get("source_ref"), loaded.task_map_ref),
                source_key=self._first_nonempty(
                    raw.get("source_key"),
                    f"{loaded.task_map_ref}#{task_id}",
                ),
                source_task_id=self._first_nonempty(raw.get("source_task_id"), task_id),
                source_index_ref=loaded.source_index_ref,
                source_mode=str(raw.get("source_mode") or "canonical").strip(),
                owner_role=owner_role,
                owner_instance=owner_instance,
                acceptance=(
                    "; ".join(map(self._writer_acceptance_text, acceptance_criteria))
                    if acceptance_criteria
                    else "exit_code=0"
                ),
                acceptance_criteria=acceptance_criteria,
                goal_claim_ids=self._writer_task_contract_list(
                    raw.get("goal_claim_ids") or item.get("goal_claim_ids")
                ),
                evidence_contract=evidence_contract,
            )
            refreshed_task = Task(
                id=task_id,
                title=scope,
                status="backlog",
                blocked_by=blocked_by,
                contract=contract,
            )
            if existing is None:
                pending_new_tasks.append(refreshed_task)
            elif self._can_refresh_writer_task(existing, loaded):
                old_ref = self._task_contract_task_map_ref(existing.contract)
                old_status = str(getattr(existing, "status", "") or "")
                reset_for_replan = bool(
                    getattr(loaded, "is_replan", False)
                    and old_status in {
                        "done",
                        "blocked",
                        "failed",
                        "review",
                        "test",
                    }
                )
                if reset_for_replan:
                    self.task_store.reopen(refreshed_task)
                    reopened = old_status == "done"
                else:
                    self.task_store.update(
                        task_id,
                        title=scope,
                        blocked_by=blocked_by,
                        contract=contract,
                    )
                    reopened = False
                self.event_writer.append(ZfEvent(
                    type="task.contract.update",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "source": self._writer_task_refresh_source(
                            existing,
                            loaded,
                        ),
                        "feature_id": feature_id,
                        "old_task_map_ref": old_ref,
                        "new_task_map_ref": loaded.task_map_ref,
                        "replan": bool(getattr(loaded, "is_replan", False)),
                        "old_status": old_status,
                        "reopened_from_terminal": reopened,
                        "reset_for_replan": reset_for_replan,
                    },
                ))
        from zf.runtime.writer_task_materialization import materialize_writer_tasks
        materialize_writer_tasks(self, pending_new_tasks, loaded)

    def _task_map_runtime_owner_role(
        self,
        raw_owner_role: str,
        default_owner_role: str,
    ) -> str:
        raw = str(raw_owner_role or "").strip()
        if not raw:
            return default_owner_role
        for role in self.config.roles:
            if role.name == raw:
                return role.name
        for role in self.config.roles:
            if role.instance_id == raw:
                return role.name
        return default_owner_role

    def _task_map_runtime_owner_instance(
        self,
        raw_owner_instance: str,
        raw_owner_role: str,
    ) -> str:
        for value in (raw_owner_instance, raw_owner_role):
            raw = str(value or "").strip()
            if not raw:
                continue
            for role in self.config.roles:
                if role.instance_id == raw:
                    return role.instance_id
        return ""

    @staticmethod
    def _task_contract_task_map_ref(contract) -> str:
        evidence = getattr(contract, "evidence_contract", {}) or {}
        if not isinstance(evidence, dict):
            return ""
        refs = evidence.get("source_refs")
        if isinstance(refs, dict):
            return str(refs.get("task_map_ref") or "").strip()
        manual = evidence.get("manual_evidence")
        if isinstance(manual, dict):
            manual_refs = manual.get("source_refs")
            if isinstance(manual_refs, dict):
                return str(manual_refs.get("task_map_ref") or "").strip()
        return ""

    def _can_refresh_writer_task(self, task, loaded) -> bool:
        if str(getattr(task, "status", "") or "") in {"cancelled", "superseded"}:
            return False
        contract = getattr(task, "contract", None)
        if contract is None:
            return False
        evidence = getattr(contract, "evidence_contract", {}) or {}
        if self._is_workflow_bootstrap_task(task, evidence):
            return bool(loaded.task_map_ref) and str(
                getattr(task, "status", "") or "backlog"
            ) in {"", "backlog", "todo"}
        if not getattr(loaded, "is_replan", False):
            return False
        if str(getattr(contract, "feature_id", "") or "") != str(
            loaded.feature_id or loaded.pdd_id or ""
        ):
            return False
        if isinstance(evidence, dict) and str(evidence.get("source") or "") != "refactor_task_map":
            return False
        old_ref = self._task_contract_task_map_ref(contract)
        if not old_ref or not loaded.task_map_ref:
            return False
        if old_ref != loaded.task_map_ref:
            return True
        return str(getattr(task, "status", "") or "") in {
            "blocked",
            "failed",
            "review",
            "test",
        }

    @staticmethod
    def _is_workflow_bootstrap_task(task, evidence: object | None = None) -> bool:
        """A workflow submit creates a placeholder kanban task before the real
        triage/plan task_map exists. The first task_map.ready must be allowed
        to replace that placeholder contract; otherwise writer admission sees
        the empty task_map_ref as stale and the run dead-ends before impl."""
        if evidence is None:
            contract = getattr(task, "contract", None)
            evidence = getattr(contract, "evidence_contract", None)
        if not isinstance(evidence, dict):
            return False
        if str(evidence.get("source") or "") != "workflow_invoke_bootstrap":
            return False
        if not bool(evidence.get("workflow_fanout_anchor")):
            return False
        contract = getattr(task, "contract", None)
        if contract is None:
            return False
        refs = evidence.get("source_refs")
        if isinstance(refs, dict) and str(refs.get("task_map_ref") or "").strip():
            return False
        return True

    def _writer_task_refresh_source(self, task, loaded) -> str:
        evidence = getattr(getattr(task, "contract", None), "evidence_contract", {}) or {}
        if self._is_workflow_bootstrap_task(task, evidence):
            return "workflow_task_map_adoption"
        if getattr(loaded, "is_replan", False):
            return "refactor_replan_adoption"
        return "task_map_contract_refresh"

    def _release_affinity_writer_slot_and_dispatch_next(
        self,
        *,
        fanout_id: str,
        completed_payload: dict,
        causation_id: str,
    ) -> None:
        lane_id = str(completed_payload.get("lane_id") or "")
        stage_slot = str(completed_payload.get("stage_slot") or "")
        stage_id = str(completed_payload.get("stage_id") or "")
        if not lane_id or not stage_slot or not stage_id:
            return
        stage = self._fanout_stage_by_id(stage_id)
        if stage is None:
            return
        role = self._fanout_affinity_lane_role(
            stage,
            lane_id=lane_id,
            stage_slot=stage_slot,
        )
        if role is None:
            return
        manifest = self._fanout_manifest(fanout_id)
        if not manifest:
            return
        trace_id = str(manifest.get("trace_id") or completed_payload.get("trace_id") or "")
        release_payload = {
            "fanout_id": fanout_id,
            "trace_id": trace_id,
            "stage_id": stage_id,
            "child_id": str(completed_payload.get("child_id") or ""),
            "run_id": str(completed_payload.get("run_id") or ""),
            "role_instance": role.instance_id,
            "lane_id": lane_id,
            "stage_slot": stage_slot,
            "task_id": str(completed_payload.get("task_id") or ""),
            "affinity_tag": str(completed_payload.get("affinity_tag") or ""),
        }
        release_event = self.event_writer.append(ZfEvent(
            type="fanout.slot.released",
            actor="zf-cli",
            payload=release_payload,
            causation_id=causation_id,
            correlation_id=trace_id,
        ))
        self._reconcile_affinity_writer_slots(
            fanout_id=fanout_id,
            stage=stage,
            stage_slot=stage_slot,
            causation_id=release_event.id,
        )

    def _reconcile_affinity_writer_slots(
        self,
        *,
        fanout_id: str,
        stage,
        stage_slot: str,
        causation_id: str,
    ) -> int:
        """Fill every free affinity lane with one dependency-ready child."""
        from zf.runtime.writer_slot_reconciler import (
            reconcile_affinity_writer_slots,
        )

        return reconcile_affinity_writer_slots(
            self,
            fanout_id=fanout_id,
            stage=stage,
            stage_slot=stage_slot,
            causation_id=causation_id,
        )


























    def _publish_refactor_plan_manifest(
        self,
        *,
        manifest: dict,
        projection_payload: dict,
        trace_id: str,
    ) -> None:
        """doc 78 W3a: publish the refactor plan into the artifact-ledger
        version chain so task_map_history / delivery_trace can render the
        re-plan supersedes chain. Emitted with actor=zf-cli + role!=orchestrator
        + no product_delivery handoff, and with no task_id, so the reactor's
        _on_artifact_manifest_published early-returns (no plan-only-done /
        product-delivery-spine side effects). Best-effort; never break the tick.
        """
        try:
            from zf.runtime.refactor_artifacts import build_plan_manifest_payload

            feature_id = str(
                manifest.get("feature_id")
                or manifest.get("pdd_id")
                or str(manifest.get("target_ref") or "").rsplit("/", 1)[-1]
                or ""
            ).strip()
            if not feature_id:
                return
            payload = build_plan_manifest_payload(
                projection_payload=projection_payload,
                feature_id=feature_id,
                source_event_id=str(manifest.get("trigger_event_id") or ""),
                is_replan=bool(manifest.get("rework_attempt") or manifest.get("rework_of")),
            )
            if not payload.get("artifact_refs"):
                return
            self.event_writer.append(ZfEvent(
                type="artifact.manifest.published",
                actor="zf-cli",
                payload=payload,
                correlation_id=trace_id,
            ))
        except Exception:
            pass

    def _bridge_refactor_plan_ready_to_task_map(
        self,
        *,
        manifest: dict,
        projection_payload: dict,
        trace_id: str,
    ) -> None:
        """P0-2 (2026-06-19 e2e): deterministically convert a gate-passed
        refactor ``zaofu.refactor.plan.ready`` into the first ``task_map.ready``
        and start the writer (impl) fanout.

        The refactor-flow/v1 profile lists ``task_map.ready`` as an
        external_trigger and the only kernel emit lives in the candidate-rework
        sweep (re-plan only). On a fresh plan nothing produced task_map.ready,
        so the impl lane never started and the orchestrator livelocked
        re-waking on plan.ready. The projection payload here has already passed
        the lane_pipeline admission gate (``_compile_refactor_plan_projection``),
        so its task_map_ref is dispatchable. Payload shape mirrors the rework
        sweep's task_map.ready. ``_maybe_start_writer_fanout`` owns its own
        re-arm guard, so a re-aggregated plan will not restart the impl fanout.
        """
        task_map_ref = str(projection_payload.get("task_map_ref") or "").strip()
        if not task_map_ref:
            return

        def _pick(key: str) -> str:
            return str(
                projection_payload.get(key) or manifest.get(key) or ""
            ).strip()

        event = self.event_writer.append(ZfEvent(
            type="task_map.ready",
            actor="zf-cli",
            payload={
                "pdd_id": _pick("pdd_id") or _pick("feature_id"),
                "feature_id": _pick("feature_id") or _pick("pdd_id"),
                "trace_id": trace_id,
                "task_map_ref": task_map_ref,
                "source_index_ref": _pick("source_index_ref"),
                "source_commit": _pick("source_commit"),
                "candidate_base_commit": _pick("candidate_base_commit")
                or _pick("source_commit"),
                "target_ref": _pick("target_ref"),
                "source": "refactor_plan_bridge",
                **self._refactor_replan_payload(projection_payload),
            },
            correlation_id=trace_id,
        ))
        self._maybe_start_writer_fanout(event)

    def _bridge_gap_plan_ready_to_task_map(self, event: ZfEvent) -> OrchestratorDecision | None:
        """Deterministically convert module parity gap plans into dispatchable work.

        The bridge writes an amended full task-map artifact and then reuses the
        normal ``task_map.ready`` writer-fanout path. It does not mutate
        TaskStore directly; canonical task materialization remains owned by the
        writer fanout admission/seed path.
        """
        import json

        from zf.runtime.module_gap_plan import (
            gap_tasks_from_gap_plan_payload,
            write_gap_task_map_amend_artifact,
        )

        gap_event_type = event.type or "gap_plan.ready"
        if self._has_bridge_output(event.id, {"task_map.amended"}):
            return OrchestratorDecision(
                action="noop",
                reason=f"{gap_event_type} already amended task_map",
            )

        payload = event.payload if isinstance(event.payload, dict) else {}
        pdd_id = str(payload.get("pdd_id") or payload.get("feature_id") or "").strip()
        feature_id = str(payload.get("feature_id") or pdd_id).strip()
        trace_id = str(payload.get("trace_id") or event.correlation_id or event.id).strip()
        base_task_map_ref = str(
            payload.get("task_map_ref")
            or payload.get("base_task_map_ref")
            or payload.get("supersedes_task_map_ref")
            or ""
        ).strip()
        if not pdd_id or not base_task_map_ref:
            self.event_writer.append(ZfEvent(
                type="task_map.amend.failed",
                actor="zf-cli",
                causation_id=event.id,
                correlation_id=trace_id,
                payload={
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "trace_id": trace_id,
                    "reason": f"{gap_event_type} requires pdd_id and task_map_ref",
                    "gap_event_type": gap_event_type,
                    "source_event_id": event.id,
                },
            ))
            return OrchestratorDecision(
                action="block",
                reason=f"{gap_event_type} missing pdd_id or task_map_ref",
            )
        gap_plan_ref = str(payload.get("gap_plan_ref") or "").strip()
        gap_plan_payload = dict(payload)
        if gap_plan_ref and not gap_tasks_from_gap_plan_payload(gap_plan_payload):
            try:
                path = self._resolve_runtime_artifact_ref(gap_plan_ref)
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    gap_plan_payload = data
            except Exception as exc:
                self.event_writer.append(ZfEvent(
                    type="task_map.amend.failed",
                    actor="zf-cli",
                    causation_id=event.id,
                    correlation_id=trace_id,
                    payload={
                        "pdd_id": pdd_id,
                        "feature_id": feature_id,
                        "trace_id": trace_id,
                        "task_map_ref": base_task_map_ref,
                        "gap_plan_ref": gap_plan_ref,
                        "reason": f"gap_plan_ref unreadable: {exc}",
                        "gap_event_type": gap_event_type,
                        "source_event_id": event.id,
                    },
                ))
                return OrchestratorDecision(
                    action="block",
                    reason=f"{gap_event_type} gap_plan_ref unreadable",
                )
        gap_tasks = gap_tasks_from_gap_plan_payload(gap_plan_payload)
        if not gap_tasks:
            self.event_writer.append(ZfEvent(
                type="task_map.amend.failed",
                actor="zf-cli",
                causation_id=event.id,
                correlation_id=trace_id,
                payload={
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "trace_id": trace_id,
                    "task_map_ref": base_task_map_ref,
                    "gap_plan_ref": gap_plan_ref,
                    "reason": f"{gap_event_type} contains no gap_tasks",
                    "gap_event_type": gap_event_type,
                    "source_event_id": event.id,
                },
            ))
            return OrchestratorDecision(
                action="block",
                reason=f"{gap_event_type} contains no gap_tasks",
            )
        requested = self.event_writer.append(ZfEvent(
            type="task_map.amend.requested",
            actor="zf-cli",
            causation_id=event.id,
            correlation_id=trace_id,
            payload={
                "schema_version": "task-map-amend-request.v1",
                "pdd_id": pdd_id,
                "feature_id": feature_id,
                "trace_id": trace_id,
                "task_map_ref": base_task_map_ref,
                "gap_plan_ref": gap_plan_ref,
                "gap_task_count": len(gap_tasks),
                "gap_event_type": gap_event_type,
                "source_event_id": event.id,
                "source": "gap_plan_bridge",
            },
        ))
        try:
            amend = write_gap_task_map_amend_artifact(
                state_dir=self.state_dir,
                project_root=self.project_root,
                base_task_map_ref=base_task_map_ref,
                pdd_id=pdd_id,
                source_event_id=event.id,
                gap_tasks=gap_tasks,
                gap_plan_ref=gap_plan_ref,
            )
        except Exception as exc:
            self.event_writer.append(ZfEvent(
                type="task_map.amend.failed",
                actor="zf-cli",
                causation_id=requested.id,
                correlation_id=trace_id,
                payload={
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "trace_id": trace_id,
                    "task_map_ref": base_task_map_ref,
                    "gap_plan_ref": gap_plan_ref,
                    "reason": str(exc),
                    "gap_event_type": gap_event_type,
                    "source_event_id": event.id,
                    "request_event_id": requested.id,
                },
            ))
            return OrchestratorDecision(
                action="block",
                reason=f"task_map amend failed: {exc}",
            )
        gap_task_ids = list(amend.get("gap_task_ids") or [])
        amended = self.event_writer.append(ZfEvent(
            type="task_map.amended",
            actor="zf-cli",
            causation_id=requested.id,
            correlation_id=trace_id,
            payload={
                "schema_version": "task-map-amended.v1",
                "pdd_id": pdd_id,
                "feature_id": feature_id,
                "trace_id": trace_id,
                "task_map_ref": str(amend.get("task_map_ref") or ""),
                "supersedes_task_map_ref": base_task_map_ref,
                "gap_plan_ref": gap_plan_ref,
                "gap_task_ids": gap_task_ids,
                "gap_task_count": len(gap_task_ids),
                "superseded_task_ids": list(amend.get("superseded_task_ids") or []),
                "gap_event_type": gap_event_type,
                "source_event_id": event.id,
                "request_event_id": requested.id,
                "source": "gap_plan_bridge",
            },
        ))
        ready = self.event_writer.append(ZfEvent(
            type="task_map.ready",
            actor="zf-cli",
            causation_id=amended.id,
            correlation_id=trace_id,
            payload={
                "pdd_id": pdd_id,
                "feature_id": feature_id,
                "trace_id": trace_id,
                "task_map_ref": str(amend.get("task_map_ref") or ""),
                "source_index_ref": str(payload.get("source_index_ref") or ""),
                "source_commit": str(payload.get("source_commit") or ""),
                "candidate_base_commit": str(
                    payload.get("candidate_base_commit")
                    or payload.get("source_commit")
                    or ""
                ),
                "dispatch_base_commit": str(
                    payload.get("dispatch_base_commit")
                    or payload.get("candidate_head_commit")
                    or payload.get("candidate_base_commit")
                    or payload.get("source_commit")
                    or ""
                ),
                "target_ref": str(payload.get("target_ref") or ""),
                "amend_of": base_task_map_ref,
                "gap_plan_ref": gap_plan_ref,
                "gap_task_ids": gap_task_ids,
                "task_ids": gap_task_ids,
                "superseded_task_ids": list(amend.get("superseded_task_ids") or []),
                "resume_scope": "gap_tasks_only",
                "gap_event_type": gap_event_type,
                "source_event_id": event.id,
                "source": "gap_plan_bridge",
            },
        ))
        self._maybe_start_writer_fanout(ready)
        return OrchestratorDecision(
            action="bridge",
            reason=f"{gap_event_type} amended task_map for {len(gap_task_ids)} gap task(s)",
        )

    def _resolve_runtime_artifact_ref(self, ref: str) -> Path:
        path = Path(ref)
        if path.is_absolute():
            return path
        if path.parts and path.parts[0] == ".zf":
            return self.state_dir.joinpath(*path.parts[1:])
        return self.project_root / path

    @staticmethod
    def _refactor_replan_payload(payload: dict) -> dict:
        out: dict = {}
        for key in (
            "rework_of",
            "rework_attempt",
            "rework_source",
            "rework_feedback",
            "rework_categories",
            "rework_summary",
            "replan_classification",
            "replan",
            "orchestrator_decision",
            "supersedes_plan_fanout_id",
            "supersedes_plan_artifact_refs",
        ):
            value = payload.get(key)
            if value not in (None, "", []):
                out[key] = value
        return out

    def _publish_task_map_version_manifest(
        self,
        *,
        loaded,
        trace_id: str,
        is_replan: bool,
    ) -> None:
        """doc 78 W3a: publish an orchestrator-produced task_map into the
        artifact-ledger version chain (kind=task_map keyed by feature_id), so
        task_map_history / delivery_trace render the re-plan supersedes chain.
        Reactor-collision-safe: actor=zf-cli, no task_id, role!=orchestrator, no
        product_delivery handoff. Best-effort; never break the fanout.
        """
        try:
            feature_id = str(
                getattr(loaded, "feature_id", "")
                or getattr(loaded, "pdd_id", "")
                or ""
            ).strip()
            task_map_ref = str(getattr(loaded, "task_map_ref", "") or "").strip()
            if not feature_id or not task_map_ref:
                return
            sha = ""
            path = getattr(loaded, "task_map_path", None)
            if path is not None:
                try:
                    from zf.core.security.hash import sha256_file

                    sha = sha256_file(path)
                except Exception:
                    sha = ""
            self.event_writer.append(ZfEvent(
                type="artifact.manifest.published",
                actor="zf-cli",
                payload={
                    "role": "refactor-plan",
                    "feature_id": feature_id,
                    "artifact_refs": [{
                        "kind": "task_map",
                        "path": task_map_ref,
                        "sha256": sha,
                        "summary": "orchestrator task-map" + (" (replan)" if is_replan else ""),
                        "status": "accepted",
                    }],
                    "handoff_contract": {"source": "refactor_task_map"},
                },
                correlation_id=trace_id,
            ))
        except Exception:
            pass

    def _project_refactor_success_artifacts(
        self,
        *,
        manifest: dict,
        success_event: str,
        synth_event: ZfEvent | None = None,
    ):
        from zf.runtime.refactor_artifacts import project_refactor_artifacts

        projection = project_refactor_artifacts(
            state_dir=self.state_dir,
            manifest=manifest,
            success_event=success_event,
            synth_event=synth_event,
        )
        if success_event in {"zaofu.refactor.plan.ready", "refactor.plan.ready"}:
            return self._compile_refactor_plan_projection(projection)
        return projection

    def _compile_refactor_plan_projection(self, projection):
        """Compile a refactor plan task_map against deterministic workflow shape.

        This is a structural gate, not a subjective quality review. It reuses
        the writer-fanout task normalization and lane_pipeline admission
        contract so plan artifacts that cannot be dispatched fail before
        publishing ``*.plan.ready``.
        """
        if projection is None or not getattr(projection, "ok", False):
            return projection
        payload = dict(getattr(projection, "payload", {}) or {})
        if payload.get("artifact_kind") != "refactor_plan":
            return projection
        task_map_ref = str(payload.get("task_map_ref") or "").strip()
        pipeline_spec = self._lane_pipeline_for_trigger("task_map.ready")
        if not task_map_ref or pipeline_spec is None:
            if task_map_ref:
                payload["plan_compile_gate"] = "skipped"
                projection.payload.update(payload)
            return projection

        diagnostics: list[str] = []
        try:
            import json
            from zf.core.security.hash import sha256_file
            from zf.core.workflow.lane_pipeline import (
                validate_lane_pipeline_admission,
            )
            from zf.runtime.refactor_artifacts import RefactorArtifactProjection
            from zf.runtime.task_map import validate_task_map_payload
            from zf.runtime.writer_fanout_admission import (
                _resolve_artifact_ref,
                validate_writer_task_items,
                writer_task_items,
            )

            task_map_path = _resolve_artifact_ref(
                task_map_ref,
                state_dir=self.state_dir,
                project_root=self.project_root,
            )
            data = json.loads(task_map_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                task_map_validation = validate_task_map_payload(
                    data,
                    require_task_verification=False,
                )
                if not task_map_validation.passed:
                    diagnostics.extend(
                        f"task_map: {error}"
                        for error in task_map_validation.errors
                    )
            task_items = writer_task_items(data)
            validate_writer_task_items(task_items)
            problems = validate_lane_pipeline_admission(
                pipeline_spec,
                task_items,
                task_map_payload=data if isinstance(data, dict) else None,
            )
            diagnostics.extend(f"lane_pipeline: {problem}" for problem in problems)
        except Exception as exc:  # fail closed: malformed task_map is not ready.
            diagnostics.append(f"plan compile failed: {exc}")

        if not diagnostics:
            payload["plan_compile_gate"] = "passed"
            projection.payload.update(payload)
            return projection

        artifact_dir = Path(
            str(payload.get("artifact_dir") or getattr(projection, "artifact_dir", ""))
        )
        if not artifact_dir:
            artifact_dir = self.state_dir / "artifacts" / "refactor-plan"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        diagnostics_path = artifact_dir / "artifact-gate-diagnostics.json"
        diagnostics_path.write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        artifact_refs = list(dict.fromkeys(
            [str(ref) for ref in payload.get("artifact_refs", []) or [] if str(ref)]
            + [str(diagnostics_path)]
        ))
        payload.update({
            "artifact_gate": "failed",
            "plan_compile_gate": "failed",
            "diagnostics_ref": str(diagnostics_path),
            "artifact_refs": artifact_refs,
            "artifact_digests": {
                ref: sha256_file(Path(ref))
                for ref in artifact_refs
                if Path(ref).exists() and Path(ref).is_file()
            },
        })
        return RefactorArtifactProjection(
            status="failed",
            artifact_dir=str(artifact_dir),
            artifact_refs=artifact_refs,
            payload=payload,
            diagnostics=diagnostics,
        )


    def _candidate_ready_contract_payload(
        self,
        *,
        candidate_payload: dict,
        pdd_id: str,
        feature_id: str,
        task_map_ref: str,
        source_index_ref: str,
        completed_task_ids: list[str],
    ) -> dict:
        candidate_ref = str(candidate_payload.get("branch") or "")
        base_commit = str(candidate_payload.get("base_commit") or "")
        head_commit = str(candidate_payload.get("commit") or "")
        diff_ref = (
            str(candidate_payload.get("diff_ref") or "")
            or (
                f"{base_commit}..{head_commit}"
                if base_commit and head_commit
                else candidate_ref
            )
        )
        quality = (
            candidate_payload.get("quality")
            if isinstance(candidate_payload.get("quality"), dict)
            else {}
        )
        candidate_environment = (
            candidate_payload.get("candidate_environment")
            if isinstance(candidate_payload.get("candidate_environment"), dict)
            else {}
        )
        return {
            "pdd_id": pdd_id,
            "feature_id": feature_id,
            "task_map_ref": task_map_ref,
            "source_index_ref": source_index_ref,
            "candidate_ref": candidate_ref,
            "candidate_base_commit": base_commit,
            "candidate_head_commit": head_commit,
            "diff_ref": diff_ref,
            "completed_task_ids": completed_task_ids,
            "quality_status": str(candidate_payload.get("quality_status") or ""),
            "quality_check_count": int(quality.get("check_count") or 0),
            "quality_gates_passed": list(quality.get("gates_passed") or []),
            "quality_gates_failed": list(quality.get("gates_failed") or []),
            "candidate_environment": dict(candidate_environment),
        }

    def _load_writer_task_map(self, stage, event: ZfEvent, pdd_id: str) -> list[dict]:
        from zf.runtime.writer_fanout_admission import load_writer_task_map

        return load_writer_task_map(
            stage=stage,
            event=event,
            pdd_id=pdd_id,
            state_dir=self.state_dir,
            project_root=self.project_root,
            candidate_quality_source=str(getattr(
                self.config.workflow,
                "candidate_quality_source",
                "auto",
            ) or "auto"),
            work_units_config=getattr(
                self.config.workflow, "work_units", None,
            ),
        ).task_items


    @staticmethod
    def _skill_briefing_section(
        role: RoleConfig,
        skill_entries: list | None = None,
    ) -> list[str]:
        if not role.skills:
            return []
        entries_by_name = {
            getattr(entry, "name", ""): entry
            for entry in (skill_entries or [])
        }
        lines = [
            "## Enabled Skills",
            "",
            "These skills are enabled by `zf.yaml` for this Star child. "
            "Use them when their description matches the task; they define "
            "review, verification, or output rules for this run.",
        ]
        for skill in role.skills:
            entry = entries_by_name.get(skill)
            description = (
                str(getattr(entry, "description", "") or "").strip()
                if entry is not None else ""
            )
            status = (
                str(getattr(entry, "status", "") or "").strip()
                if entry is not None else ""
            )
            source = (
                str(getattr(entry, "source", "") or "").strip()
                if entry is not None else ""
            )
            runtime = (
                str(getattr(entry, "materialized_to", "") or "").strip()
                if entry is not None else ""
            )
            metadata = [
                item for item in (
                    f"status: {status}" if status else "",
                    f"source: {source}" if source else "",
                    f"runtime: {runtime}" if runtime else "",
                )
                if item
            ]
            suffix = f" ({'; '.join(metadata)})" if metadata else ""
            if description:
                lines.append(f"- `/{skill}` - {description}{suffix}")
            else:
                lines.append(f"- `/{skill}`{suffix}")
        lines.append("")
        return lines






    def _notify_orchestrator_agent(self, event: ZfEvent) -> None:
        """Layer 2 dispatch: build orchestrator briefing + send via transport.

        No-op if:
          - the config has no `orchestrator` role (Layer 2 not opt-in), OR
          - the event type is not in the orchestrator role's `triggers` list

        The trigger filter prevents Layer 2 from being woken on every system
        event (e.g. loop.started, agent.usage). Only events the orchestrator
        role explicitly subscribes to via `triggers` reach Layer 2.
        """
        orch_role = self._find_role_by_name("orchestrator")
        if orch_role is None:
            return
        # B6a (R25 ISSUE-006 根因①): telemetry 噪音硬性不唤 Layer 2 —
        # 一次 Layer 2 turn 是一次完整 agent 推理(15s+),worker 活跃期
        # hook 流速 > 推理吞吐 → cursor 永久积压(R25: lag 2.9h)。hook/
        # usage 类由 Layer 1 handler 消费即可;orch_role.triggers 为空
        # = 订阅一切的旧语义仅对非噪音事件保留。显式订阅可覆盖
        # (triggers 列出噪音类型则仍唤,运维调试口)。
        from zf.runtime.wake_patterns import LAYER2_NOISE_EVENTS

        if (
            event.type in LAYER2_NOISE_EVENTS
            and event.type not in (orch_role.triggers or ())
        ):
            return
        if orch_role.triggers and event.type not in orch_role.triggers:
            return
        # B11: skip dispatch while Layer 2 is in cool-down (set after a
        # recent agent.api_blocked or agent.timeout). The trigger event
        # is intentionally NOT replayed when cool-down expires — Layer 2
        # will see it next time it wakes via the event tail.
        now_ts = time.time()
        if now_ts < self._layer2_blocked_until:
            self.event_writer.append(ZfEvent(
                type="orchestrator.dispatch_skipped",
                actor="zf-cli",
                payload={
                    "trigger_event_id": event.id,
                    "reason": "layer2_cooldown",
                    "remaining_s": round(self._layer2_blocked_until - now_ts, 1),
                },
            ))
            return
        # ZF-E2E-MINI-P2 (2026-07-11): budget-freeze silence. When the global
        # budget is frozen, the control loop's own paid dispatch is blocked at
        # the charging primitive (being wakeable and being budget-gated
        # resolve the same orchestrator role), so stall/attention wakes only
        # burn a briefing + dispatch_failed per sweep re-emit (~5min cycle in
        # the mini e2e). First wake per freeze episode passes as the
        # observability anchor; the rest are silenced until the freeze lifts.
        from zf.runtime.wake_patterns import LAYER2_FREEZE_SILENCED_EVENTS

        if event.type in LAYER2_FREEZE_SILENCED_EVENTS:
            frozen = self._global_budget_frozen()
            if frozen and self._layer2_freeze_wake_fired:
                self.event_writer.append(ZfEvent(
                    type="orchestrator.dispatch_skipped",
                    actor="zf-cli",
                    payload={
                        "trigger_event_id": event.id,
                        "reason": "budget_freeze_silence",
                    },
                ))
                return
            self._layer2_freeze_wake_fired = frozen
        # Batch coalescing (doc 66 §14.0): while inside a run_once reaction
        # batch, every trigger event accumulates into _layer2_pending and the
        # batch fires ONE multi-trigger turn at its end (_flush_layer2_batch).
        # stream-json send_task blocks + run_once is single-threaded, so a
        # per-event turn would serialize N briefings; batch-level is the correct
        # idle-gating unit.
        if self._layer2_in_batch:
            # Internal accumulation — the batch fires this same run_once at its
            # end, so this is NOT a suppressed/deferred dispatch. Emit no
            # dispatch_skipped (that signal means "suppressed, retried later").
            self._add_layer2_pending(event)
            return
        # Timer coalescing (cross-batch): collapse rapid bursts that arrive in
        # separate run_once calls within wake_min_interval_s. Pending is flushed
        # by _flush_pending_layer2_wake (run_once top / ~5s idle tick), never
        # dropped. First-ever wake passes (_layer2_last_wake_at is 0.0).
        interval = self._layer2_wake_min_interval_s
        # FIX-5②(bizsim r4 $697 空转账单):同型触发连发指数退避——
        # 窗口计算在 wake_patterns sibling,此处仅 streak 记账 + 门控。
        from zf.runtime.wake_patterns import layer2_effective_wake_interval

        effective = layer2_effective_wake_interval(
            interval=interval,
            event_type=event.type,
            streak_type=self._layer2_streak_type,
            streak_count=self._layer2_streak_count,
        )
        if effective > 0 and (now_ts - self._layer2_last_wake_at) < effective:
            self._add_layer2_pending(event)
            self.event_writer.append(ZfEvent(
                type="orchestrator.dispatch_skipped",
                actor="zf-cli",
                payload={
                    "trigger_event_id": event.id,
                    "reason": (
                        "same_trigger_backoff"
                        if effective > interval else "wake_coalesced"
                    ),
                    "remaining_s": round(
                        effective - (now_ts - self._layer2_last_wake_at), 1
                    ),
                },
            ))
            return
        # Committed to wake: fire one turn covering this event + any pending
        # (the burst's accumulated triggers). Fresh briefing rebuilds full
        # state from disk, so the multi-trigger turn loses nothing.
        if event.type == self._layer2_streak_type:
            self._layer2_streak_count += 1
        else:
            self._layer2_streak_type = event.type
            self._layer2_streak_count = 1
        self._layer2_last_wake_at = now_ts
        triggers = self._drain_layer2_pending(also=event)
        self._fire_orchestrator_turn(orch_role, triggers)

    # -- Layer 2 batch coalescing helpers (doc 66 §14) --

    def _pending_key(self, event: ZfEvent) -> tuple:
        """Dedup identity (§14.3): task events by (type, task_id), keep latest;
        task-less events by event id (no dedup — each is distinct)."""
        if event.task_id:
            return ("t", event.type, event.task_id)
        return ("e", event.id)

    def _add_layer2_pending(self, event: ZfEvent) -> None:
        key = self._pending_key(event)
        for i, existing in enumerate(self._layer2_pending):
            if self._pending_key(existing) == key:
                self._layer2_pending[i] = event  # latest wins
                return
        self._layer2_pending.append(event)

    def _drain_layer2_pending(self, *, also: ZfEvent | None = None) -> list[ZfEvent]:
        """Return pending triggers (in arrival order) + `also`, deduped, then clear."""
        drained = list(self._layer2_pending)
        self._layer2_pending = []
        if also is not None:
            key = self._pending_key(also)
            drained = [e for e in drained if self._pending_key(e) != key]
            drained.append(also)
        return drained

    def _fire_orchestrator_turn(
        self, orch_role: RoleConfig, triggers: list[ZfEvent]
    ) -> None:
        """Send one Layer 2 turn covering `triggers` (most recent is primary)."""
        if not triggers:
            return
        primary = triggers[-1]
        # Regenerate progress.md so Layer 2 sees fresh narrative state.
        try:
            regenerate_progress(self.state_dir)
        except Exception:
            pass  # progress.md is optional; never block dispatch on it
        also_triggered = [e.type for e in triggers[:-1]]
        briefing = build_orchestrator_briefing(
            state_dir=self.state_dir,
            config=self.config,
            trigger_event=primary,
            also_triggered=also_triggered,
        )
        briefings_dir = self.state_dir / "briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        ts_safe = primary.ts.replace(":", "-").replace(".", "-")
        evid_short = primary.id.replace("evt-", "")[:12]
        briefing_path = briefings_dir / f"orchestrator-{ts_safe}-{evid_short}.md"
        briefing_path.write_text(briefing, encoding="utf-8")
        extra = f" (+{len(also_triggered)} coalesced)" if also_triggered else ""
        prompt = (
            f"You are the orchestrator agent. New events have fired{extra} and you "
            f"need to decide what to do next.\n"
            f"Read the briefing at {briefing_path} for the full system state and trigger details. "
            f"Make ONE round of decisions via tool calls, then exit."
        )
        try:
            self._record_skill_provenance(role=orch_role, task_id=primary.task_id)
            context = self._dispatch_context(
                role=orch_role,
                briefing_path=briefing_path,
                task_id=primary.task_id,
                trace_id=primary.correlation_id,
            )
            self._send_transport_task("orchestrator", briefing_path, prompt, context)
        except Exception as exc:
            payload = {
                "trigger_event_id": primary.id,
                "error": str(exc),
            }
            payload.update(transport_error_diagnostics(exc))
            self.event_writer.append(ZfEvent(
                type="orchestrator.dispatch_failed",
                actor="zf-cli",
                payload=payload,
            ))
            if str(payload.get("dead_reason") or "") == "pane_dead":
                self.event_writer.append(ZfEvent(
                    type="worker.respawn.requested",
                    actor="zf-cli",
                    payload={
                        "role": "orchestrator",
                        "instance_id": "orchestrator",
                        "trigger_event_id": primary.id,
                        "reason": "pane_dead_dispatch_failed",
                        "source_event_type": "orchestrator.dispatch_failed",
                        "source": "dispatch_failure_recovery",
                    },
                    causation_id=primary.id,
                    correlation_id=primary.correlation_id,
                ))
                self.event_writer.append(ZfEvent(
                    type="orchestrator.dispatch.retry_requested",
                    actor="zf-cli",
                    payload={
                        "role": "orchestrator",
                        "assignee": "orchestrator",
                        "trigger_event_id": primary.id,
                        "reason": "retry_after_pane_respawn",
                        "source": "dispatch_failure_recovery",
                        "max_attempts": 1,
                    },
                    causation_id=primary.id,
                    correlation_id=primary.correlation_id,
                ))

    def _flush_layer2_batch(self) -> None:
        """End-of-batch flush: fire ONE turn covering everything accumulated
        during this run_once reaction (doc 66 §14.0). Cross-batch timer gating
        is re-applied by _notify_orchestrator_agent."""
        self._layer2_in_batch = False
        if not self._layer2_pending:
            return
        rep = self._layer2_pending.pop()
        self._notify_orchestrator_agent(rep)

    def _flush_pending_layer2_wake(self) -> None:
        """Flush a timer-coalesced burst once wake_min_interval_s has elapsed.

        Called at the top of run_once, so a new event or the ~5s idle tick
        drains pending. Guarantees a suppressed trigger is delayed by at most
        wake_min_interval_s, never dropped.
        """
        if not self._layer2_pending:
            return
        interval = self._layer2_wake_min_interval_s
        if interval > 0 and (time.time() - self._layer2_last_wake_at) < interval:
            return  # still inside the window; wait for the next run_once
        rep = self._layer2_pending.pop()
        self._notify_orchestrator_agent(rep)
