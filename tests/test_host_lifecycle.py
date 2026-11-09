from zf.core.events import ZfEvent
from zf.runtime.host_lifecycle import host_restart_eligibility


def test_unexpected_exit_keeps_restart_eligibility_but_stop_intent_does_not(tmp_path):
    events = [ZfEvent(type="loop.started")]
    assert host_restart_eligibility(tmp_path, events)["eligible"]
    for stop in ("loop.shutdown_requested", "runtime.stop.requested", "run.cancelled", "loop.stopped"):
        assert not host_restart_eligibility(tmp_path, events + [ZfEvent(type=stop)])["eligible"]
    assert not host_restart_eligibility(tmp_path, events + [ZfEvent(type="dispatch.paused")])["eligible"]
    assert not host_restart_eligibility(tmp_path, events + [ZfEvent(type="event.malformed", payload={"ledger_integrity": "unknown"})])["eligible"]
    assert not host_restart_eligibility(tmp_path, [])["eligible"]
