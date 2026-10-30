import hashlib
import json

import pytest

from tests.test_web_workflow_proposals import _client
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


@pytest.mark.parametrize("fault", ["", "digest", "foreign_request", "foreign_project", "unknown_task", "outside"])
def test_web_task_binding_comes_from_verified_project_input(tmp_path, monkeypatch, fault):
    client, state = _client(tmp_path, monkeypatch)
    TaskStore(state / "kanban.json").add(Task(id="T1", title="Hello"))
    root = state / "workflow-requests"
    root.mkdir()
    manifest = {
        "request_id": "OTHER" if fault == "foreign_request" else "REQ",
        "project_id": "OTHER" if fault == "foreign_project" else "default",
        "task_input_binding": {"task_id": "OTHER" if fault == "unknown_task" else "T1",
                               "task_contract_digest": "sha256:pinned"},
    }
    body = json.dumps(manifest).encode()
    path = (state if fault == "outside" else root / "effective") / "input.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    (root / "REQ.json").write_text(json.dumps({
        "request_id": "REQ", "project_id": "default", "status": "running",
        "workflow_input_manifest_ref": str(path),
        "workflow_input_manifest_digest": "wrong" if fault == "digest" else hashlib.sha256(body).hexdigest(),
    }))
    detail = client.get("/api/projects/default/workflow-requests/REQ").json()
    assert detail["result"]["task_id"] == ("" if fault else "T1")
    items = client.get("/api/projects/default/workflow-requests").json()["items"]
    assert next(row for row in items if row["request_id"] == "REQ")["task_id"] == ("" if fault else "T1")
