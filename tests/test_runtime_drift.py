"""Tests for drift detector."""

from __future__ import annotations

from zf.runtime.drift import DriftDetector


class TestRepeatDecisions:
    def test_no_repeat_when_varied(self):
        events = [{"type": f"event.{i}"} for i in range(10)]
        d = DriftDetector()
        signals = d.check(events)
        assert not any(s.signal == "repeat_decisions" for s in signals)

    def test_detects_repeat(self):
        # B-NEW-5 fix: actor is now required for repeat_decisions to count.
        # Real agent drift comes with an actor (e.g. "dev-1") attribution.
        # Kernel synthesis events with actor=None are intentionally
        # excluded — see test_fanout_contract_synthesis_is_not_agent_drift.
        events = [{"type": "dev.build.done", "actor": "dev-1"}] * 10
        d = DriftDetector(repeat_threshold=3)
        signals = d.check(events)
        assert any(s.signal == "repeat_decisions" for s in signals)

    def test_same_type_from_different_actors_is_not_repeat(self):
        events = [
            {"type": "review.approved", "actor": "review-security"},
            {"type": "review.approved", "actor": "review-architecture"},
            {"type": "review.approved", "actor": "review-testing"},
            {"type": "fanout.child.completed", "actor": "zf-cli"},
            {"type": "agent.usage", "actor": "review-security"},
            {"type": "agent.usage", "actor": "review-architecture"},
        ]
        d = DriftDetector(repeat_threshold=3)
        signals = d.check(events)
        assert not any(s.signal == "repeat_decisions" for s in signals)

    def test_drift_events_do_not_detect_themselves(self):
        events = [{"type": "worker.drift.detected", "actor": "zf-cli"}] * 8
        d = DriftDetector(repeat_threshold=3)
        signals = d.check(events)
        assert not any(s.signal == "repeat_decisions" for s in signals)

    def test_infra_actor_repeats_are_not_agent_drift(self):
        events = [
            {"type": "orchestrator.dispatch_skipped", "actor": "zf-cli"}
        ] * 8
        d = DriftDetector(repeat_threshold=3)
        signals = d.check(events)
        assert not any(s.signal == "repeat_decisions" for s in signals)

    def test_run_manager_ticks_are_not_agent_drift(self):
        # 2026-07-08 E2E: run-manager periodic ticks flooded worker.drift.detected
        # (80-120 per fanout run) because _INFRA_ACTORS only excluded zf-cli.
        # All infra actors emit periodic/loop events by design and must not count.
        for actor, etype in (
            ("run-manager", "run.manager.tick.started"),
            ("zf-supervisor", "runtime.attention.needed"),
            ("zf-autoresearch", "autoresearch.invocation.accepted"),
            ("zf-runtime", "failure.candidates.materialized"),
        ):
            events = [{"type": etype, "actor": actor}] * 10
            signals = DriftDetector(repeat_threshold=3).check(events)
            assert not any(s.signal == "repeat_decisions" for s in signals), (
                f"{actor}/{etype} must not count as agent repeat-decision drift"
            )
        # A genuine worker still triggers drift.
        worker = [{"type": "dev.build.done", "actor": "dev-lane-0"}] * 10
        assert any(
            s.signal == "repeat_decisions"
            for s in DriftDetector(repeat_threshold=3).check(worker)
        )

    def test_worker_state_repeats_are_not_agent_drift(self):
        events = [
            {"type": "worker.state.changed", "actor": "review"}
        ] * 8
        d = DriftDetector(repeat_threshold=3)
        signals = d.check(events)
        assert not any(s.signal == "repeat_decisions" for s in signals)

    def test_fanout_contract_synthesis_is_not_agent_drift(self):
        """B-NEW-5 regression: P0 stage ④ orchestrator-driven backlog
        synthesis writes one task.contract.update per fanout task,
        with actor=None. Pre-fix, 4-way fanout triggered repeat_decisions
        → refresh loop, blocking the entire pipeline. Pin both layers
        of fix: explicit task.contract.update observation filter, AND
        actor=None/empty kernel events never count as agent decisions.
        """
        # Layer A: task.contract.update specifically must never count
        events_a = [
            {"type": "task.contract.update", "actor": None}
        ] * 8
        d = DriftDetector(repeat_threshold=3)
        signals_a = d.check(events_a)
        assert not any(s.signal == "repeat_decisions" for s in signals_a)

        # Layer B: any kernel-synthesis event with actor=None or actor=""
        # is not an agent decision, regardless of event type
        events_b = [
            {"type": "some.future.kernel.event", "actor": None}
        ] * 8
        signals_b = d.check(events_b)
        assert not any(s.signal == "repeat_decisions" for s in signals_b)

        events_c = [
            {"type": "some.future.kernel.event", "actor": ""}
        ] * 8
        signals_c = d.check(events_c)
        assert not any(s.signal == "repeat_decisions" for s in signals_c)

    def test_real_agent_with_explicit_actor_still_detected(self):
        """Defense check for the B-NEW-5 fix: filtering actor=None must
        NOT mask real agent drift. An actor='dev-1' repeating dev.build.done
        8 times still triggers refresh."""
        events = [{"type": "dev.build.done", "actor": "dev-1"}] * 8
        d = DriftDetector(repeat_threshold=3)
        signals = d.check(events)
        assert any(s.signal == "repeat_decisions" for s in signals)

    def test_codex_hook_burst_is_not_repeat_decision_drift(self):
        events = [
            {"type": "codex.hook.pre_tool_use", "actor": "dev-1"},
            {"type": "codex.hook.pre_tool_use", "actor": "dev-1"},
            {"type": "codex.hook.pre_tool_use", "actor": "dev-1"},
            {"type": "codex.hook.post_tool_use", "actor": "dev-1"},
            {"type": "codex.hook.post_tool_use", "actor": "dev-1"},
            {"type": "codex.hook.post_tool_use", "actor": "dev-1"},
            {"type": "agent.usage", "actor": "dev-1"},
        ]
        d = DriftDetector(repeat_threshold=3)
        signals = d.check(events)
        assert not any(s.signal == "repeat_decisions" for s in signals)


