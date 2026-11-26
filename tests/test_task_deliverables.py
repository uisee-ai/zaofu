import hashlib
import json

from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.workspace import stable_project_id
from zf.web.server import create_app


def test_only_verified_files_can_be_opened_from_their_task(tmp_path):
    state = tmp_path / "runtime"
    (state / "refs").mkdir(parents=True)
    TaskStore(state / "kanban.json").add(Task(id="T1", title="Review"))
    EventLog(state / "events.jsonl").append(ZfEvent(type="task.created", actor="planner", task_id="T1"))
    report = tmp_path / "report.md"
    report.write_text("Reviewed\n")
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    ref = {"status": "accepted", "path": "report.md", "sha256": digest}
    checked = {"status": "ok", "path": "report.md", "expected_sha256": digest, "resolved_path": str(report)}
    index = state / "refs/task-index.json"
    index.write_text(json.dumps({"T1": {"artifact_refs": [ref], "hash_status": [checked]}}))

    with TestClient(create_app(state, project_root=tmp_path)) as client:
        project_id = stable_project_id(name="test", root=tmp_path)
        base = f"/api/projects/{project_id}/tasks/T1"
        result = client.get(base)
        assert result.status_code == 200, result.text
        assert result.json()["deliverables"] == [{"name": "report.md", "sha256": digest}]
        url = f"{base}/deliverables/{digest}"
        response = client.get(url)
        assert response.status_code == 200
        assert response.text == report.read_text()
        assert response.headers["content-type"].startswith("text/plain")
        assert client.get(url.replace("/T1/", "/T2/")).status_code == 404
        assert client.get(url.replace(f"/{project_id}/", "/unregistered-project/")).status_code == 404
        report.write_text("Changed\n")
        assert client.get(url).status_code == 409
        report.unlink()
        assert client.get(base).json()["deliverables"] == []
        assert client.get(url).status_code == 404
        outside = tmp_path.parent / "other-report.md"
        outside.write_text("Reviewed\n")
        report.symlink_to(outside)
        assert client.get(url).status_code == 404
        report.unlink()
        report.write_text("Reviewed\n")
        ref["status"] = "superseded"
        index.write_text(json.dumps({"T1": {"artifact_refs": [ref], "hash_status": [checked]}}))
        assert client.get(base).json()["deliverables"] == []
        assert client.get(url).status_code == 404


def test_deliverable_label_uses_task_redaction_and_download_stays_digest_bound(tmp_path):
    from zf.core.security.redaction import redact_text

    state = tmp_path / "runtime"
    (state / "refs").mkdir(parents=True)
    TaskStore(state / "kanban.json").add(Task(id="T1", title="Review"))
    EventLog(state / "events.jsonl").append(ZfEvent(type="task.created", actor="planner", task_id="T1"))
    report = tmp_path / "ACCESS_TOKEN=fixture.md"
    report.write_text("Reviewed\n")
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    ref = {"status": "accepted", "path": report.name, "sha256": digest}
    checked = {"status": "ok", "path": report.name, "expected_sha256": digest, "resolved_path": str(report)}
    (state / "refs/task-index.json").write_text(json.dumps({"T1": {"artifact_refs": [ref], "hash_status": [checked]}}))
    base = f"/api/projects/{stable_project_id(name='test', root=tmp_path)}/tasks/T1"

    with TestClient(create_app(state, project_root=tmp_path)) as client:
        detail = client.get(base)
        assert detail.status_code == 200
        assert report.name not in detail.text
        assert detail.json()["deliverables"] == [{"name": redact_text(report.name), "sha256": digest}]
        response = client.get(f"{base}/deliverables/{digest}")
        assert response.status_code == 200 and response.text == report.read_text()
