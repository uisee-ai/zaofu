"""Task anchors survive the real reader retry dispatch path."""

import time
from pathlib import Path

import pytest

from tests.test_fanout_hardening import _state
from zf.core.config.schema import RoleConfig
from zf.core.events import ZfEvent


def test_reader_timeout_retry_preserves_dispatched_task_anchor(tmp_path: Path):
    _, log, transport, runtime = _state(tmp_path, timeout_seconds=1, max_retries=1)
    runtime.run_once(events=[ZfEvent(
        type="candidate.ready", task_id="TASK-ROOT", actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111", "task_id": "TASK-ROOT"},
    )])
    initial = [event for event in log.read_all() if event.type == "fanout.child.dispatched"]
    assert len(initial) == 1
    assert transport.sent[0][1].read_text().splitlines()[0] == "Active task: TASK-ROOT"
    runtime._set_worker_state("review-a", "idle", force=True)
    runtime._now = lambda: time.time() + 10
    runtime.run_once(events=[])
    dispatches = [event for event in log.read_all() if event.type == "fanout.child.dispatched"]
    assert len(dispatches) == 2
    retry = dispatches[-1]
    assert retry.payload["task_id"] == "TASK-ROOT"
    assert retry.payload["retry_of_run_id"] == initial[0].payload["run_id"]
    text = transport.sent[-1][1].read_text()
    assert text.splitlines()[0] == "Active task: TASK-ROOT"
    assert retry.payload["run_id"] in text
    assert "Do not run `zf guard ownership`" in text


@pytest.mark.parametrize("child,expected", [
    ({"task_id": "TASK-CHILD", "payload": {"task_id": "TASK-PAYLOAD"}}, "TASK-CHILD"),
    ({"payload": {"task_id": "TASK-PAYLOAD"}}, "TASK-PAYLOAD"),
    ({"payload": {"parent_task_id": "TASK-PARENT"}}, "TASK-PARENT"),
    ({"payload": {"trigger_payload": {"task_id": "TASK-TRIGGER"}}}, "TASK-TRIGGER"),
    ({"payload": {}}, ""),
])
def test_reader_retry_anchor_uses_existing_identity_only(tmp_path: Path, child: dict, expected: str):
    _, _, _, runtime = _state(tmp_path)
    path = runtime._write_fanout_retry_briefing(
        role=RoleConfig(name="review-a", backend="mock", role_kind="reader"),
        manifest={"fanout_id": "fanout-retry", "stage_id": "review-candidate",
                  "topology": "fanout_reader", "aggregate_config": {}},
        child={"child_id": "review-a", **child}, run_id="run-retry-1",
    )
    text = path.read_text()
    if expected:
        assert text.splitlines()[0] == f"Active task: {expected}"
    else:
        assert "Active task:" not in text
    assert "Do not run `zf guard ownership`" in text
