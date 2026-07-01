"""backlog 0820 block B: authorized self-repair gate + bounded cap."""

from __future__ import annotations

from types import SimpleNamespace

from zf.runtime.repair_authorization import (
    REPAIR_DISPATCH_EVENT,
    auto_repair_authorized,
    canonical_repair_fingerprint,
    decide_repair,
    repair_attempts_for_fingerprint,
)


# doc 79 Tier routing: only the content bucket dispatches to Tier2 self-repair,
# so cap/dispatch mechanics are exercised with a content-bucket fingerprint
# (test_failed_real). Infra/structural classes (e.g. failure:fatal) now skip —
# covered by test_repair_failure_class_routing.py.
def _candidate(fp="failure:test_failed_real:slice-x"):
    return {"candidate": {"candidate_id": "HIC-1", "fingerprint": fp}}


def _dispatch_ev(fp):
    return SimpleNamespace(type=REPAIR_DISPATCH_EVENT, payload={"fingerprint": fp})


def test_default_off_skips():
    d = decide_repair(_candidate(), [], env={})
    assert d.action == "skip"
    assert auto_repair_authorized({}) is False
    assert auto_repair_authorized({"ZF_AUTORESEARCH_AUTO_REPAIR": "off"}) is False


def test_authorized_under_cap_dispatches():
    d = decide_repair(_candidate(), [], env={"ZF_AUTORESEARCH_AUTO_REPAIR": "authorized"})
    assert d.action == "dispatch"
    assert d.attempt == 1
    assert d.fingerprint.startswith("failure:test_failed_real")


def test_authorized_at_cap_escalates():
    fp = "failure:test_failed_real:slice-x"
    events = [_dispatch_ev(fp), _dispatch_ev(fp)]  # already 2 attempts == cap
    d = decide_repair(_candidate(fp), events, env={"ZF_AUTORESEARCH_AUTO_REPAIR": "authorized"}, cap=2)
    assert d.action == "escalate"
    assert d.attempt == 2


def test_cap_counts_only_same_fingerprint():
    events = [_dispatch_ev("other-fp"), _dispatch_ev("other-fp")]
    fp = "failure:test_failed_real:my-subject"
    d = decide_repair(_candidate(fp), events, env={"ZF_AUTORESEARCH_AUTO_REPAIR": "authorized"})
    assert d.action == "dispatch"  # different fingerprint, not capped
    assert repair_attempts_for_fingerprint(events, fp) == 0


def test_structural_failure_prefix_counts_as_same_repair_fingerprint():
    direct = "task_ref_rejected:CJMIN-PI-CORE-001:evt-dev:workdir dirty"
    wrapped = "failure:" + direct
    events = [_dispatch_ev(direct), _dispatch_ev(wrapped)]

    d = decide_repair(
        _candidate(wrapped),
        events,
        env={"ZF_AUTORESEARCH_AUTO_REPAIR": "authorized"},
        cap=2,
    )

    assert canonical_repair_fingerprint(wrapped) == direct
    assert repair_attempts_for_fingerprint(events, wrapped) == 2
    assert d.fingerprint == direct
    assert d.action == "escalate"


def test_no_fingerprint_skips():
    d = decide_repair({"candidate": {}}, [], env={"ZF_AUTORESEARCH_AUTO_REPAIR": "authorized"})
    assert d.action == "skip"
