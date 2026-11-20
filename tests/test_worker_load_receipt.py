import json

from tests.test_artifact_input_recovery import current_dispatch
from tests.test_artifact_read_capability import _grant
from tests.test_artifact_read_snapshot import published
from zf.cli.main import main
from zf.core.events import EventLog, ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.runtime_version_projection import runtime_version_snapshot
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def observe(state, monkeypatch, capsys):
    manifest, required, _ = published(state)
    current_dispatch(state, required)
    _grant(state, monkeypatch, manifest=manifest, operation_id="op")
    assert main(["artifact", "index", "--attempt", "a", "--state-dir", str(state)]) == 0
    json.loads(capsys.readouterr().out)
    events = EventLog(state / "events.jsonl").read_all()
    receipt = next(e for e in events if e.type == "worker.runtime.observed")
    return events, receipt


def test_authenticated_cli_records_loaded_code_and_current_dispatch(tmp_path, monkeypatch, capsys):
    events, receipt = observe(tmp_path, monkeypatch, capsys)
    body = hydrate_sidecar_ref(tmp_path, receipt.payload["worker_load_ref"]).payload
    assert receipt.origin == "kernel" and receipt.actor == "dev-1"
    assert body["scope"] == "authenticated_worker_cli"
    assert body["identity"]["operation_id"] == "op"
    assert body["identity"]["dispatch_id"] == "a"
    assert body["loaded_consumers"] and body["source_revision"]
    assert all(len(c["loaded_code_sha256"]) == 64 for c in body["loaded_consumers"].values())
    assert "token" not in json.dumps(body)
    adoption = runtime_version_snapshot(tmp_path, events)["task_adoption"]
    assert adoption["active"] == adoption["observed"] == 1
    assert adoption["tasks"][0]["status"] == "observed"


def test_old_dispatch_and_corrupt_latest_never_claim_adoption(tmp_path, monkeypatch, capsys):
    events, receipt = observe(tmp_path, monkeypatch, capsys)
    corrupt = ZfEvent(type=receipt.type, origin="kernel", actor=receipt.actor, task_id=receipt.task_id,
                     payload={**receipt.payload, "worker_load_ref": {}})
    assert runtime_version_snapshot(tmp_path, events + [corrupt])["task_adoption"]["observed"] == 0
    TaskStore(tmp_path / "kanban.json").update("T7", active_dispatch_id="new")
    assert runtime_version_snapshot(tmp_path, events)["task_adoption"]["observed"] == 0


def test_incremental_projection_preserves_origin_and_rejects_worker_self_report(tmp_path, monkeypatch, capsys):
    from zf.web.projections.metric_views import pulse

    events, receipt = observe(tmp_path, monkeypatch, capsys)
    assert pulse(tmp_path)["run_pulse"]["versions"]["task_adoption"]["observed"] == 1
    # A payload saying origin=kernel cannot upgrade a worker-authored event.
    EventLog(tmp_path / "events.jsonl").append(ZfEvent(type=receipt.type, origin="worker",
        actor=receipt.actor, task_id=receipt.task_id, payload={**receipt.payload, "origin": "kernel"}))
    assert pulse(tmp_path)["run_pulse"]["versions"]["task_adoption"] == runtime_version_snapshot(
        tmp_path, EventLog(tmp_path / "events.jsonl").read_all())["task_adoption"]


def test_cancelled_operation_cannot_publish_cli_receipt(tmp_path, monkeypatch, capsys):
    manifest, required, _ = published(tmp_path)
    _, writer = current_dispatch(tmp_path, required)
    _grant(tmp_path, monkeypatch, manifest=manifest, operation_id="op")
    writer.append(ZfEvent(type="workflow.operation.cancelled", payload={"operation_id": "op",
        "workflow_run_id": "r", "active_attempt_id": "a"}))
    assert main(["artifact", "index", "--attempt", "a", "--state-dir", str(tmp_path)]) == 1
    assert not any(e.type == "worker.runtime.observed" for e in EventLog(tmp_path / "events.jsonl").read_all())
