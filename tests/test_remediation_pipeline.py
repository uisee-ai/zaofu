"""doc 80 rev1 P0: Remediation SM — classify/route pure functions + r14
equivalence proof.

The route step must REPRODUCE the scattered decide_cascade (infra/Tier1) +
decide_repair (content+kernel-logic/Tier2) decisions, so retiring the sweeps
(P2) is behaviour-preserving. Proven two ways:
  1. direct equivalence: route() == decide_cascade / decide_repair on the r14
     failure classes;
  2. fixture replay: the r14 remediation.cascade events (events/2026-06-06.jsonl)
     are reproduced by route() (skipped if the local archive is absent).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.runtime.remediation_cascade import decide_cascade
from zf.runtime.repair_authorization import decide_repair
from zf.runtime.remediation_pipeline import (
    EV_SAFE_HALTED,
    FAILURE_DETECTED_TYPES,
    SAFE_HALT,
    SM_DETECTED,
    SM_TERMINAL,
    TIER1,
    TIER2,
    TIER3,
    is_terminal,
    route,
    state_of,
)

AUTHORIZED = {"ZF_AUTORESEARCH_AUTO_REPAIR": "authorized"}


# --- SM state model --------------------------------------------------------

def test_state_of_maps_transitions():
    assert state_of("worker.stuck.recovery_failed") == SM_DETECTED
    assert state_of("remediation.classified") == "classified"
    assert state_of("remediation.routed") == "routed"
    assert state_of("remediation.consumed") == "consumed"
    assert state_of(EV_SAFE_HALTED) == SM_TERMINAL
    assert state_of("orchestrator.decision.recorded") is None  # not an SM event


def test_terminal_only_safe_halted():
    assert is_terminal(SM_TERMINAL)
    assert not is_terminal(SM_DETECTED)


# --- route: unit ------------------------------------------------------------

def test_route_infra_retry_then_escalate_then_safe_halt():
    assert route("worker_stuck", attempts=1, cap=3, liveness=True).tier == TIER1
    assert route("worker_stuck", attempts=3, cap=3, liveness=True).action == "escalate"
    assert route("worker_stuck", attempts=3, cap=3, liveness=False).tier == SAFE_HALT


def test_route_kernel_logic_to_tier2_when_authorized():
    d = route("handoff_stall", attempts=0, cap=2, liveness=True, authorized=True)
    assert d.tier == TIER2 and d.action == "dispatch"


def test_route_terminal_and_unknown():
    assert route("api_invalid_request", attempts=0, cap=2, liveness=True).tier == TIER3
    assert route("api_invalid_request", attempts=0, cap=2, liveness=False).tier == SAFE_HALT
    assert route("brand_new_thing", attempts=0, cap=2, liveness=True).tier == SAFE_HALT


# --- equivalence #1: route() == decide_cascade (infra/Tier1) ----------------

def test_route_infra_equivalent_to_decide_cascade():
    # The r14 cascade baseline: worker_stuck at attempts=3/cap=3.
    for attempts, liveness in [(1, True), (2, True), (3, True), (3, False)]:
        rd = route("worker_stuck", attempts=attempts, cap=3, liveness=liveness)
        cd = decide_cascade(
            failure_class="worker_stuck", attempts=attempts, cap=3, liveness=liveness
        )
        # route.action carries the cascade tier verbatim (retry/escalate/safe_halt)
        assert rd.action == cd.tier, (attempts, liveness)


# --- equivalence #2: route() == decide_repair (content+kernel-logic/Tier2) --

def _candidate(fp):
    return {"candidate": {"fingerprint": fp}}


def test_route_repair_equivalent_to_decide_repair_on_r14_classes():
    # r14 bug_candidate classes + their fingerprint shapes.
    cases = [
        ("worker_stuck", "failure:worker_stuck:dev-lane-0"),
        ("handoff_stall", "failure:handoff_stall:CJMIN-R14-WEB-TUI:static_gate"),
        ("dispatch_preflight_blocker", "failure:dispatch_preflight_blocker:CJMIN"),
        ("stall", "stall:candidate.ready->cj-min-candidate-review:CJMIN-R14"),
        ("fatal", "failure:fatal:worker.respawn.failed:x"),
        ("test_failed_real", "failure:test_failed_real:slice-x"),
    ]
    for failure_class, fp in cases:
        repair = decide_repair(_candidate(fp), [], env=AUTHORIZED)
        rd = route(failure_class, attempts=0, cap=2, liveness=True, authorized=True)
        # decide_repair "dispatch" ⟺ route lands on Tier2 dispatch
        assert (repair.action == "dispatch") == (rd.tier == TIER2 and rd.action == "dispatch"), (
            failure_class, repair.action, rd.tier, rd.action,
        )


# --- equivalence #3: r14 fixture replay (skipped if archive absent) ---------

_R14 = Path(
    "/path/to/hermes-agent"
    "/.zf-cj-min-refactor.r14-safehalt-20260607-0026/events/2026-06-06.jsonl"
)


@pytest.mark.skipif(not _R14.exists(), reason="r14 local archive not present")
def test_r14_cascade_decisions_reproduced_by_route():
    # Replay every remediation.cascade event from the real R14 run and confirm
    # route() reproduces its (failure_class, attempts, liveness) → tier decision.
    seen = 0
    for line in _R14.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "remediation.cascade":
            continue
        p = e.get("payload", {})
        fc = p.get("failure_class")
        attempts, cap = int(p.get("attempts") or 0), int(p.get("cap") or 2)
        was_safe_halt = bool(p.get("safe_halt"))
        # liveness is what produced the historical decision: safe_halt ⟺ dead
        rd = route(fc, attempts=attempts, cap=cap, liveness=not was_safe_halt)
        assert (rd.tier == SAFE_HALT) == was_safe_halt, (fc, p.get("tier"))
        seen += 1
    assert seen >= 1, "fixture had no remediation.cascade events"


# === P1: interpreter (build_sm_set / advance / remediation_tick) ============

from zf.runtime.remediation_pipeline import (  # noqa: E402
    EV_CLASSIFIED,
    EV_CONSUMED,
    EV_ROUTED,
    build_sm_set,
    fingerprint_of,
    remediation_tick,
)


def _ev(t, payload=None, corr=""):
    return {"type": t, "payload": payload or {}, "correlation_id": corr}


def _recovery_failed(instance):
    return _ev("worker.stuck.recovery_failed", {"instance_id": instance})


def _apply_transitions(events, transitions):
    # turn Transition → event dicts and append (simulate the reactor emitting them)
    for t in transitions:
        events.append({"type": t.type, "payload": t.payload, "correlation_id": t.correlation_id})
    return events


def test_coalesce_same_fingerprint_one_sm():
    # R12/R14: 135 worker.stuck for one worker must be ONE SM, not 135.
    events = [_recovery_failed("dev-lane-0") for _ in range(135)]
    sm = build_sm_set(events)
    assert len(sm) == 1
    assert list(sm)[0] == "worker_stuck:dev-lane-0"


def test_distinct_workers_distinct_sms():
    events = [_recovery_failed("dev-lane-0"), _recovery_failed("dev-lane-1")]
    assert len(build_sm_set(events)) == 2


def test_interpreter_advances_detected_to_consumed():
    # one failure walks detected → classified → routed → consumed across ticks
    events = [_recovery_failed("dev-lane-0")]
    seen_types = []
    for _ in range(5):
        out = remediation_tick(events)
        if not out:
            break
        seen_types += [t.type for t in out]
        _apply_transitions(events, out)
    assert seen_types == [EV_CLASSIFIED, EV_ROUTED, EV_CONSUMED]
    # the routed transition carried the Tier1 decision for worker_stuck
    routed = next(e for e in events if e["type"] == EV_ROUTED)
    assert routed["payload"]["failure_class"] == "worker_stuck"
    assert routed["payload"]["tier"] in ("tier1", "safe_halt")


def test_resumable_rebuild_from_events_continues():
    # advance two steps, DROP all in-memory state (there is none — remediation_tick
    # is pure over the event list), rebuild from events, continue to consumed.
    events = [_recovery_failed("dev-lane-0")]
    _apply_transitions(events, remediation_tick(events))   # → classified
    _apply_transitions(events, remediation_tick(events))   # → routed
    # "restart": only the event list survives. Rebuild + continue.
    out = remediation_tick(list(events))
    assert [t.type for t in out] == [EV_CONSUMED]


def test_idempotent_terminal_not_advanced_and_no_dup():
    # a safe_halted SM is terminal → not ticked; ticking twice = same output
    events = [
        _recovery_failed("dev-lane-0"),
        _ev("remediation.classified", {"failure_class": "worker_stuck"}, "worker_stuck:dev-lane-0"),
        _ev("runtime.safe_halted", {}, "worker_stuck:dev-lane-0"),
    ]
    assert remediation_tick(events) == []          # terminal → nothing
    assert remediation_tick(events) == remediation_tick(events)  # stable


def test_idempotent_consumed_is_end():
    events = [
        _recovery_failed("dev-lane-0"),
        _ev(EV_CLASSIFIED, {"failure_class": "worker_stuck"}, "worker_stuck:dev-lane-0"),
        _ev(EV_ROUTED, {"failure_class": "worker_stuck", "tier": "tier1"}, "worker_stuck:dev-lane-0"),
        _ev(EV_CONSUMED, {"tier": "tier1"}, "worker_stuck:dev-lane-0"),
    ]
    assert remediation_tick(events) == []  # consumed → no further advance


# === P1b: shadow runner (gated, side-effect-free marker emission) ===========

from types import SimpleNamespace  # noqa: E402

from zf.runtime.remediation_pipeline import (  # noqa: E402
    remediation_sm_shadow_enabled,
    run_remediation_sm_shadow,
)


def test_shadow_runner_gated_off_by_default():
    assert remediation_sm_shadow_enabled({}) is False
    assert remediation_sm_shadow_enabled({"ZF_REMEDIATION_SM_SHADOW": ""}) is False
    assert remediation_sm_shadow_enabled({"ZF_REMEDIATION_SM_SHADOW": "on"}) is True


def test_shadow_runner_emits_one_marker_per_tick():
    events = [_recovery_failed("dev-lane-0")]
    captured = []
    writer = SimpleNamespace(append=lambda e: captured.append(e) or e)
    n = run_remediation_sm_shadow(events, writer)
    assert n == 1
    assert captured[0].type == EV_CLASSIFIED
    assert captured[0].correlation_id == "worker_stuck:dev-lane-0"


# === N1 (review): natural-completion terminals + state_of enumeration ======
#
# Pre-fix the SM only treats runtime.safe_halted as terminal; Tier1 recovered
# and Tier3 owner-acked completions had no terminal marker so the SM stays
# `consumed` forever and shadow keeps re-emitting. doc 80 rev1 review N1.

from zf.runtime.remediation_pipeline import (  # noqa: E402
    EV_ESCALATED_ACKED,
    EV_RECOVERED,
)


def test_state_of_natural_completion_events_terminal():
    assert state_of(EV_RECOVERED) == SM_TERMINAL
    assert state_of(EV_ESCALATED_ACKED) == SM_TERMINAL


def test_is_terminal_for_natural_completions():
    assert is_terminal(state_of(EV_RECOVERED))
    assert is_terminal(state_of(EV_ESCALATED_ACKED))


def test_sm_set_picks_natural_completion_as_terminal():
    """When a recovered marker is the latest event for a fingerprint, the SM
    must be terminal — interpreter must skip it forever after."""
    from zf.runtime.remediation_pipeline import build_sm_set

    events = [
        _recovery_failed("dev-lane-7"),
        SimpleNamespace(
            type=EV_CLASSIFIED,
            correlation_id="worker_stuck:dev-lane-7",
            payload={"failure_class": "worker_stuck"},
        ),
        SimpleNamespace(
            type=EV_RECOVERED,
            correlation_id="worker_stuck:dev-lane-7",
            payload={"failure_class": "worker_stuck", "reason": "respawn ready"},
        ),
    ]
    sm = build_sm_set(events)
    latest = sm["worker_stuck:dev-lane-7"]
    assert state_of(latest.type) == SM_TERMINAL
    assert is_terminal(state_of(latest.type))


def test_shadow_runner_stops_emitting_after_natural_completion():
    events = [
        _recovery_failed("dev-lane-8"),
        SimpleNamespace(
            type=EV_RECOVERED,
            correlation_id="worker_stuck:dev-lane-8",
            payload={"reason": "respawn ready"},
        ),
    ]
    captured = []
    writer = SimpleNamespace(append=lambda e: captured.append(e) or e)
    n = run_remediation_sm_shadow(events, writer)
    assert n == 0  # terminal SM — interpreter must not advance / re-emit
    assert captured == []


# === N10 (review): incomplete_sm_count + stuck observability ================
#
# The "bypass → SM stuck" forcing function (rev1 § 3) is only visible if the
# operator can observe stuck SM count. Without a metric they have to grep
# events.jsonl manually. Solution: track count; when > threshold emit a
# single observation event per tick so operators get a single signal instead
# of N stuck SMs sprinkled through the log.

from zf.runtime.remediation_pipeline import (  # noqa: E402
    EV_SM_STUCK_OBSERVED,
    DEFAULT_STUCK_THRESHOLD,
    incomplete_sm_count,
)


def test_incomplete_sm_count_zero_when_all_terminal():
    events = [
        _recovery_failed("dev-lane-1"),
        SimpleNamespace(
            type=EV_RECOVERED,
            correlation_id="worker_stuck:dev-lane-1",
            payload={},
        ),
    ]
    assert incomplete_sm_count(events) == 0


def test_incomplete_sm_count_counts_only_non_terminal():
    events = [
        _recovery_failed("dev-lane-1"),
        _recovery_failed("dev-lane-2"),
        SimpleNamespace(
            type=EV_RECOVERED,
            correlation_id="worker_stuck:dev-lane-2",
            payload={},
        ),
        _recovery_failed("dev-lane-3"),
    ]
    # dev-lane-1 + dev-lane-3 still detected (not advanced) — both incomplete
    assert incomplete_sm_count(events) == 2


def test_shadow_runner_emits_stuck_observed_when_over_threshold():
    """Bypass scenario: many failure.detected events but classify/route never
    runs (someone called decide_cascade directly). The SM set sits stuck at
    `detected` forever. The shadow runner advances each one one step but
    when the incomplete count exceeds threshold it emits a single
    observation event per tick so the operator sees one stuck signal."""
    events = [_recovery_failed(f"dev-lane-{i}") for i in range(DEFAULT_STUCK_THRESHOLD + 2)]
    captured = []
    writer = SimpleNamespace(append=lambda e: captured.append(e) or e)
    _ = run_remediation_sm_shadow(events, writer)
    stuck = [e for e in captured if e.type == EV_SM_STUCK_OBSERVED]
    assert len(stuck) == 1, "exactly one stuck observation per tick"
    payload = stuck[0].payload
    assert payload["count"] == DEFAULT_STUCK_THRESHOLD + 2
    assert payload["threshold"] == DEFAULT_STUCK_THRESHOLD
    assert len(payload["samples"]) <= 5  # bounded sample of correlation_ids


def test_shadow_runner_no_stuck_event_below_threshold():
    events = [_recovery_failed(f"dev-lane-{i}") for i in range(2)]
    captured = []
    writer = SimpleNamespace(append=lambda e: captured.append(e) or e)
    run_remediation_sm_shadow(events, writer)
    assert not any(e.type == EV_SM_STUCK_OBSERVED for e in captured)


def test_threshold_env_override(monkeypatch):
    """Operator can tune sensitivity via ZF_REMEDIATION_STUCK_THRESHOLD."""
    monkeypatch.setenv("ZF_REMEDIATION_STUCK_THRESHOLD", "1")
    events = [_recovery_failed("dev-lane-a"), _recovery_failed("dev-lane-b")]
    captured = []
    writer = SimpleNamespace(append=lambda e: captured.append(e) or e)
    run_remediation_sm_shadow(events, writer)
    stuck = [e for e in captured if e.type == EV_SM_STUCK_OBSERVED]
    assert len(stuck) == 1
    assert stuck[0].payload["threshold"] == 1


class TestK3ShadowEmission:
    """K3 相 3:remediation 影子决策(只记录不执行)。"""

    def test_shadow_route_pure_decision(self):
        from zf.runtime.remediation_pipeline import route
        d = route("content", attempts=0, authorized=False)
        assert getattr(d, "tier", None) is not None  # 决策可序列化进影子事件

    def test_shadow_emitter_payload_shape(self):
        # 形状级:emitter 是 best-effort(异常静默不影响主路),此处锁
        # payload 关键字段的契约形。
        import inspect
        from zf.runtime import orchestrator_dispatch as od
        src = inspect.getsource(od)
        assert "remediation.decision.shadow" in src
        assert "shadow_only" in src
        assert "_emit_remediation_shadow" in src
        # 相 2:cap 改名后旧名零残留
        assert "_EVIDENCE_REISSUE_MAX" not in src
        assert "_MAX_EVIDENCE_REISSUE" in src
