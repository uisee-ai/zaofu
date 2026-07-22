"""Canonical set of event types emitted by zaofu kernel + worker contract.

Used by the config loader (E sprint) to warn when a role's `triggers`
list references an event name that no kernel module emits AND no role
in the same yaml `publishes`. Catches typos like
``triggers: [task.assigend]`` (silent — role never wakes) at config
load time instead of after a 30-minute hang.

The list is intentionally broad — adding a new event type requires
either appending here OR declaring it in some role's `publishes` (which
the loader also treats as known for trigger validation).
"""

from __future__ import annotations


KNOWN_EVENT_TYPES: frozenset[str] = frozenset({
    # Session / loop lifecycle (kernel)
    "session.started",
    # Deterministic regression eval case (design 101 §8 C/E)
    "regression.case.captured",
    "regression.case.replayed",
    "loop.started", "loop.stopped",
    "loop.shutdown_requested", "loop.pause_requested", "loop.resume_requested",
    "run.teardown",
    "runtime.maintenance.entered", "runtime.maintenance.exited",
    "runtime.snapshot.recorded", "runtime.snapshot.superseded",
    "runtime.snapshot.rehydrated", "runtime.snapshot.invalid",
    "runtime.liveness.stale",
    "runtime.attention.needed", "runtime.attention.acknowledged",
    "runtime.attention.snoozed", "runtime.attention.resolved",
    "runtime.attention.escalated", "runtime.attention.unacknowledged",
    "runtime.attention.feedback.recorded",
    "approval.requested", "approval.resolved", "approval.expired",
    "approval.rejected_by_policy",
    "supervisor.decision.recorded",
    "owner.visible_message.requested",
    "owner.visible_message.delivery_attempted",
    "owner.visible_message.delivered", "owner.visible_message.failed",
    "owner.visible_message.route_unhealthy",
    "owner.visible_message.expired", "owner.visible_message.superseded",
    "owner.visible_message.suppressed",
    "dispatch.paused", "dispatch.resumed",
    # doc 79 remediation cascade (no-dead-end): cascade decision + safe-halt floor
    "remediation.cascade", "runtime.safe_halted", "runtime.resumed",
    # doc 80 Remediation State Machine transitions (interpreter)
    "remediation.classified", "remediation.routed", "remediation.consumed",
    "repair.action.requested", "repair.action.applied", "repair.action.rejected",
    "loop.action.requested", "loop.action.mapped", "loop.action.rejected",
    "loop.verify.requested", "loop.verify.completed",
    "loop.learning.materialized",
    "loop.learning.promotion.requested", "loop.learning.promotion.materialized",
    "loop.learning.promotion.rejected",
    "projection.rebuild.requested",
    # doc 80 rev1 review N1 — natural-completion terminals (Tier1 recover / Tier3 ack)
    "remediation.recovered", "remediation.escalated_acked",
    # doc 80 rev1 review N10 — incomplete-SM threshold observation (bypass signal)
    "remediation.sm_stuck_observed",
    # User input
    "user.message", "user.intent.submitted",
    # Observability for orphan user.message: emitted by orchestrator
    # _scan_inline_overrides when a human-actor user.message produced no
    # inline-override match AND no downstream task creation, so the
    # operator can see the message was received but not routed.
    "user.message.unrouted",
    # Task lifecycle (kernel + zf kanban CLI)
    "task.created", "task.updated", "task.archived",
    "task.assigned", "task.dispatched", "task.status_changed",
    "task.cancel_requested", "task.retry_requested", "task.files_touched",
    "task.contract.update", "task.evidence_linked",
    "task.contract.invalid", "task.rework.requested", "task.rework.blocked",
    "impl.rework.requested",
    "task.complexity.overridden",
    "task.rework.triage.requested", "task.rework.triage.completed",
    "task.rework.triage.blocked", "task.evidence.reissue.requested",
    "task.done.evidence", "task.done.blocked", "task.requeued",
    "task.requeue.skipped", "task.requeue.recovered",
    "task.ref.repair.requested",
    "task.split_quality.blocked",
    "completion_audit.started", "completion_audit.routed",
    "task.continuation_scheduled", "task.retry_scheduled",
    "task.reset_requested", "task.escalated", "task.integration_enqueued",
    "task.done.accepted", "task.retry.stale_ignored",
    "task.attempt.started", "task.attempt.heartbeat",
    "task.attempt.succeeded", "task.attempt.failed",
    "task.attempt.retry_scheduled", "task.attempt.deadlettered",
    "task.source.published", "task.doc.published", "task.doc.updated",
    "task.doc.ingest.rejected",
    "task.progress.projected", "task.progress.note",
    "task.doc.validation_failed", "task.doc.stale_rejected",
    "task.dispatch_context.bound", "task.completion.stale_rejected",
    "task.graph.updated",
    "dispatch.notification.raised", "dispatch.blocked", "dispatch.unblocked",
    "dispatch.skills_unmatched",
    "dispatch.pull.requested", "runtime.loop.stale",
    "workflow.profile.selected", "workflow.stage.required",
    "workflow.stage.skipped", "workflow.stage.policy_violation",
    "workflow.stage.output.accepted", "workflow.stage.output.missing",
    "workflow.stage.criteria.passed", "workflow.stage.criteria.failed",
    "workflow.stage.retry_scheduled", "workflow.stage.suspended",
    "config.render_lock.drift_detected",
    "product_delivery.task_map.accepted", "product_delivery.task_map.rejected",
    "verify.parity_scan.requested",
    "module.parity.scan.completed",
    "module.parity.scan.failed",
    # Legacy aliases accepted for historical Cangjie/Hermes refactor runs.
    "cangjie.module.parity.scan.completed",
    "cangjie.module.parity.scan.failed",
    "module.parity.closed",
    "module.parity.blocked",
    "gap_plan.ready",
    "task_map.amend.requested",
    "task_map.amended",
    "task_map.amend.failed",
    "task_map.admitted",
    "flow.discovery.requested",
    "flow.discovery.completed",
    "flow.discovery.failed",
    "flow.gap_plan.ready",
    "flow.goal.closed",
    "flow.goal.blocked",
    "goal.rescan.requested",
    "goal.rescan.completed",
    "goal.rescan.failed",
    "goal.gap.detected",
    "goal.gap_plan.ready",
    "goal.closure.closed",
    "goal.closure.blocked",
    "issue.triage.failed",
    "issue.triage.child.completed",
    "issue.triage.child.failed",
    "issue.plan.failed",
    "prd.ready",
    "prd.approved",
    "prd.blocked",
    "prd.child.completed",
    "prd.child.failed",
    "prd.critic.completed",
    "prd.critic.failed",
    "prd.scan.completed",
    "prd.scan.failed",
    "prd.scan.child.completed",
    "prd.scan.child.failed",
    "prd.plan.failed",
    "prd.plan.child.completed",
    "prd.plan.child.failed",
    "task_map.blocked",
    "task_map.child.completed",
    "task_map.child.failed",
    "plan.insight.discovered",
    "research.probe.requested", "research.probe.completed",
    "reflection.recorded", "replan.proposal.created",
    "replan.contract_eval.requested", "replan.contract_eval.completed",
    "replan.contract_eval.adoption_blocked",
    "replan.adoption.prepared", "replan.adoption.completed",
    "replan.adoption.stale_rejected",
    "replan.adoption.awaiting_owner", "replan.adoption.owner_rejected",
    "replan.adoption.redrive_failed",
    "replan.owner_decision.approved", "replan.owner_decision.deferred",
    "replan.owner_decision.rejected",
    "delivery.phase.started",
    "delivery.phase.evaluated",
    "delivery.phase.completed",
    "behavior.source_coverage_gap.detected",
    "eval.contract_completeness.completed",
    "eval.evidence_sufficiency.completed",
    "product_delivery.wave.ready", "product_delivery.spine.started",
    "kanban.agent.turn.created", "kanban.agent.turn.started",
    "kanban.agent.turn.delta", "kanban.agent.turn.completed",
    "kanban.agent.turn.failed", "kanban.agent.message.delta",
    "kanban.agent.message.completed", "kanban.agent.action.proposed",
    "kanban.agent.reply", "operator.session.requested",
    "operator.session.started", "operator.session.failed",
    "operator.session.stopped", "operator.input.submitted",
    "operator.input.failed", "operator.action.completed",
    "operator.action.failed", "operator.action.proposed",
    "operator.intent.created", "operator.intent.approved",
    "operator.intent.rejected",
    "assignment.intent.proposed",
    # Feature lifecycle (zf feature CLI)
    "feature.created", "feature.deleted", "feature.status_changed",
    "feature.decomposed", "feature.liveness.blocked",
    # Worker contract — emitted by role agents via `zf emit`
    "arch.proposal.done", "arch.clarification.needed",
    "clarification.needed",
    "dev.build.done", "dev.blocked", "dev.failed",
    "review.approved", "review.rejected", "design.critique.done",
    "verify.passed", "verify.failed",
    "test.passed", "test.failed",
    "judge.passed", "judge.failed",
    # P3/K5 (docs/impl/22): static_gate as independent DAG stage
    # between ⑤ implement and ⑦ code_review. Emitted by kernel on
    # dev.build.done when workflow.dag.enabled=true.
    "static_gate.passed", "static_gate.failed", "static_gate.skipped",
    # Worker observability (kernel)
    "worker.state.changed", "worker.stuck", "worker.stuck.recovered",
    "worker.stuck.recovery_failed", "worker.spawn_warning",
    "worker.completed", "worker.runtime_write.rejected",
    "worker.scope_write.rejected",
    "worker.checkpointed", "worker.launch_artifact.written",
    "worker.policy.applied",
    "worker.refresh.triggered", "worker.recycled", "worker.recycling",
    "worker.recycle.failed", "worker.respawned", "worker.respawn.failed",
    # I41 evidence (doc 87 §6 rev4): pane probe observation, control-inert.
    "worker.pane.dead_observed",
    "worker.respawn.circuit_opened",
    "worker.restarted",
    "worker.context.warning", "worker.context.critical",
    "worker.context.compact.requested", "worker.context.compacted",
    "worker.context.compact.failed",
    "worker.recovery.insufficient", "worker.recovery.blocked",
    "worker.recovery.skipped", "worker.recovery.injected",
    "worker.runner.failed",
    "recovery.contract.rehydrate.requested", "recovery.contract.rehydrated",
    # ZF-PWF-PRECOMPACT-001 (doc 41 §4.4): Claude Code PreCompact
    # hook → worker.context.precompact (observed) →
    # worker.context.snapshot_requested (kernel-internal trigger).
    "worker.context.precompact",
    "worker.context.snapshot_requested",
    "worker.drift.detected",
    # Operator reliability recovery catalog (read-only projection consumers;
    # emission is reserved for deterministic recovery runners / CLI actions).
    "recovery.run.started", "recovery.step.started",
    "recovery.step.completed", "recovery.step.failed",
    "recovery.run.completed",
    # Per backlog 2026-05-14-1549: pre-spawn purge of stale claude session
    # references in ~/.claude.json (the actual "Session ID already in use" lock)
    "worker.spawn.stale_session_purged",
    "worker.reply.requested", "worker.reply.sent", "worker.reply.failed",
    "worker.respawn.requested", "worker.respawn.started",
    "worker.respawn.completed", "worker.respawn.deferred",
    "worker.drain.requested", "worker.drain.failed",
    "role.instance.allocated", "role.instance.draining",
    "autoscale.evaluated", "autoscale.scale_up.requested",
    "autoscale.scale_up.completed", "autoscale.scale_up.failed",
    "autoscale.scale_down.completed", "autoscale.scale_down.blocked",
    # Run lifecycle / archive projection
    "run.started", "run.heartbeat", "run.stalled", "run.cancelled",
    "run.goal.rescan.granted",
    "run.completed", "run.archived", "run.abandoned",
    # Runtime skill projection
    "skills.materialized",
    # Agent-skills artifact manifest projection
    "artifact.manifest.published", "artifact.manifest.rejected",
    "artifact.manifest.blocked",
    "artifact.promote.completed", "artifact.promote.blocked",
    "task.artifact_refs.updated",
    # Workdir/git isolation projections
    "workdir.prepared", "workdir.prepare_failed",
    "workdir.writer_synced", "workdir.dependency_apply.failed",
    "workdir.retired", "workdir.retire_failed",
    # Worker action queue projection terminal failure
    "worker.action.permanently_failed",
    # Dispatch backoff: too many dispatch_failed in window triggered cooldown
    "orchestrator.dispatch_cooldown",
    # Worker respawn backoff (mirror of dispatch backoff) per backlog 2026-05-14-1439
    "worker.respawn.cooldown",
    "remediation.superseded_by_success",
    # Evidence reissue cap exhausted (mirror) per backlog 2026-05-14-1440
    "task.evidence.reissue.exhausted",
    # Spec markdown → kanban bridge (zf spec)
    "spec.extract.completed",
    # P2 #2 (backlog 2026-05-14): suggested when arch.proposal.done
    # references a spec md with frontmatter — operator/automation can
    # then run `zf spec ingest`.
    "spec.ingest.suggested",
    "task.ref.updated", "task.ref.rejected",
    "reader.checkout_failed", "reader.checkout_skipped",
    "reader.write_violation",
    "candidate.started", "candidate.integration.started",
    "candidate.task_ref.applied", "candidate.integration.completed",
    "candidate.updated", "candidate.conflict", "candidate.stale",
    "candidate.quality.started", "candidate.quality.passed",
    "candidate.quality.failed", "candidate.mechanical_fix.applied",
    "candidate.worktree.stale",
    "candidate.ready", "integration.failed",
    "candidate.integration.duplicate_suppressed",
    "candidate.rework.capped",
    "integration.queue.integrating", "integration.queue.integrated",
    "integration.queue.needs_review", "integration.queue.retry_requested",
    "integration.queue.discarded", "integration.arbiter.decision",
    # TR-DISPATCH-SILENT-STALL-001 (2026-05-21): kernel reactor watchdog
    # for task.assigned without matching task.dispatched within threshold
    # (#G site 6, cangjie Round 1 evidence).
    "dispatch.silent_stall",
    "dispatch.terminal.recorded", "dispatch.terminal.replayed",
    "dispatch.terminal.rejected", "provider.stop.recovery",
    "provider.health.changed", "provider.cooldown.started",
    "provider.fallback.selected", "provider.account.exhausted",
    "ship.lock_acquired", "ship.lock_released", "ship.started",
    "ship.blocked", "ship.conflict", "ship.completed", "ship.done", "ship.failed",
    "web.action.requested", "web.action.completed", "web.action.failed",
    "feishu.command.enveloped", "feishu.message.received", "feishu.message.sent",
    "feishu.document.synced", "feishu.bitable.synced",
    "feishu.action.completed", "feishu.action.failed",
    "feishu.approval.requested", "feishu.approval.approved",
    "feishu.approval.denied", "feishu.approval.expired",
    "feishu.approval.superseded",
    "feishu.inbound_bridge.started", "feishu.inbound_bridge.skipped",
    "feishu.inbound_bridge.failed", "feishu.inbound_bridge.stopped",
    "feishu.inbound.sender_blocked",
    "identity.binding.requested",
    "bridge.message.send.requested", "bridge.message.delivered",
    "bridge.message.failed", "bridge.inbound.received",
    "bridge.inbound.rejected", "bridge.loop.skipped",
    "openclaw_feishu_bridge.action.completed",
    "openclaw_feishu_bridge.action.failed",
    "runtime.action.accepted", "runtime.action.rejected",
    "runtime.action.completed", "runtime.action.failed",
    "runtime.action.attempt.started", "runtime.action.attempt.completed",
    "runtime.action.attempt.failed",
    "run.manager.action.effect.pending",
    "run.manager.action.effect.passed",
    "run.manager.action.effect.failed",
    "autopilot.proposal.created",
    "automation.run.started", "automation.run.completed",
    "automation.run.failed", "automation.run.skipped",
    "automation.alert.raised", "automation.alert.resolved",
    "automation.proposal.created", "automation.proposal.accepted",
    "automation.proposal.rejected", "automation.proposal.applied",
    "automation.proposal.failed",
    "spine_review.artifact.created", "spine_review.proposal.created",
    "agent.session.run.started", "agent.session.run.completed",
    "agent.session.run.failed", "agent.session.run.cancelled",
    "agent.session.part.started", "agent.session.part.delta",
    "agent.session.part.completed", "agent.session.part.failed",
    "provider.permission.snapshot.recorded",
    "provider.permission.snapshot.drift",
    "channel.created", "channel.archived",
    "channel.member.added", "channel.member.add.rejected",
    "channel.member.connected", "channel.member.invited",
    "channel.member.removed", "channel.member.resumed",
    "channel.member.suspended",
    "channel.member.permissions.updated", "channel.member.permission_profile.audit",
    "channel.member.visibility.updated",
    "channel.mention.detected", "channel.route.defaulted",
    "channel.spine_review.requested",
    "channel.agent.reply.requested", "channel.agent.reply.started",
    "channel.agent.reply.completed", "channel.agent.reply.failed",
    "channel.agent.reply.dispatch_failed",
    "channel.agent.reply.remediation.exhausted",
    "kanban.agent.proposal.resolved",
    "channel.typing.started", "channel.typing.stopped",
    "channel.message.stream.started", "channel.message.stream.delta",
    "channel.message.stream.ended",
    "channel.attachment.uploaded", "channel.artifact.proposed",
    "channel.artifact.attached", "channel.artifact.rejected",
    "channel.context_pack.built", "channel.context_pack.rejected",
    "channel.handoff.requested", "channel.handoff.accepted",
    "channel.handoff.rejected", "channel.state_update.posted",
    "channel.discussion.mode.set", "channel.discussion.started",
    "channel.discussion.phase.changed", "channel.discussion.closed",
    "channel.discussion.participant.missed",
    "channel.relay.routed", "channel.relay.suppressed",
    "channel.question.opened", "channel.question.resolved",
    "channel.question.resolve.rejected", "channel.question.merged",
    "channel.questions.frozen", "channel.consensus.proposed",
    "channel.consensus.signed", "channel.consensus.blocked",
    "channel.consensus.reached",
    "channel.message.posted", "channel.message.delivered",
    "channel.message.failed", "channel.message.read",
    "channel.history.cleared",
    "channel.finding.recorded", "channel.summary.updated",
    "channel.synthesis.requested", "channel.synthesis.proposed",
    "channel.owner_report.requested", "channel.owner_report.generated",
    "channel.owner_report.delivered", "channel.automation_report.ingested",
    "workflow.intake.created", "workflow.intake.clarification.required",
    "workflow.intake.ready", "workflow.request.updated",
    "workflow.request.proposed", "workflow.request.approved",
    "workflow.request.submitted", "workflow.request.running",
    "workflow.submit.requested", "workflow.submit.accepted",
    "workflow.submit.rejected",
    "workflow.invoke.requested", "workflow.invoke.accepted",
    "workflow.reconcile.requested",
    "workflow.invoke.rejected",
    "workflow.adjust.requested", "workflow.adjust.accepted",
    "workflow.adjust.rejected",
    "workflow.config.change.proposed",
    "workflow.config.validation.requested",
    "workflow.config.change.apply.requested",
    "workflow.child.completed", "workflow.child.failed",
    "review.child.completed", "review.child.failed",
    "verify.child.completed", "verify.child.failed",
    "workflow.resume.checkpoint", "workflow.resume.planned",
    "workflow.resume.applied", "workflow.resume.rejected",
    "stage.transition.stalled",
    "provider.dev_chat.start.requested",
    "provider.dev_chat.message.requested",
    "provider.dev_chat.stop.requested",
    "runtime.stop.proposed", "runtime.stop.requested",
    "runtime.restart.proposed", "runtime.restart.requested",
    "runtime.resume.proposed", "runtime.resume.requested",
    "task.fanout.requested", "task.fanout.rejected",
    "fanout.requested",
    "fanout.started", "fanout.child.queued", "fanout.child.dispatched",
    "fanout.child.dispatch_deferred",
    "fanout.child.completed", "fanout.child.failed",
    "fanout.child.stale_completion",
    "fanout.child.completion_adopted",
    "fanout.child.workdir_mismatch",
    "fanout.retrigger.suppressed",
    "run.goal.started", "run.goal.updated",
    "run.goal.completed", "run.goal.blocked",
    "run.goal.completion.claimed", "run.goal.completion.blocked",
    "run.goal.completion.rejected",
    "run.delivery.requested", "run.delivery.settled",
    "run.delivery.failed", "run.delivery.blocked",
    "goal.claim_set.pinned", "goal.claim_set.pin.failed",
    "goal.closure.identity.invalid", "goal.closure.superseded",
    "goal.closure.synthesized", "goal.closure.synthesis.failed",
    "goal.closure.rejected", "goal.closure.blocked",
    "goal.gap.recovery.requested", "goal.closure.compat.projected",
    "run.goal.quiescent.entered", "run.goal.quiescent.exited",
    "rework.feedback.published", "rework.feedback.acknowledged",
    "rework.feedback.resolution_claimed", "rework.feedback.verified_closed",
    "rework.feedback.residual", "rework.feedback.rerouted",
    "stage.report.evidence_missing",
    "task.rework.continuation_injected",
    "diagnosis.requested", "diagnosis.completed", "diagnosis.failed",
    "fanout.slot.assigned", "fanout.slot.released",
    "fanout.assignment.override", "fanout.aggregate.started",
    "fanout.aggregate.completed", "fanout.synth.dispatched",
    "fanout.synth.completed", "fanout.timed_out", "fanout.cancelled",
    "zaofu.refactor.review.requested", "zaofu.refactor.review.ready",
    "zaofu.refactor.review.blocked", "zaofu.refactor.plan.requested",
    "zaofu.refactor.plan.ready", "zaofu.refactor.plan.blocked",
    "zaofu.refactor.scan.blocked",
    # α-1 (2026-05-17): emitted when _check_fanout_independence detects
    # file overlap between proposed fanout children; signals that the
    # affected tasks must be dispatched serially via backlog scheduler
    # instead of in parallel.
    "fanout.serialize",
    # α-2 (2026-05-17): worker.heartbeat — per-worker liveness signal
    # emitted every ~60s by every active worker pane. Payload:
    # {instance_id, current_task_id, state, last_action_ts,
    #  context_used_ratio?, checkpoint_ref?}. Consumed by housekeeping
    # (writes last_heartbeat_at into role_sessions.yaml) and by α-3
    # EventWatcher sweep (proactive dispatch + true-stuck detection).
    "worker.heartbeat",
    "worker.progress", "phase.progressed", "phase.regression.ignored",
    "phase.regression.blocked",
    # α-3 (2026-05-17): worker.probe.silent — periodic sweep flags a
    # busy worker that hasn't heartbeated in >silent_threshold seconds.
    # Informational; escalates to worker.stuck after stuck_threshold.
    "worker.probe.silent",
    # α-3+ (2026-05-17): worker.probe.idle — periodic sweep noticed an
    # idle worker WHILE backlog has ready tasks. Wakes run_once so the
    # existing dispatch path tries to assign. Suppressed when backlog
    # is empty (avoid wake spam).
    "worker.probe.idle",
    # β-1 (2026-05-17): zaofu.bug.detected — periodic scan over recent
    # events detected a known-zaofu-failure signature (ship_block_loop /
    # respawn_failure_cascade / judge_failure_loop / etc.). Operator
    # playbook (β-2) + zf bug-fix-cycle CLI (β-3) consume this to drive
    # an off-line fix cycle.
    "zaofu.bug.detected",
    # β-4 (2026-05-17): task.fix_spawned — emitted when the fix-task path
    # creates a fix-task (instead of requeuing the original) on a local-
    # CRITICAL multi-task failure. Payload links parent_task_id +
    # fix_task_id + trigger_event_id.
    "task.fix_spawned",
    # ω-1.a (2026-05-18): kernel fast-forwarded task ref onto main HEAD
    # before role dispatch. Closes audit Class A1 (state sync gap that
    # caused r-next-10 baseline-drift reject loop). Payload:
    # BaselineSyncResult.to_payload() + source="pre_dispatch".
    "task.baseline_synced",
    # ω-1.a (2026-05-18): kernel observed task branch has commits not
    # on main → refused to rewrite (safety). Operator or LLM
    # orchestrator must resolve (e.g. force-merge, manual rebase).
    "task.baseline_diverged",
    # Agent (stream-json transport)
    "agent.thinking", "agent.text", "agent.tool.use", "agent.tool.result",
    "agent.usage", "agent.api_blocked", "agent.timeout",
    # Autoresearch trigger decisions and supervisor diagnostic injection.
    "autoresearch.trigger.accepted", "autoresearch.trigger.skipped",
    "autoresearch.invocation.requested",
    "autoresearch.invocation.accepted",
    "autoresearch.invocation.rejected",
    "autoresearch.loop.requested",
    "autoresearch.loop.accepted",
    "autoresearch.loop.skipped",
    "autoresearch.loop.started",
    "autoresearch.loop.completed",
    "autoresearch.loop.failed",
    "autoresearch.resident_sidecar.started",
    "autoresearch.resident_sidecar.failed",
    "autoresearch.resident_sidecar.stopped",
    "autoresearch.review_gate.requested",
    "autoresearch.review_gate.accepted",
    "autoresearch.review_gate.started",
    "autoresearch.review_gate.completed",
    "autoresearch.review_gate.failed",
    "autoresearch.review_gate.skipped",
    "autoresearch.bug_candidate.created",
    "autoresearch.bug_candidate.confirmed",
    "autoresearch.bug_candidate.dismissed",
    "autoresearch.bug_candidate.superseded",
    "autoresearch.validation.passed", "autoresearch.validation.failed",
    "autoresearch.inject.worker_stuck",
    # Orchestrator
    "orchestrator.dispatch_failed", "orchestrator.dispatch_skipped",
    "orchestrator.dispatch.retry_requested",
    "orchestrator.round.complete",  # Run 6: tmux orchestrator round-end signal
    "orchestrator.idle", "orchestrator.evidence_rework.requested",
    "orchestrator.rework.triage.requested",
    "orchestrator.rework.triage.recorded",
    # r-next backlog B-2: surface watcher tick failures instead of swallowing.
    "orchestrator.tick.failed",
    # doc 78 W2: candidate-rework sweep asks the orchestrator to re-decompose
    # the task_map on a plan-level candidate failure (vs re-implement).
    "orchestrator.replan_requested",
    # doc 78 O-2 (safe half): a harness self-repair is prepared for human
    # review + manual apply (NEVER auto-applied to the kernel).
    "autoresearch.repair.prepared",
    # backlog 0820 block B: AUTHORIZED auto-repair — dispatch a zf-self-repair
    # agent for this candidate (opt-in ZF_AUTORESEARCH_AUTO_REPAIR, bounded cap).
    "autoresearch.repair.dispatch_requested",
    # backlog 0820 block B consumer: zf self-repair prepared an isolated zaofu
    # worktree + briefing and (optionally) spawned the repair agent.
    "autoresearch.repair.dispatched",
    # Consumer-level failure to prepare/spawn a bounded self-repair worker.
    "autoresearch.repair.dispatch_blocked",
    "autoresearch.repair.escalation.requested",
    # closeout bridge: isolated self-repair branch has commits and needs an
    # operator merge/restart decision; never auto-merged by the tick.
    "autoresearch.repair.closeout.required",
    # Run Manager: run-level recovery owner projections and controlled actions.
    "run.manager.tick.started",
    "run.manager.tick.completed",
    "run.manager.tick.failed",
    "run.manager.transition",
    "run.manager.action.planned",
    "run.manager.action.applied",
    "run.manager.action.blocked",
    "run.manager.action.failed",
    "run.manager.action.verify.passed",
    "run.manager.action.verify.failed",
    "run.manager.action.no_progress_break",
    "run.manager.repair.accepted",
    "run.manager.repair.rejected",
    "run.manager.repair.blocked",
    "run.manager.repair.merge.queued",
    "run.manager.repair.merge.merging",
    "run.manager.repair.merge.merged",
    "run.manager.repair.merge.needs_review",
    "run.manager.repair.merge.discarded",
    "run.manager.autoresearch.requested",
    "run.manager.autoresearch.consumed",
    "run.manager.reflect.requested",
    "run.manager.reflect.completed",
    "run.manager.inbound.received",
    "run.manager.context.resolved",
    "run.manager.explanation.requested",
    "run.manager.explanation.generated",
    "run.manager.inbound.handoff.requested",
    "run.manager.human_decision.applied",
    "run.manager.human_decision.rejected",
    "run.manager.resident.spawned",
    "run.manager.resident.prompted",
    "run.manager.resident.preserved",
    "run.manager.resident.rebound",
    "run.manager.resident.restart_requested",
    "run.manager.resident.restarted",
    "run.manager.resident.restart_failed",
    "run.manager.unhealthy",
    "run.manager.source_repair.dispatch_requested",
    "run.manager.agent.observation",
    "run.manager.agent.recommendation",
    "run.manager.agent.recommendation.consumed",
    "workflow.resume.control_action.result",
    # Durable agent call results and replayable workflow operations (doc 140).
    "workflow.call.result.reported",
    "workflow.call.result.repair.requested",
    "workflow.call.result.admitted",
    "workflow.call.result.invalid",
    "workflow.operation.requested",
    "workflow.operation.started",
    "workflow.operation.settled",
    "workflow.operation.failed",
    "workflow.operation.blocked",
    "human.escalation.sent",
    "human.escalation.failed",
    "human.escalation.acknowledged",
    # Discriminator + GAN
    "discriminator.passed", "discriminator.failed",
    "gan.round.started", "gan.round.completed",
    # Cost / scope / safety
    "cost.budget.exceeded", "scope.violation",
    "cost.usage.capture_miss",       # B-COST-02: claude session file not found
    # LH-0: Layer 1 enforcement gates
    "task.rework.capped",            # T1: retry_count > max_rework_attempts
    "task.invalid_transition",       # T2: zf kanban assign/move without prev stage
    "task.orphan_warning",           # T3: dispatched > warning threshold, no progress
    "task.orphaned",                 # T3: dispatched > escalate threshold → back to backlog
    # LH-3: Hook Tri-State + defensive hook chain
    "review.suspended",              # reviewer needs more info (not rejection)
    "test.suspended",                # test env broken / external dep missing
    "hook.write_failed",             # hook_recv could not append to events.jsonl
    "hook.orphan_event",             # hook had no resolvable session_id / task
    # ZF-LH-INLINE-001: operator inline override audit event (doc 26 §3.3)
    "workflow.inline_override",
    # ZF-ORCH-ACT-001 (doc 39 §4.2, doc 40 §6 candidate I52):
    # one summary event per Orchestrator.run_once() wake so the
    # decision ledger captures dispatch / no_action / blocked /
    # escalate / scale / wait outcomes — closing the silent-idle
    # debugging gap.
    "orchestrator.decision.recorded",
    # ZF-PWF-STOP-GUARD-001 (doc 41 §4.5, candidate I64): worker
    # provider Stop hook check — recorded for audit; hook exit code
    # 2 blocks the stop when gates are missing.
    "provider.stop.check",
    # ZF-LH-SPEC-PROMOTE-001 (doc 26 §4.1, doc 40 §6 I56): after
    # judge.passed, kernel records whether the verified behavior was
    # promoted into the canonical spec / ADR or skipped (with reason).
    "spec.promote.completed",
    "spec.promote.skipped",

    # Namespaced hook events (explicit backend origin).
    "claude.hook.pre_tool_use", "claude.hook.post_tool_use", "claude.hook.stop",
    # Codex hook events reserved for LH-3.5 (config + adapter arrive there).
    "codex.hook.session_start", "codex.hook.user_prompt_submit",
    "codex.hook.pre_tool_use", "codex.hook.post_tool_use", "codex.hook.stop",
    # LH-4: error taxonomy — circuit breaker + per-category escalate
    "circuit.tripped",               # breaker refuses dispatch (role, task)
    "circuit.closed",                # breaker reset after success probe
    "role.suspended.circuit",        # per-role breaker opened
    # Human
    "human.escalate", "human.note", "human.resolved",
    # Misc
    "handoff.generated", "event.malformed",
})


def validate_role_event_names(
    roles: list,
) -> list[str]:
    """Return one warning string per suspect trigger reference.

    A trigger is "suspect" if it is neither in KNOWN_EVENT_TYPES nor in
    any role's `publishes` list within the same config. publishes are
    NOT validated — they're how users introduce new event names; the
    loader treats them as user-declared known.
    """
    extended_known = set(KNOWN_EVENT_TYPES)
    for r in roles:
        extended_known.update(getattr(r, "publishes", []) or [])

    warnings: list[str] = []
    for r in roles:
        for trigger in getattr(r, "triggers", []) or []:
            if trigger not in extended_known:
                warnings.append(
                    f"role {r.name!r}: trigger {trigger!r} is not a known event "
                    f"type and is not published by any role — possible typo?"
                )
    return warnings
