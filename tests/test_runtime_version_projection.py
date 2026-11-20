from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.runtime_version_projection import runtime_version_snapshot


def loaded(state, revision="old"):
    ref = write_sidecar_json(state, "diagnostics/runtime-load/test.json", {
        "schema_version": "runtime-load.v1", "source_revision": revision,
        "source_root": "/old/check-out", "pid": 123,
    }, kind="runtime-load", schema_version="runtime-load.v1", created_by="zf-cli", required=True)
    return ZfEvent(type="loop.started", origin="kernel", payload={"runtime_load_ref": ref})


def test_updated_checkout_does_not_claim_old_process_loaded_new_code(tmp_path, monkeypatch):
    monkeypatch.setattr("zf.runtime.runtime_version_projection._source_revision", lambda: "new")
    result = runtime_version_snapshot(tmp_path, [loaded(tmp_path)])
    assert result["source_revision"] == "new"
    assert result["loaded_revision"] == "old"
    assert result["comparison"] == "different"
    assert result["event_id"]


def test_missing_or_corrupt_latest_receipt_never_falls_back_to_old_success(tmp_path, monkeypatch):
    monkeypatch.setattr("zf.runtime.runtime_version_projection._source_revision", lambda: "new")
    first = loaded(tmp_path, "new")
    assert runtime_version_snapshot(tmp_path, [first, ZfEvent(type="loop.started")])["comparison"] == "unknown"
    (tmp_path / first.payload["runtime_load_ref"]["ref"]).write_text("{}")
    assert runtime_version_snapshot(tmp_path, [first])["comparison"] == "unknown"


def test_stopped_controller_keeps_last_loaded_version_without_claiming_running(tmp_path, monkeypatch):
    monkeypatch.setattr("zf.runtime.runtime_version_projection._source_revision", lambda: "new")
    result = runtime_version_snapshot(tmp_path, [loaded(tmp_path), ZfEvent(type="loop.stopped")])
    assert result["lifecycle"] == "stopped"
    assert result["loaded_revision"] == "old"


def test_dashboard_incremental_pulse_exposes_same_version_receipt(tmp_path, monkeypatch):
    from zf.core.events.log import EventLog
    from zf.web.projections.metric_views import pulse

    monkeypatch.setattr("zf.runtime.runtime_version_projection._source_revision", lambda: "new")
    (tmp_path / "kanban.json").write_text("[]")
    EventLog(tmp_path / "events.jsonl").append(loaded(tmp_path))
    assert pulse(tmp_path)["run_pulse"]["versions"]["comparison"] == "different"
