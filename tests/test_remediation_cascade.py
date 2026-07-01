"""doc 79 Tier1: remediation cascade — no-dead-end tier transitions.

The R12 collapse: worker_stuck is classified infra/retryable, but when bounded
respawn retries exhaust, the code parks in blocked_human and waits for an
operator who (unattended) never comes → 6h limbo. The cascade closes that:
an exhausted tier transitions to the next, and the floor is safe-halt — never
a silent dead-end.
"""

from __future__ import annotations

from zf.runtime.remediation_cascade import (
    CASCADE_ESCALATE,
    CASCADE_RETRY,
    CASCADE_SAFE_HALT,
    classify_bucket,
    decide_cascade,
)


# --- classify_bucket reuses rework_triage taxonomy, does not reinvent it ----

def test_worker_stuck_is_infra():
    # The R12 class: worker_stuck already lives in rework_triage INFRA bucket.
    assert classify_bucket("worker_stuck") == "infra"


def test_terminal_and_content_buckets():
    assert classify_bucket("api_invalid_request") == "terminal"
    assert classify_bucket("scope_violation") == "content"


def test_unknown_class_is_unknown_bucket():
    assert classify_bucket("something_never_seen") == "unknown"


# --- decide_cascade: the no-dead-end invariant -----------------------------

def test_infra_under_cap_retries():
    d = decide_cascade(failure_class="worker_stuck", attempts=1, cap=3, liveness=True)
    assert d.tier == CASCADE_RETRY


def test_infra_exhausted_with_liveness_escalates():
    d = decide_cascade(failure_class="worker_stuck", attempts=3, cap=3, liveness=True)
    assert d.tier == CASCADE_ESCALATE


def test_infra_exhausted_without_liveness_safe_halts_not_limbo():
    # THE R12 FIX: infra retry exhausted + no human reachable must NOT dead-end
    # in blocked_human limbo — it must safe-halt (the floor).
    d = decide_cascade(failure_class="worker_stuck", attempts=3, cap=3, liveness=False)
    assert d.tier == CASCADE_SAFE_HALT


def test_unknown_class_defaults_to_safe_halt():
    # no-dead-end default: a failure class matching no bucket is fail-safe
    # halted, never silently dropped.
    d = decide_cascade(failure_class="brand_new_failure", attempts=0, cap=3, liveness=True)
    assert d.tier == CASCADE_SAFE_HALT


def test_terminal_without_liveness_safe_halts():
    d = decide_cascade(failure_class="api_invalid_request", attempts=0, cap=3, liveness=False)
    assert d.tier == CASCADE_SAFE_HALT


def test_terminal_with_liveness_escalates():
    d = decide_cascade(failure_class="api_invalid_request", attempts=0, cap=3, liveness=True)
    assert d.tier == CASCADE_ESCALATE


def test_decision_carries_bucket_and_reason():
    d = decide_cascade(failure_class="worker_stuck", attempts=3, cap=3, liveness=False)
    assert d.bucket == "infra"
    assert d.failure_class == "worker_stuck"
    assert d.reason  # non-empty human-readable reason


# --- wire-up: the respawn cap-exhausted path emits the cascade -------------

from types import SimpleNamespace  # noqa: E402

from zf.runtime.orchestrator_lifecycle import LifecycleManagerMixin  # noqa: E402


class _LifecycleHarness(LifecycleManagerMixin):
    """Minimal stand-in exercising the inherited respawn-failure path."""

    def __init__(self, *, channel_live=None):
        self._t = 0.0
        self.events = []
        self.states = {}
        self.event_writer = SimpleNamespace(
            append=lambda e: self.events.append(e) or e
        )
        # events-derived respawn cap (2026-06-11-0325): the harness event
        # log IS the appended events list; a configured owner channel keeps
        # the legacy default liveness=True for cascade tests.
        self.event_log = SimpleNamespace(read_days=lambda d: list(self.events))
        self.config = SimpleNamespace(integrations=SimpleNamespace(
            openclaw_feishu_bridge=SimpleNamespace(enabled=True),
        ))
        if channel_live is not None:
            self._operator_channel_live = channel_live

    def _now(self):
        return self._t

    def _set_worker_state(self, instance_id, state, reason=""):
        self.states[instance_id] = state


def _cascade_events(h):
    return [e for e in h.events if e.type == "remediation.cascade"]


