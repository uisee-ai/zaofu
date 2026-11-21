from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.candidate_rework_publication import publish_candidate_rework


def test_checkpoint_replay_rejects_payload_drift_and_keeps_runs_separate(tmp_path):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"), default_origin="kernel")
    service = SimpleNamespace(writer=writer, state_dir=tmp_path)
    kwargs = dict(actor="zf-cli", correlation_id="run-1", causation_id="failure-1",
                  payload={"resume_checkpoint_ref": "repair-1", "task_ids": ["FIX-1"]})
    first = publish_candidate_rework(service, "task_map.ready", **kwargs)
    assert publish_candidate_rework(service, "task_map.ready", **kwargs).id == first.id
    with pytest.raises(ValueError, match="diverged"):
        publish_candidate_rework(service, "task_map.ready", **{**kwargs,
            "payload": {**kwargs["payload"], "task_ids": ["UNAUTHORIZED"]}})
    assert len(writer.event_log.read_all()) == 1
    other = publish_candidate_rework(service, "task_map.ready", **{**kwargs, "correlation_id": "run-2"})
    assert other.id != first.id
