from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.operator_plan_preview import build_plan_preview
from tests.test_writer_fanout_runtime import _approval_orch


def _artifacts(tmp_path: Path):
    state = tmp_path / "runtime"
    (state / "artifacts").mkdir(parents=True)
    (state / "kanban.json").write_text("[]\n")
    (state / "feature_list.json").write_text("[]\n")
    full = "# Complete plan\n\nArchitecture\n\n" + "Business rule\n" * 1000 + "END-OF-PLAN\n"
    (state / "artifacts/plan.md").write_text(full)
    (state / "artifacts/digest.md").write_text("# Digest\nMechanical checks only\n")
    (state / "artifacts/tasks.json").write_text(json.dumps({"tasks": []}))
    payload = {
        "plan_id": "evt-plan", "task_map_ref": "artifacts/tasks.json",
        "plan_artifact_ref": "artifacts/plan.md", "digest_ref": "artifacts/digest.md",
        "plan_artifact_digest": hashlib.sha256(full.encode()).hexdigest(),
    }
    return state, full, payload


def _preview(state, payload, *prior):
    request = ZfEvent(type="plan.approval.requested", actor="zf-cli", payload=payload)
    return build_plan_preview(state, iter([*prior, request]), plan_id="evt-plan", project_root=state.parent)


def test_full_plan_is_primary_and_digest_is_separate(tmp_path):
    state, full, payload = _artifacts(tmp_path)
    result = _preview(state, payload)
    assert result["markdown"] == full
    assert result["content_kind"] == "full_plan"
    assert result["refs"]["markdown_ref"] == payload["plan_artifact_ref"]
    assert result["digest_markdown"].startswith("# Digest")


def test_legacy_request_recovers_only_its_exact_trigger(tmp_path):
    state, full, payload = _artifacts(tmp_path)
    trigger = ZfEvent(id="evt-plan", type="task_map.ready", actor="planner", payload=dict(payload))
    payload.pop("plan_artifact_ref")
    payload.pop("plan_artifact_digest")
    result = _preview(state, payload, trigger)
    assert result["markdown"] == full
    assert result["source_event_id"] == trigger.id


@pytest.mark.parametrize("mismatch", ["event_id", "task_map_ref", "pdd_id"])
def test_legacy_request_does_not_borrow_other_plan(tmp_path, mismatch):
    state, _, payload = _artifacts(tmp_path)
    source = {**payload, "pdd_id": "PDD-1"}
    payload["pdd_id"] = "PDD-1"
    event_id = "evt-plan"
    if mismatch == "event_id":
        event_id = "other-plan"
    else:
        source[mismatch] = "different"
    trigger = ZfEvent(id=event_id, type="task_map.ready", actor="planner", payload=source)
    payload.pop("plan_artifact_ref")
    payload.pop("plan_artifact_digest")
    result = _preview(state, payload, trigger)
    assert result["content_kind"] == "digest_only"
    assert "END-OF-PLAN" not in result["markdown"]


@pytest.mark.parametrize("failure", ["missing", "digest", "outside", "symlink", "binary"])
def test_declared_full_plan_failure_never_silently_becomes_digest(tmp_path, failure):
    state, _, payload = _artifacts(tmp_path)
    plan = state / payload["plan_artifact_ref"]
    if failure == "missing":
        plan.unlink()
    elif failure == "digest":
        plan.write_text("changed after approval request")
    elif failure == "binary":
        plan.write_bytes(b"\xff\xfe")
    else:
        outside = tmp_path.parent / f"{tmp_path.name}-other-project.md"
        outside.write_text("PRIVATE-OTHER-PROJECT")
        if failure == "outside":
            payload["plan_artifact_ref"] = str(outside)
        else:
            plan.unlink()
            plan.symlink_to(outside)
    result = _preview(state, payload)
    assert result["content_kind"] == "unavailable"
    assert result["content_error"]
    assert "PRIVATE-OTHER-PROJECT" not in result["markdown"]
    assert result["markdown"] != result["digest_markdown"]


def test_real_approval_request_preserves_full_plan_and_preview_is_read_only(tmp_path):
    state, log, orch = _approval_orch(tmp_path, enabled=True)
    plan = state / "artifacts/complete-plan.md"
    plan.write_text("# Architecture and business coverage\nEND-OF-PLAN\n")
    trigger = ZfEvent(type="task_map.ready", actor="zf-cli", correlation_id="trace-1", payload={
        "pdd_id": "F-11111111", "plan_artifact_ref": "artifacts/complete-plan.md",
    })
    EventWriter(log).append(trigger)
    orch.run_once(events=[trigger])
    requests = [e for e in log.read_all() if e.type == "plan.approval.requested"]
    assert len(requests) == 1
    assert requests[0].payload["plan_artifact_ref"] == "artifacts/complete-plan.md"
    assert requests[0].payload["plan_artifact_digest"] == hashlib.sha256(plan.read_bytes()).hexdigest()
    before = (state / "events.jsonl").read_bytes()
    result = build_plan_preview(state, log.read_all(), plan_id=trigger.id, project_root=tmp_path)
    assert result["markdown"] == plan.read_text()
    assert (state / "events.jsonl").read_bytes() == before
    assert not any(e.type in {"plan.approved", "fanout.started"} for e in log.read_all())