class TestThrashing:
    def test_no_thrashing(self):
        events = [{"type": "review.approved", "task_id": "T1"}] * 5
        d = DriftDetector()
        signals = d.check(events)
        assert not any(s.signal == "thrashing" for s in signals)

    def test_detects_thrashing(self):
        events = [{"type": "review.rejected", "task_id": "T1"}] * 4
        d = DriftDetector(thrash_threshold=3)
        signals = d.check(events)
        thrash = [s for s in signals if s.signal == "thrashing"]
        assert len(thrash) >= 1
        assert thrash[0].severity == "high"


class TestNodeSkip:
    def test_no_skip_when_all_active(self):
        events = [
            {"type": "a", "actor": "dev"},
            {"type": "b", "actor": "review"},
        ]
        d = DriftDetector()
        signals = d.check(events, expected_roles=["dev", "review"])
        assert not any(s.signal == "node_skip" for s in signals)

    def test_detects_skip(self):
        # node_skip requires >= 10 events to avoid cold-start false positives
        events = [{"type": "a", "actor": "dev"}] * 12
        d = DriftDetector()
        signals = d.check(events, expected_roles=["dev", "review"])
        assert any(s.signal == "node_skip" for s in signals)

    def test_no_skip_below_min_event_count(self):
        """Cold-start guard: <10 events never produces node_skip."""
        events = [{"type": "a", "actor": "dev"}] * 5
        d = DriftDetector()
        signals = d.check(events, expected_roles=["dev", "review"])
        assert not any(s.signal == "node_skip" for s in signals)

    def test_replica_activity_satisfies_base_role(self):
        events = [
            {"type": "agent.usage", "actor": "test-1"},
            {"type": "task.dispatched", "actor": "orchestrator"},
        ] * 6
        d = DriftDetector()
        signals = d.check(events, expected_roles=["test"])
        assert not any(s.signal == "node_skip" for s in signals)


class TestEscalateAnomaly:
    def test_normal_escalation(self):
        events = [{"type": "task.done"}] * 20
        d = DriftDetector()
        signals = d.check(events)
        assert not any(s.signal == "escalate_anomaly" for s in signals)

    def test_over_escalating(self):
        events = [{"type": "human.escalate"}] * 15 + [{"type": "other"}] * 5
        d = DriftDetector()
        signals = d.check(events)
        assert any(s.signal == "escalate_anomaly" for s in signals)