def _respawn_fail(h, instance_id):
    """Mirror production order (_respawn_instance except-branch): the
    worker.respawn.failed event is appended BEFORE _record_respawn_failure,
    so the events-derived count includes the current failure."""
    from zf.core.events.model import ZfEvent as _Ev
    h.event_writer.append(_Ev(
        type="worker.respawn.failed", actor=instance_id, payload={},
    ))
    h._record_respawn_failure(instance_id)


def test_respawn_cap_exhausted_emits_cascade_not_silent_deadend():
    # Drive _record_respawn_failure past the cap; the cap-exhausted branch must
    # emit remediation.cascade (the R12 dead-end replaced by a routed signal).
    h = _LifecycleHarness()  # configured channel → liveness defaults True
    for _ in range(h._RESPAWN_FAILURE_MAX_CONSECUTIVE):
        _respawn_fail(h, "dev-lane-0")
    evs = _cascade_events(h)
    assert len(evs) == 1
    p = evs[0].payload
    assert p["bucket"] == "infra"
    assert p["tier"] == "escalate"  # operator reachable (default) → escalate
    assert p["safe_halt"] is False


def test_recovery_failed_cascade_does_not_retry():
    # doc 79 D fix (R13): worker.stuck.recovery_failed calls _emit_respawn_cascade
    # with attempts=cap. recovery already gave up, so the cascade must NOT decide
    # retry — it escalates (liveness) or safe-halts (no liveness). R13 caught the
    # cap-counter path never firing because failures spread thin across lanes.
    h = _LifecycleHarness()  # liveness default True
    h._emit_respawn_cascade("dev-lane-0", attempts=h._RESPAWN_FAILURE_MAX_CONSECUTIVE)
    evs = _cascade_events(h)
    assert len(evs) == 1
    assert evs[0].payload["tier"] != "retry"  # recovery gave up → never retry
    assert evs[0].payload["tier"] == "escalate"


def test_respawn_cap_exhausted_safe_halts_when_channel_dead():
    # Unattended + dead owner channel → cascade floors to safe-halt, not limbo.
    h = _LifecycleHarness(channel_live=lambda: False)
    for _ in range(h._RESPAWN_FAILURE_MAX_CONSECUTIVE):
        _respawn_fail(h, "dev-lane-0")
    evs = _cascade_events(h)
    assert len(evs) == 1
    assert evs[0].payload["tier"] == "safe_halt"
    assert evs[0].payload["safe_halt"] is True
    # safe-halt must EXECUTE, not just signal: terminal runtime.safe_halted +
    # dispatch.paused (reusing the enforced pause gate) so the run stops.
    types = [e.type for e in h.events]
    assert "runtime.safe_halted" in types
    assert "dispatch.paused" in types
    halted = next(e for e in h.events if e.type == "runtime.safe_halted")
    assert halted.payload["root_failure_class"] == "worker_stuck"
    assert halted.payload["resumable"] is True
    # evidence points at the cascade event (not empty) so the operator can trace
    # why it halted.
    cascade = next(e for e in h.events if e.type == "remediation.cascade")
    assert halted.payload["evidence_event_ids"] == [cascade.id]


# --- 2026-06-10 review P0-1: cap must fire at REAL retry cadence ------------

def test_respawn_cap_fires_with_real_cadence_spacing():
    """The cangjie regression: failures arrive ~330s apart (the watchdog's
    actual retry interval). The old 120s sliding window reset the counter on
    every failure, so 786 respawn.failed produced ZERO cooldown/cascade.
    Consecutive-failure counting must trip the cap regardless of spacing."""
    h = _LifecycleHarness()
    for i in range(h._RESPAWN_FAILURE_MAX_CONSECUTIVE):
        h._t = i * 330.0  # > any old window
        _respawn_fail(h, "dev-3")
    cooldowns = [e for e in h.events if e.type == "worker.respawn.cooldown"]
    assert len(cooldowns) == 1, (
        "cap must fire on consecutive failures even when spaced wider than "
        "any time window"
    )
    assert _cascade_events(h), "cap exhaustion must route through the cascade"
    assert h.states.get("dev-3") == "blocked_human"


def test_respawn_success_resets_consecutive_count():
    from zf.core.events.model import ZfEvent as _Ev
    h = _LifecycleHarness()
    _respawn_fail(h, "dev-3")
    _respawn_fail(h, "dev-3")
    # success path: worker.respawned event is what resets the derived count
    h.event_writer.append(_Ev(type="worker.respawned", actor="dev-3", payload={}))
    h._clear_respawn_failure("dev-3")
    _respawn_fail(h, "dev-3")
    _respawn_fail(h, "dev-3")
    assert not [e for e in h.events if e.type == "worker.respawn.cooldown"]
    assert not _cascade_events(h)


