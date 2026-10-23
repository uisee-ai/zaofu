"""Process loss -> kernel reconciliation -> explicit retry, with no real provider."""

import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from zf.core.config.schema import ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.core.state.session import SessionStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


pytestmark = pytest.mark.mock_e2e


def test_process_loss_recovers_through_kernel_and_controlled_retry(tmp_path, monkeypatch):
    state = tmp_path / "runtime-state"
    SessionStore(state / "session.yaml").create(project_root=str(tmp_path))
    writer = EventWriter(EventLog(state / "events.jsonl"))
    writer.emit("channel.created", actor="web", payload={"channel_id": "ch-recovery", "owner_actor_ref": "web"})
    identity = {"channel_id": "ch-recovery", "thread_id": "main", "prd_revision": 1,
                "artifact_ref": "channels/prd.json", "artifact_digest": "a" * 64, "role": "full"}
    writer.emit("channel.synthesis.proposed", actor="web", payload=identity)
    service = ControlledActionService(state, writer, actor="web", source="web", surface="web")

    def request(retry=False):
        action = writer.emit("runtime.action.requested", actor="web", payload={"action": "channel-prd-evaluate"})
        return service.execute(action="channel-prd-evaluate", requested_action="channel-prd-evaluate",
                               requested=action, payload={**identity, "retry": retry})

    first = request()
    event = next(e for e in writer.event_log.read_all() if e.type == "channel.prd_evaluation.requested")
    script = '''
import json, sys, threading
from pathlib import Path
from types import SimpleNamespace
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime import channel_prd_evaluation_reactor as reactor
state = Path(sys.argv[1])
writer = EventWriter(EventLog(state / "events.jsonl"))
def provider(**kw):
    writer.emit("output.evaluation.started", payload={"request_id": kw["request_id"]})
    threading.Event().wait()
reactor._request_item = lambda *args: object()
reactor.evaluate_output = provider
host = SimpleNamespace(state_dir=state, event_log=writer.event_log, event_writer=writer)
reactor.react_channel_prd_evaluation_requested(host, ZfEvent(**json.loads(sys.argv[2])))
threading.Event().wait()
'''
    child = subprocess.Popen([sys.executable, "-c", script, str(state), event.to_json()],
                             cwd=Path(__file__).resolve().parents[2])
    try:
        deadline = time.monotonic() + 10
        while not any(e.type == "output.evaluation.started" for e in writer.event_log.read_all()):
            assert child.poll() is None, "mock provider process exited before starting"
            assert time.monotonic() < deadline, "mock provider did not start"
            time.sleep(0.02)
        assert request(retry=True)["request_id"] == first["request_id"]
    finally:
        child.kill()
        child.wait(timeout=5)

    runtime = Orchestrator(state, ZfConfig(), TmuxTransport(TmuxSession(session_name="unused", dry_run=True)),
                           project_root=tmp_path)
    runtime.run_once(events=[])
    failed = [e for e in writer.event_log.read_all() if e.type == "output.evaluation.failed"]
    assert len(failed) == 1 and failed[0].payload["request_id"] == first["request_id"]
    second = request(retry=True)
    assert second["status"] == "requested" and second["request_id"] != first["request_id"]

    completed = threading.Event()

    def provider(**kwargs):
        kwargs["writer"].emit("output.evaluation.completed", payload={
            "request_id": kwargs["request_id"], "verdict": "INDETERMINATE"})
        completed.set()

    monkeypatch.setattr("zf.runtime.channel_prd_evaluation_reactor._request_item", lambda *args: object())
    monkeypatch.setattr("zf.runtime.channel_prd_evaluation_reactor.evaluate_output", provider)
    runtime.run_once(events=[])
    assert completed.wait(5)
    runtime.run_once(events=[])
    events = writer.event_log.read_all()
    assert len([e for e in events if e.type == "output.evaluation.completed"]) == 1
    assert len([e for e in events if e.type == "output.evaluation.failed"]) == 1
