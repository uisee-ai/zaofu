"""Progress publication follows verified placement after operation recovery."""
import json

import pytest

from zf.core.events import EventWriter, ZfEvent
from tests.test_work_record import setup, publish
from tests.test_work_record_dispatch import dispatch


def recovered(state, *, fault=""):
    _, _, log, record, token = setup(state, canonical_request=True)
    dispatch(log, record)
    sent = {
        "workflow_run_id": "r", "task_id": "T7", "operation_id": "op",
        "attempt_id": "a", "dispatch_id": "a", "lease_id": "operation-lease",
        "placement_epoch": 3,
    }
    if fault == "lease":
        sent["lease_id"] = "old-lease"
    if fault != "missing_sent":
        EventWriter(log).append(ZfEvent(
            type="task.attempt.sent", task_id="T7",
            origin="worker" if fault == "untrusted_sent" else "kernel", payload=sent,
        ))
    EventWriter(log).append(ZfEvent(
        type="workflow.operation.started", task_id="T7", origin="kernel",
        payload={"workflow_run_id": "r", "operation_id": "op", "active_attempt_id": "a",
                 "dispatch_id": "a", "lease_id": "operation-lease", "role_instance": "dev-1",
                 "task_pipeline_stage": "impl"},
    ))
    dispatch(log, record, placement_epoch=1 if fault == "old_dispatch" else 3)
    return log, record, token


def test_recovered_placement_can_publish_and_read_via_cli(tmp_path, monkeypatch, capsys):
    from zf.cli.main import main
    state = tmp_path / "state"
    log, record, token = recovered(state)
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "dev-1")
    monkeypatch.setenv("ZF_RESULT_SUBMIT_TOKEN", token)
    assert main(["artifact", "work-record", "publish", "--operation", "op", "--file", str(record),
                 "--request-id", "one", "--state-dir", str(state)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert main(["artifact", "work-record", "show", "--operation", "op", "--state-dir", str(state)]) == 0
    assert json.loads(capsys.readouterr().out)["sha256"] == result["work_record_ref"]["sha256"]
    assert publish(state, log, record, token)["replayed"]


@pytest.mark.parametrize("fault", ["missing_sent", "untrusted_sent", "lease", "old_dispatch"])
def test_recovery_requires_current_verified_placement(tmp_path, fault):
    state = tmp_path / "state"
    log, record, token = recovered(state, fault=fault)
    with pytest.raises(ValueError):
        publish(state, log, record, token)
    assert not any(e.type == "worker.work_record.published" for e in log.read_all())