def test_cooldown_active_does_not_time_reset_count():
    h = _LifecycleHarness()
    for i in range(h._RESPAWN_FAILURE_MAX_CONSECUTIVE):
        h._t = i * 330.0
        _respawn_fail(h, "dev-3")
    # Within backoff → cooldown active
    h._t = h._t + 1.0
    assert h._respawn_recent_failure_cooldown_active("dev-3") is True
    # After backoff expiry the cooldown lifts, but the count is NOT
    # time-reset — it is events-derived and only a worker.respawned
    # success resets it.
    h._t = h._t + h._RESPAWN_FAILURE_BACKOFF_SECONDS + 1.0
    assert h._respawn_recent_failure_cooldown_active("dev-3") is False
    assert h._consecutive_respawn_failures("dev-3") == 3


def test_respawn_cap_survives_restart():
    """2026-06-11-0325: 计数 events 派生 → 重启不丢。旧 in-memory registry
    在重启后归零,结构性损坏的 worker 每次重启重新买 3 次失败额度。"""
    a = _LifecycleHarness()
    _respawn_fail(a, "dev-3")
    _respawn_fail(a, "dev-3")
    assert not [e for e in a.events if e.type == "worker.respawn.cooldown"]

    b = _LifecycleHarness()  # "重启":全新实例,共享同一事件日志
    b.events.extend(a.events)
    _respawn_fail(b, "dev-3")  # 第 3 次连续失败(跨重启)
    assert [e for e in b.events if e.type == "worker.respawn.cooldown"]
    assert _cascade_events(b)
    assert b.states.get("dev-3") == "blocked_human"


# --- 2026-06-10 review: no owner channel configured → liveness floor -------

class _LivenessHarness(_LifecycleHarness):
    def __init__(self, *, events=None, bridge_enabled=False):
        super().__init__()
        from types import SimpleNamespace as NS
        self.event_log = NS(read_days=lambda days: list(events or []))
        self.config = NS(integrations=NS(
            openclaw_feishu_bridge=NS(enabled=bridge_enabled),
        ))


def test_unknown_liveness_without_configured_channel_is_unreachable():
    """R12 hole: zero owner.visible_message events + no owner channel in
    zf.yaml means escalation reaches no one — the cascade must floor to
    safe-halt instead of escalating into the void."""
    h = _LivenessHarness(events=[], bridge_enabled=False)
    assert h._operator_channel_live() is False
    h._emit_respawn_cascade("dev-3", attempts=h._RESPAWN_FAILURE_MAX_CONSECUTIVE)
    evs = _cascade_events(h)
    assert evs and evs[0].payload["tier"] == "safe_halt"
    assert [e for e in h.events if e.type == "runtime.safe_halted"]


def test_unknown_liveness_with_configured_channel_still_escalates():
    h = _LivenessHarness(events=[], bridge_enabled=True)
    assert h._operator_channel_live() is True
    h._emit_respawn_cascade("dev-3", attempts=h._RESPAWN_FAILURE_MAX_CONSECUTIVE)
    evs = _cascade_events(h)
    assert evs and evs[0].payload["tier"] == "escalate"


def test_confirmed_dead_channel_unreachable_even_when_configured():
    from zf.core.events.model import ZfEvent as _Ev
    fails = [
        _Ev(type="owner.visible_message.failed", actor="zf-cli")
        for _ in range(3)
    ]
    h = _LivenessHarness(events=fails, bridge_enabled=True)
    assert h._operator_channel_live() is False


# --- 2026-06-10 review P1-3: safe-halt freezes the watchdog -----------------

class _WatchdogHarness(_LifecycleHarness):
    """Harness driving the real _capture_logs against a dead transport."""

    def __init__(self, *, halted, tmp_path):
        super().__init__()
        from types import SimpleNamespace as NS
        from zf.core.config.schema import RoleConfig
        self.state_dir = tmp_path
        self.config = NS(roles=[RoleConfig(name="dev", backend="mock")])
        self.transport = NS(is_alive=lambda inst: False)
        self._last_worker_state = {}
        self._dead_counter = {}
        self._dead_threshold = 1
        halt_events = (
            [ZfEvent(type="runtime.safe_halted", actor="dev-1")]
            if halted else []
        )
        self.event_log = NS(read_days=lambda days: list(halt_events))
        self.respawn_calls = []

    def _respawn_instance(self, role):
        self.respawn_calls.append(role.instance_id)
        from zf.runtime.orchestrator_types import OrchestratorDecision
        return OrchestratorDecision(
            action="respawn", role=role.instance_id, reason="test",
        )

    def _active_task_for_instance(self, instance_id):
        return None

    def _emit_worker_runner_failed(self, **kwargs):
        pass

    def _request_manifest_terminal_completion_if_pending(self, **kwargs):
        return None


