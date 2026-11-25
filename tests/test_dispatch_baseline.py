"""Bind the inspected release to the first delivery even after later successful work."""

from dataclasses import replace

import pytest

from tests.test_scheduler_task_attempt import _runtime, _send
from zf.cli.artifact_input_index import run_input_index
from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events import ZfEvent
from zf.core.security.hash import sha256_file
from zf.runtime.artifact_read_capability import (
    authorize_artifact_read_from_environment,
    canonical_source_manifest_digest,
)
from zf.runtime.artifact_read_ledger import write_attempt_source_manifest
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.dispatch_baseline import first_dispatch_baseline
from zf.runtime.load_receipt import consumer_receipts, runtime_load_payload
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.worker_load_receipt import record_worker_cli_load
from zf.runtime.workflow_operation import operation_request_hash


def test_normal_transport_binds_the_card_observed_at_send(tmp_path):
    runtime, transport, state = _runtime(tmp_path)
    runtime._skill_materialization_events = {"dev": ("TASK-1", "material-selected")}
    _send(runtime, state, dispatch_id="disp-1")
    (sent,) = [e for e in runtime.event_log.read_all() if e.type == "task.attempt.sent"]
    from zf.core.security.hash import sha256_text

    assert sent.payload["briefing_digest"] == sha256_text(transport.observed_briefing)
    assert sent.payload["role_instance"] == "dev"
    assert sent.payload["skill_materialization_event_id"] == "material-selected"


def projection(tmp_path):
    project = tmp_path
    state = project / "state"
    state.mkdir()
    (project / "zf.yaml").write_text("# configured fixture\n")
    skill = project / "skills" / "audit" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: audit\ndescription: Verify results.\n---\nAudit\n")
    cfg = ZfConfig(
        project=ProjectConfig(name="baseline", state_dir="state"),
        roles=[RoleConfig(name="reader", instance_id="reader", skills=["audit"])],
    )

    def side(body):
        return write_immutable_json_sidecar(
            state,
            body,
            root="qualification",
            kind="fixture",
            schema_version=body["schema_version"],
            created_by="test",
        )

    def ev(typ, p, **kw):
        return ZfEvent(type=typ, actor=kw.pop("actor", "zf-cli"), origin="kernel", task_id="T", payload=p, **kw)

    boot = ev("loop.started", runtime_load_payload(config=cfg, state_dir=state, project_root=project))
    rev = hydrate_sidecar_ref(state, boot.payload["runtime_load_ref"]).payload
    manifest = {
        "schema_version": "attempt-source-manifest.v1",
        "workflow_run_id": "run",
        "task_id": "T",
        "attempt_id": "disp",
        "dispatch_id": "disp",
        "sources": [],
    }
    raw_digest = write_attempt_source_manifest(state, manifest)["sha256"]
    worker_digest = canonical_source_manifest_digest(manifest)
    assert raw_digest != worker_digest
    request = {
        "schema_version": "workflow-operation-request.v1",
        "task_id": "T",
        "role_instance": "reader",
        "workflow_run_id": "run",
        "operation_id": "op",
        "active_attempt_id": "disp",
        "request": {"skills": ["audit"], "source_manifest_digest": raw_digest},
    }
    issued = ev(
        "workflow.operation.requested",
        {**request, "request_ref": side(request), "request_hash": operation_request_hash(request)},
    )
    material = side(
        {
            "schema_version": "skill-dispatch-treatment.v1",
            "role_instance": "reader",
            "task_id": "T",
            "materialized_skills": [
                {
                    "skill_name": "audit",
                    "digest": sha256_file(skill),
                    "materialized_to": "reader/skills/audit",
                    "status": "resolved",
                }
            ],
        }
    )
    mat = ev(
        "skills.materialized",
        {
            "instance_id": "reader",
            "skill_dispatch_treatment_ref": material["ref"],
            "skill_dispatch_treatment_digest": material["sha256"],
        },
    )
    sent = ev(
        "task.attempt.sent",
        {
            "role_instance": "reader",
            "workflow_run_id": "run",
            "dispatch_id": "disp",
            "operation_id": "op",
            "briefing_digest": "b" * 64,
            "kernel_pid": rev["pid"],
            "runtime_epoch": rev["runtime_epoch"],
            "skill_materialization_event_id": mat.id,
        },
        actor="orchestrator",
    )
    identity = {
        "task_id": "T",
        "actor": "reader",
        "workflow_run_id": "run",
        "dispatch_id": "disp",
        "operation_id": "op",
        "attempt_id": "disp",
        "manifest_digest": worker_digest,
    }
    worker = {
        "schema_version": "worker-load.v1",
        "scope": "authenticated_worker_cli",
        "source_revision": rev["source_revision"],
        "identity": identity,
        "loaded_consumers": consumer_receipts(
            (run_input_index, authorize_artifact_read_from_environment, record_worker_cli_load)
        ),
    }
    obs = ev("worker.runtime.observed", {**identity, "worker_load_ref": side(worker)}, actor="reader")

    def check(rows, **opts):
        return first_dispatch_baseline(
            project,
            state,
            opts.get("config", cfg),
            rows,
            run_id="run",
            revision=rev["source_revision"],
            source_digest=rev["source_tree_sha256"],
            pins=opts.get("pins", {}),
        )

    return check, [boot, issued, mat, sent, obs], skill, side, worker


