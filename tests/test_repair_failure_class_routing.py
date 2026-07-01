"""doc 79 Tier routing: decide_repair gates Tier2 LLM self-repair to the
content bucket only.

R12: the self-repair loop dispatched 17 fingerprints, 16 of them infra /
structural (worker_stuck / handoff_stall / fatal / dispatch_preflight_blocker)
that an LLM cannot fix → churn. Routing by the rework_triage bucket means only
``content`` (kernel-logic, needs prompt/spec review) failures spawn Tier2;
infra → Tier1 (cascade owns it), terminal → escalate, unknown → not LLM-repairable.
"""

from __future__ import annotations

from zf.runtime.repair_authorization import decide_repair

AUTHORIZED = {"ZF_AUTORESEARCH_AUTO_REPAIR": "authorized"}


def _candidate(fingerprint: str) -> dict:
    return {"candidate": {"fingerprint": fingerprint}}


# --- R12 replay: none of the 17 should dispatch to Tier2 -------------------

R12_FINGERPRINTS = [
    "failure:worker_stuck:dev-lane-4",
    "failure:handoff_stall:CJMIN-R12-WEB-TUI:static_gate.passed",
    "failure:fatal:worker.stuck.recovery_failed:watchdog",
    "failure:dispatch_preflight_blocker:CJMIN-R12-WEB-TUI",
]


def test_transient_infra_does_not_dispatch_to_tier2():
    # Transient infra (worker_stuck) + its fatal wrappers are handled by the
    # Tier1 cascade, not LLM self-repair (that was R12's wasted churn).
    for fp in [
        "failure:worker_stuck:dev-lane-4",
        "failure:fatal:worker.respawn.failed:worker dev-lane-0",
        "failure:fatal:worker.stuck.recovery_failed:watchdog",
    ]:
        d = decide_repair(_candidate(fp), [], env=AUTHORIZED)
        assert d.action != "dispatch", f"{fp} should not dispatch to Tier2"


def test_worker_stuck_routes_to_tier1_not_llm():
    d = decide_repair(_candidate("failure:worker_stuck:dev-lane-0"), [], env=AUTHORIZED)
    assert d.action == "skip"
    assert d.bucket == "infra"
    assert "Tier1" in d.reason or "infra" in d.reason


def test_kernel_logic_structural_dispatches_to_self_repair():
    # R14 fix: handoff_stall (= verify-not-firing: a stage success event not
    # triggering the downstream stage) + dispatch_preflight_blocker are
    # reproducible CODE bugs the authorized self-repair loop can fix. R14
    # detected 5x handoff_stall but the over-broad skip routed 0 to Tier2.
    for fp in [
        "failure:handoff_stall:CJMIN-R14-WEB-TUI:static_gate.passed:review",
        "failure:dispatch_preflight_blocker:CJMIN-R14-PI-CORE",
        "stall:candidate.ready->cj-min-candidate-review:CJMIN-R14",
    ]:
        d = decide_repair(_candidate(fp), [], env=AUTHORIZED)
        assert d.action == "dispatch", f"{fp} should dispatch to Tier2 self-repair"


def test_truly_unknown_class_still_skips():
    d = decide_repair(_candidate("failure:brand_new_thing:x"), [], env=AUTHORIZED)
    assert d.action == "skip"


# --- content bucket DOES dispatch (genuine kernel-logic bug) ----------------

def test_content_bucket_dispatches_to_tier2():
    d = decide_repair(
        _candidate("failure:test_failed_real:slice-pi-core"), [], env=AUTHORIZED
    )
    assert d.action == "dispatch"
    assert d.bucket == "content"


def test_content_bucket_respects_cap():
    from types import SimpleNamespace

    fp = "failure:design_issue:slice-x"
    # two prior dispatches for this fingerprint → at cap → escalate, not dispatch
    events = [
        SimpleNamespace(
            type="autoresearch.repair.dispatch_requested",
            payload={"fingerprint": fp},
        )
        for _ in range(2)
    ]
    d = decide_repair(_candidate(fp), events, env=AUTHORIZED, cap=2)
    assert d.action == "escalate"


# --- terminal → escalate, not LLM ------------------------------------------

def test_terminal_bucket_escalates():
    d = decide_repair(
        _candidate("failure:api_invalid_request:provider"), [], env=AUTHORIZED
    )
    assert d.action == "escalate"
    assert d.bucket == "terminal"


# --- authorization still gates everything ----------------------------------

def test_unauthorized_still_skips_regardless_of_class():
    d = decide_repair(_candidate("failure:test_failed_real:x"), [], env={})
    assert d.action == "skip"