from zf.core.events.model import ZfEvent  # noqa: E402


def test_watchdog_dead_pane_is_evidence_not_control(tmp_path):
    """2026-06-11-0325(I41 降级):pane probe 不再驱动 respawn——dead pane
    只产一条 worker.pane.dead_observed evidence(per 死亡期去重);respawn
    决策归 events 派生的 heartbeat/stuck 路径。"""
    h = _WatchdogHarness(halted=False, tmp_path=tmp_path)
    h._capture_logs()
    h._capture_logs()  # 第二个 tick:同一死亡期不重复发
    assert h.respawn_calls == []
    observed = [
        e for e in h.events if e.type == "worker.pane.dead_observed"
    ]
    assert len(observed) == 1
    assert observed[0].payload["instance_id"] == "dev"
    assert "I41" in observed[0].payload["note"]


def test_watchdog_frozen_after_safe_halt(tmp_path):
    """doc-80 R14: '878 dispatch_skipped during safe-halt' — the dead-pane
    watchdog kept respawning after the halt. A safe-halted runtime must stop
    generating respawn attempts until dispatch.resumed."""
    h = _WatchdogHarness(halted=True, tmp_path=tmp_path)
    h._capture_logs()
    assert h.respawn_calls == []
    assert h._dead_counter.get("dev", 0) == 0


def test_runtime_safe_halted_cleared_by_dispatch_resumed(tmp_path):
    h = _WatchdogHarness(halted=True, tmp_path=tmp_path)
    from types import SimpleNamespace as NS
    events = [
        ZfEvent(type="runtime.safe_halted", actor="dev-1"),
        ZfEvent(type="dispatch.resumed", actor="zf-cli"),
    ]
    h.event_log = NS(read_days=lambda days: list(events))
    assert h._runtime_safe_halted() is False


# --- 2026-06-10 review P1-4: restart clears safe-halt pause symmetrically ---

import pytest  # noqa: E402
from pathlib import Path  # noqa: E402

from zf.core.config.schema import (  # noqa: E402
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog  # noqa: E402
from zf.core.state.session import SessionStore  # noqa: E402
from zf.runtime.orchestrator import Orchestrator  # noqa: E402
from zf.runtime.tmux import TmuxSession  # noqa: E402
from zf.runtime.transport import TmuxTransport  # noqa: E402


@pytest.fixture
def init_state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


def _init_config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )


def _init_transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def test_restart_resumes_dispatch_after_safe_halt_pause(init_state_dir):
    """zf start un-parks blocked_human workers but previously left the
    remediation safe-halt dispatch.paused in place — workers respawned
    while every task was skipped with reason=dispatch_paused."""
    log = EventLog(init_state_dir / "events.jsonl")
    log.append(ZfEvent(type="runtime.safe_halted", actor="dev"))
    log.append(ZfEvent(
        type="dispatch.paused",
        actor="dev",
        payload={"reason": "safe_halt", "source": "remediation_cascade"},
    ))

    orch = Orchestrator(init_state_dir, _init_config(), _init_transport())

    events = log.read_all()
    resumed = [e for e in events if e.type == "dispatch.resumed"]
    assert len(resumed) == 1
    assert resumed[0].payload["source"] == "orchestrator_init"
    assert orch._dispatch_globally_paused() is False


def test_restart_does_not_resume_maintenance_pause(init_state_dir):
    log = EventLog(init_state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="dispatch.paused",
        actor="zf-cli",
        payload={"reason": "maintenance", "source": "maintenance"},
    ))

    orch = Orchestrator(init_state_dir, _init_config(), _init_transport())

    events = log.read_all()
    assert not [e for e in events if e.type == "dispatch.resumed"]
    assert orch._dispatch_globally_paused() is True


def test_restart_no_resume_when_already_resumed(init_state_dir):
    log = EventLog(init_state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="dispatch.paused",
        actor="dev",
        payload={"reason": "safe_halt", "source": "remediation_cascade"},
    ))
    log.append(ZfEvent(type="dispatch.resumed", actor="zf-cli", payload={}))

    Orchestrator(init_state_dir, _init_config(), _init_transport())

    events = log.read_all()
    resumed = [e for e in events if e.type == "dispatch.resumed"]
    assert len(resumed) == 1  # no duplicate resume on init
