"""Workflow list reads share admission work without mixing Request identities."""
import json
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime import run_admission
from tests.test_web_workflow_proposals import _client


def test_workflow_list_reduces_admission_once_for_all_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, state = _client(tmp_path, monkeypatch)
    requests = state / "workflow-requests"
    requests.mkdir()
    writer = EventWriter(EventLog(state / "events.jsonl"))
    for index in range(20):
        request_id, run_id = f"REQ-{index}", f"RUN-{index}"
        (requests / f"{request_id}.json").write_text(json.dumps({
            "request_id": request_id, "run_id": run_id, "status": "proposed",
        }), encoding="utf-8")
        if index == 19:
            continue  # This Request has no admission fact yet.
        writer.append(ZfEvent(
            type={0: "run.admission.admitted", 2: "run.cancelled"}.get(index, "run.admission.queued"),
            actor="test", correlation_id=run_id,
            payload={"run_id": run_id, "request_id": request_id},
        ))
    calls = []
    original = run_admission.build_run_admission_projection

    def counted(events):
        calls.append(1)
        return original(events)

    monkeypatch.setattr(run_admission, "build_run_admission_projection", counted)
    response = client.get("/api/projects/default/workflow-requests")
    assert response.status_code == 200
    items = {item["request_id"]: item for item in response.json()["items"]}
    assert len(items) == 20
    assert items["REQ-0"]["status"] == "running"
    assert items["REQ-1"]["status"] == "queued"
    assert items["REQ-2"]["status"] == "cancelled"
    assert items["REQ-19"]["status"] == "proposed"
    assert items["REQ-19"]["run_admission"]["status"] == ""
    assert all(item["run_admission"]["request_id"] == key for key, item in items.items() if key != "REQ-19")
    assert len(calls) == 1
    for request_id in ("REQ-0", "REQ-1", "REQ-2", "REQ-19"):
        detail = client.get(f"/api/projects/default/workflow-requests/{request_id}")
        assert detail.status_code == 200
        assert detail.json()["lifecycle"]["admission"] == items[request_id]["run_admission"]
