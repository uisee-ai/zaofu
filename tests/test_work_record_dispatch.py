"""Real request normalization must not erase the dispatch workspace authority."""
import json
from types import SimpleNamespace

import pytest

from zf.core.events import EventWriter, ZfEvent
from zf.runtime.worker_execution_envelope import build_worker_execution_envelope
from tests.test_work_record import setup, publish


def dispatch(log, record, *, fault=""):
    payload = {
        "workflow_run_id": "r", "task_id": "T7", "operation_id": "op",
        "attempt_id": "a", "dispatch_id": "a", "lease_id": "operation-lease",
        "task_pipeline_stage": "impl", "task_map_generation": "map-g1",
        "operation_generation": 1, "placement_epoch": 1,
        "workspace_generation": 1, "role_instance": "dev-1",
        "task_stage_session_binding": "binding",
        "workdir": str(record.parent), "source_branch": "task/T7",
        "base_commit": "a" * 40, "target_ref": "a" * 40,
        "canonical_success_event": "dev.build.done", "canonical_failure_event": "dev.blocked",
        "output_profile_id": "implementation", "output_profile_revision": "1",
    }
    role = SimpleNamespace(instance_id="dev-1", name="dev", backend="mock", skills=[])
    profile = SimpleNamespace(profile_id="bounded-direct-v1", profile_digest="profile")
    payload["worker_execution_envelope"] = build_worker_execution_envelope(
        payload=payload, role=role, execution_profile=profile,
    )
    if fault in {"attempt_id", "lease_id", "role_instance", "workflow_run_id", "task_id",
                 "task_map_generation", "placement_epoch", "workspace_generation", "task_stage_session_binding"}:
        payload[fault] = "stale"
    if fault == "digest":
        payload["worker_execution_envelope"]["workspace"]["workdir"] = "/tmp/foreign"
    if fault == "directory":
        payload["workdir"] = "/tmp/foreign"
    if fault == "missing_digest":
        payload["worker_execution_envelope"].pop("envelope_digest")
    EventWriter(log).append(ZfEvent(
        type="task.pipeline.stage.dispatched", actor="orchestrator",
        origin="worker" if fault == "origin" else "kernel", task_id="T7", payload=payload,
    ))
    return payload


def test_normalized_request_can_publish_and_show_via_public_cli(tmp_path, monkeypatch, capsys):
    from zf.cli.main import main
    state = tmp_path / "state"
    manifest, task, log, record, token = setup(state, canonical_request=True)
    dispatch(log, record)
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "dev-1")
    monkeypatch.setenv("ZF_RESULT_SUBMIT_TOKEN", token)
    assert main(["artifact", "work-record", "publish", "--operation", "op", "--file", str(record),
                 "--request-id", "one", "--state-dir", str(state)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["artifact", "work-record", "show", "--operation", "op", "--state-dir", str(state)]) == 0
    assert json.loads(capsys.readouterr().out)["sha256"] == first["work_record_ref"]["sha256"]
    assert publish(state, log, record, token)["replayed"]


@pytest.mark.parametrize("fault", ["attempt_id", "lease_id", "role_instance", "workflow_run_id",
                                  "task_id", "origin", "digest", "directory", "missing_digest",
                                  "task_map_generation", "placement_epoch", "workspace_generation",
                                  "task_stage_session_binding"])
def test_dispatch_workdir_rejects_untrusted_or_stale_binding(tmp_path, fault):
    state = tmp_path / "state"
    _, _, log, record, token = setup(state, canonical_request=True)
    dispatch(log, record, fault=fault)
    with pytest.raises(ValueError):
        publish(state, log, record, token)
    assert not any(e.type == "worker.work_record.published" for e in log.read_all())


def test_newer_conflicting_dispatch_cannot_resurrect_old_directory(tmp_path):
    state = tmp_path / "state"
    _, _, log, record, token = setup(state, canonical_request=True)
    dispatch(log, record)
    dispatch(log, record, fault="directory")
    with pytest.raises(ValueError):
        publish(state, log, record, token)