def test_release_and_skill_receipts_are_independently_bound(tmp_path):
    check, rows, skill, _, _ = projection(tmp_path)
    result = check(rows, pins={"audit": sha256_file(skill)})
    assert result["status"] == "verified", result
    assert result["dispatch_event"] == rows[3].id and result["worker_event"] == rows[4].id
    assert check(rows, pins={"audit": "f" * 64})["status"] == "unproven"
    drift = ZfConfig(project=ProjectConfig(name="changed-profile", state_dir="state"))
    assert "configuration" in check(rows, config=drift)["reason"]


@pytest.mark.parametrize(
    "fault",
    [
        "unbound-first",
        "other-boot",
        "wrong-attempt",
        "bad-skill",
        "old-worker-code",
        "different-cli",
        "earlier-unstamped-fanout",
        "corrupt-consumers",
    ],
)
def test_cannot_borrow_a_later_or_unbound_success(tmp_path, fault):
    check, rows, _, side, worker = projection(tmp_path)
    boot, issued, mat, sent, obs = rows
    if fault == "unbound-first":
        rows.insert(0, replace(sent, payload={**sent.payload, "role_instance": ""}, id="earlier"))
    elif fault == "earlier-unstamped-fanout":
        rows.insert(0, replace(sent, id="early-child", type="fanout.child.dispatched"))
    elif fault == "corrupt-consumers":
        rows[4] = replace(
            obs, payload={**obs.payload, "worker_load_ref": side({**worker, "loaded_consumers": []})}
        )
    elif fault == "other-boot":
        rows.insert(4, replace(boot, id="other-boot", payload={**boot.payload, "runtime_epoch": "other"}))
    elif fault == "wrong-attempt":
        rows[-1] = replace(obs, payload={**obs.payload, "dispatch_id": "other"})
    elif fault == "bad-skill":
        rows[2] = replace(mat, payload={**mat.payload, "skill_dispatch_treatment_digest": "f" * 64})
    elif fault == "old-worker-code":
        rows[4] = replace(
            obs, payload={**obs.payload, "worker_load_ref": side({**worker, "source_revision": "f" * 40})}
        )
    elif fault == "different-cli":
        rows[4] = replace(
            obs, payload={**obs.payload, "worker_load_ref": side({**worker, "loaded_consumers": {}})}
        )
    assert check(rows)["status"] == "unproven"


def test_startup_catchup_can_deliver_before_loop_started(tmp_path):
    check, rows, _, _, _ = projection(tmp_path)
    boot, issued, material, sent, worker = rows
    session = replace(boot, type="session.started", id="session")
    result = check([session, issued, material, sent, boot, worker])
    assert result["status"] == "verified", result
    assert result["boot_event"] == "session"


def test_authentic_sandbox_read_without_release_identity_reports_specific_gap(tmp_path):
    check, rows, _, side, worker = projection(tmp_path)
    event = rows[-1]
    rows[-1] = replace(
        event, payload={**event.payload, "worker_load_ref": side({**worker, "source_revision": ""})}
    )
    result = check(rows)
    assert result["worker_event"] == event.id
    assert result["status"] == "unproven" and "resolve its Git revision" in result["reason"]


def test_manifest_requires_exact_signed_bytes_and_attempt_path(tmp_path):
    check, rows, _, _, _ = projection(tmp_path)
    (original,) = (tmp_path / "state/artifacts/attempts/disp/source-manifests").glob("*.json")
    raw = original.read_bytes()
    original.write_bytes(raw + b"\n")
    assert check(rows)["status"] == "unproven"
    original.write_bytes(raw)
    wrong = tmp_path / "state/artifacts/attempts/other/source-manifests" / original.name
    wrong.parent.mkdir(parents=True)
    original.rename(wrong)
    assert check(rows)["status"] == "unproven"
