"""A lost evaluation must terminate before an explicit new attempt is admitted."""

from types import SimpleNamespace
from unittest.mock import Mock
import uuid

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_prd_evaluation_reactor import react_channel_prd_evaluation_requested
from zf.runtime.control_actions import ControlledActionService


def interrupted_evaluation(tmp_path):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    identity = dict(channel_id="ch-recovery", thread_id="main", prd_revision=1,
                    artifact_ref="channels/prd.json", artifact_digest="a" * 64, role="full")
    writer.emit("channel.created", actor="web", payload={
        "channel_id": identity["channel_id"], "owner_actor_ref": "web"})
    writer.emit("channel.synthesis.proposed", actor="web", payload=identity)
    request = writer.emit("channel.prd_evaluation.requested", actor="web",
                          correlation_id=identity["channel_id"],
                          payload={**identity, "request_id": str(uuid.uuid4())})
    writer.emit("channel.prd_evaluation.dispatched", causation_id=request.id,
                payload={"request_id": request.payload["request_id"]})
    return SimpleNamespace(state_dir=tmp_path, event_log=writer.event_log, event_writer=writer), request


def test_replay_closes_lost_execution_once(tmp_path, monkeypatch):
    host, request = interrupted_evaluation(tmp_path)
    provider = Mock(side_effect=AssertionError("old request cannot run again"))
    monkeypatch.setattr("zf.runtime.channel_prd_evaluation_reactor.evaluate_output", provider)
    react_channel_prd_evaluation_requested(host, request)
    react_channel_prd_evaluation_requested(host, request)
    failed = [e for e in host.event_log.read_all() if e.type == "output.evaluation.failed"]
    assert len(failed) == 1
    assert failed[0].payload["request_id"] == request.payload["request_id"]
    assert failed[0].payload["verdict"] == "INDETERMINATE"
    provider.assert_not_called()


def test_explicit_retry_recovers_lost_execution(tmp_path):
    host, prior = interrupted_evaluation(tmp_path)
    service = ControlledActionService(tmp_path, host.event_writer,
                                     actor="web", source="web", surface="web")
    requested = host.event_writer.emit("runtime.action.requested", actor="web",
                                       payload={"action": "channel-prd-evaluate"})
    result = service.execute(action="channel-prd-evaluate", requested_action="channel-prd-evaluate",
                             requested=requested, payload={**prior.payload, "retry": True})
    assert result["status"] == "requested", result
    assert result["request_id"] != prior.payload["request_id"]


def test_thread_start_failure_releases_execution_and_records_failure(tmp_path, monkeypatch):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    request = writer.emit("channel.prd_evaluation.requested", payload={
        "request_id": str(uuid.uuid4()), "channel_id": "ch-start", "thread_id": "main"})
    host = SimpleNamespace(state_dir=tmp_path, event_log=writer.event_log, event_writer=writer)
    thread = Mock()
    thread.start.side_effect = RuntimeError("thread unavailable")
    monkeypatch.setattr("zf.runtime.channel_prd_evaluation_reactor.threading.Thread", lambda **kw: thread)
    react_channel_prd_evaluation_requested(host, request)
    react_channel_prd_evaluation_requested(host, request)
    failed = [e for e in writer.event_log.read_all() if e.type == "output.evaluation.failed"]
    assert len(failed) == 1 and failed[0].payload["reason"] == "thread unavailable"
    from zf.runtime.channel_prd_evaluation_recovery import evaluation_execution_lock
    with evaluation_execution_lock(tmp_path, request.payload["request_id"]):
        pass
