"""Unscoped escalation is not resolved by unrelated worker progress."""
import json

import pytest

from tests.test_run_manager import _config, _state
from zf.core.events.model import ZfEvent
from zf.runtime.run_manager import build_run_manager_projection


@pytest.mark.parametrize("kind", ["worker.state.changed", "worker.heartbeat", "dev.build.done", "judge.passed"])
def test_unscoped_human_escalation_survives_unrelated_progress(tmp_path, kind):
    state, log, _ = _state(tmp_path)
    directory = state / "projections/supervisor"
    directory.mkdir(parents=True)
    (directory / "snapshot.json").write_text(json.dumps({"attention_items": [{
        "status": "open", "fingerprint": "reader-infra", "source_event_ids": ["evt-infra"],
    }]}))
    log.append(ZfEvent(id="evt-infra", type="human.escalate", payload={
        "reason": "reader infrastructure repair requires owner action", "workflow_run_id": "affected-run",
    }))
    log.append(ZfEvent(id="evt-worker", type=kind, actor="planner", payload={
        "state": "suspending", "workflow_run_id": "other-run",
    }))
    projection = build_run_manager_projection(state, events=log.read_all(), config=_config())
    assert projection["monitor"]["open_attention"] >= 1
    assert not projection["monitor"]["suppressed_open_attention"]