def test_plan_preview_real_api_preserves_complete_body(tmp_path):
    from fastapi.testclient import TestClient
    from zf.core.events.log import EventLog
    from zf.web.server import create_app

    state, full, payload = _artifacts(tmp_path)
    log = EventLog(state / "events.jsonl")
    EventWriter(log).append(ZfEvent(type="plan.approval.requested", actor="zf-cli", payload=payload))
    client = TestClient(create_app(state, project_root=tmp_path))
    project = client.get("/api/snapshot").json()["project"]["project_id"]
    before = (state / "events.jsonl").read_bytes()
    response = client.get(f"/api/projects/{project}/plans/evt-plan/preview")
    assert response.status_code == 200
    assert response.json()["markdown"] == full
    assert client.get(f"/api/projects/{project}/plans/another-plan/preview").status_code == 404
    assert (state / "events.jsonl").read_bytes() == before


def test_package_attachment_is_plan_bound_and_digest_checked_via_api(tmp_path):
    from fastapi.testclient import TestClient
    from zf.core.events.log import EventLog
    from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
    from tests.test_plan_artifact_package import _fixture_port, _run_contract
    from zf.web.server import create_app

    state, _, payload = _artifacts(tmp_path)
    port = _fixture_port(state, "acceptance_matrix", "END-OF-MATRIX")
    package = build_plan_artifact_package(
        workflow_run_id="run-1", flow_kind="prd", producer_stage_id="prd-plan",
        run_contract=_run_contract(state), plan_revision="r1", task_map_generation="g1",
        produced=[port], required_ports=["acceptance_matrix"],
    )
    descriptor = write_plan_artifact_package(state, package)
    payload.update({"plan_artifact_package_ref": descriptor["ref"], "plan_artifact_package_digest": descriptor["sha256"]})
    log = EventLog(state / "events.jsonl")
    EventWriter(log).append(ZfEvent(type="plan.approval.requested", actor="zf-cli", payload=payload))
    client = TestClient(create_app(state, project_root=tmp_path))
    project = client.get("/api/snapshot").json()["project"]["project_id"]
    base = f"/api/projects/{project}/plans/evt-plan"
    before = (state / "events.jsonl").read_bytes()
    assert client.get(base + "/preview").json()["artifacts"][0]["name"] == "acceptance_matrix"
    response = client.get(base + "/artifacts/acceptance_matrix")
    assert response.status_code == 200 and "END-OF-MATRIX" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(base + "/artifacts/not-bound").status_code == 404
    assert client.get(base.replace("evt-plan", "other-plan") + "/artifacts/acceptance_matrix").status_code == 404
    (state / port["ref"]).write_text("changed matrix")
    assert client.get(base + "/artifacts/acceptance_matrix").status_code == 404
    (state / descriptor["ref"]).write_text("changed package")
    preview = client.get(base + "/preview").json()
    assert not preview["artifacts"] and "digest mismatch" in preview["artifacts_error"]
    assert (state / "events.jsonl").read_bytes() == before


def test_current_digest_uses_bound_task_commands_without_rewriting_history(tmp_path):
    from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
    from tests.test_plan_artifact_package import _run_contract

    state, full, payload = _artifacts(tmp_path)
    task_map = json.dumps({"tasks": [{
        "task_id": "SLICE-1", "allowed_paths": ["src/feature.py"], "wave": 1,
        "validation": {"commands": [{"id": "local", "command": "node --test tests/feature.mjs"}]},
    }]})
    (state / payload["task_map_ref"]).write_text(task_map)
    port = {
        "logical_name": "task_map", "artifact_kind": "task_map", "schema_version": "task-map.v1",
        "producer_stage_id": "plan", "ref": payload["task_map_ref"],
        "sha256": hashlib.sha256(task_map.encode()).hexdigest(),
    }
    package = build_plan_artifact_package(
        workflow_run_id="run-1", flow_kind="prd", producer_stage_id="plan",
        run_contract=_run_contract(state), plan_revision="r1", task_map_generation="g1",
        produced=[port], required_ports=["task_map"],
    )
    descriptor = write_plan_artifact_package(state, package)
    payload.update(task_map_ref=port["ref"], plan_artifact_package_ref=descriptor["ref"],
                   plan_artifact_package_digest=descriptor["sha256"])
    stored = (state / payload["digest_ref"]).read_bytes()
    result = _preview(state, payload)
    assert result["markdown"] == full
    assert "node --test tests/feature.mjs" in result["digest_markdown"]
    assert result["digest_source"] == "bound_task_map"
    assert "🔴" not in result["digest_markdown"]
    assert (state / payload["digest_ref"]).read_bytes() == stored
    (state / port["ref"]).write_text('{"tasks": []}')
    result = _preview(state, payload)
    assert result["digest_source"] == "unavailable"
    assert result["digest_markdown"] == ""
    assert "digest mismatch" in result["artifacts_error"]
