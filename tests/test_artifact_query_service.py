from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.artifact_query.handoff import CanonicalHandoffResolver
from zf.runtime.artifact_query import store as artifact_query_store
from zf.runtime.artifact_query.service import (
    ArtifactQueryError,
    ArtifactQueryService,
)
from zf.runtime.artifact_query.store import projection_db_path
from zf.runtime.artifact_read_ledger import (
    ArtifactReadError,
    build_attempt_source_manifest,
    read_attempt_artifact,
)
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package,
    package_event_payload,
    write_plan_artifact_package,
)
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_contract_snapshot import current_task_contract_identity
from zf.runtime.task_contract_snapshot import (
    build_task_contract_snapshot,
    write_task_contract_snapshot,
)


def _service(tmp_path: Path) -> tuple[Path, Path, ArtifactQueryService]:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    return (
        project_root,
        state_dir,
        ArtifactQueryService(
            state_dir=state_dir,
            project_root=project_root,
        ),
    )


def _append(
    state_dir: Path,
    *,
    event_id: str,
    descriptor: dict,
    task_id: str = "T1",
    run_id: str = "run-1",
    attempt_id: str = "attempt-1",
) -> None:
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        id=event_id,
        type="artifact.published",
        actor="dev-1",
        task_id=task_id,
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "attempt_id": attempt_id,
            "artifact_ref": descriptor,
        },
    ))


def test_catalog_keeps_occurrence_authorization_separate_and_rebuilds(
    tmp_path: Path,
) -> None:
    project_root, state_dir, service = _service(tmp_path)
    descriptor = write_sidecar_json(
        state_dir,
        "artifacts/shared.json",
        {"value": "same content"},
        kind="contract_snapshot",
        schema_version="test.contract.v1",
        created_by="dev-1",
        access_scope={
            "visibility": "project",
            "actor": "worker-a",
            "purpose": "implementation",
        },
    )
    _append(
        state_dir,
        event_id="evt-a",
        descriptor=descriptor,
    )
    restricted = {
        **descriptor,
        "access_scope": {
            "visibility": "project",
            "actor": "worker-b",
            "purpose": "implementation",
        },
    }
    _append(
        state_dir,
        event_id="evt-b",
        descriptor=restricted,
    )
    second_locator = write_sidecar_json(
        state_dir,
        "artifacts/shared-copy.json",
        {"value": "same content"},
        kind="contract_snapshot",
        schema_version="test.contract.v1",
        created_by="dev-1",
    )
    _append(
        state_dir,
        event_id="evt-copy",
        descriptor=second_locator,
    )
    context = service.context(
        actor="worker-a",
        role="dev",
        purpose="implementation",
    )

    first = service.catalog_list(context=context, task_id="T1")
    assert first["projection_state"] == "ready"
    assert len(first["items"]) == 3
    assert len({row["object_id"] for row in first["items"]}) == 1
    assert len({row["locator_id"] for row in first["items"]}) == 2
    assert len({row["occurrence_id"] for row in first["items"]}) == 3
    assert {
        row["event_id"]: row["authorized"]
        for row in first["items"]
    } == {
        "evt-a": True,
        "evt-b": False,
        "evt-copy": True,
    }
    with pytest.raises(ArtifactQueryError):
        service.hydrate(
            next(
                row["occurrence_id"]
                for row in first["items"]
                if row["event_id"] == "evt-b"
            ),
            context=context,
        )
    with pytest.raises(ArtifactQueryError, match="exact occurrence"):
        service.hydrate(first["items"][0]["object_id"], context=context)

    identities = {
        (
            row["object_id"],
            row["locator_id"],
            row["occurrence_id"],
        )
        for row in first["items"]
    }
    for path in projection_db_path(state_dir).parent.glob("read_model.sqlite*"):
        path.unlink()
    rebuilt = service.catalog_list(context=context, task_id="T1")
    assert {
        (
            row["object_id"],
            row["locator_id"],
            row["occurrence_id"],
        )
        for row in rebuilt["items"]
    } == identities
    assert rebuilt["source_snapshot"]["event_cursor"]["projected_seq"] == 3
    assert "session_store_digest" in rebuilt["source_snapshot"]
    assert str(project_root) == rebuilt["items"][0]["project_scope"]
    bounded = service.catalog_list(
        context=service.context(limit=1),
        task_id="T1",
    )
    assert len(bounded["items"]) == 1
    assert bounded["has_more"] is True


def test_catalog_corruption_uses_canonical_fallback_without_semantic_state(
    tmp_path: Path,
) -> None:
    _, state_dir, service = _service(tmp_path)
    descriptor = write_sidecar_json(
        state_dir,
        "artifacts/result.json",
        {"status": "passed"},
        kind="verification_result",
        schema_version="test.result.v1",
        created_by="verify-1",
    )
    _append(
        state_dir,
        event_id="evt-result",
        descriptor=descriptor,
    )
    context = service.context(mode="canonical")
    assert service.catalog_list(context=context)["fallback"]["used"] is False

    db_path = projection_db_path(state_dir)
    db_path.write_bytes(b"not a sqlite database")
    result = service.catalog_list(context=context)

    assert result["fallback"] == {
        "used": True,
        "source": "event-log-descriptor-scan",
    }
    assert result["projection_state"] == "degraded"
    assert result["items"][0]["event_id"] == "evt-result"
    assert not (state_dir / "kanban.json").exists()


def test_attempt_missing_reads_is_protocol_repair_then_closes(
    tmp_path: Path,
) -> None:
    _, state_dir, service = _service(tmp_path)
    source = state_dir / "artifacts" / "inputs" / "contract.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"acceptance": ["AC-1"]}), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-read",
        task_id="T-read",
        attempt_id="attempt-read",
        dispatch_id="dispatch-read",
        sources=[{
            "source_id": "contract",
            "artifact_id": "contract.json",
            "kind": "task_contract_snapshot",
            "ref": "artifacts/inputs/contract.json",
            "sha256": digest,
            "allowed_paths": ["$"],
        }],
    )
    required = {
        "source_id": "contract",
        "artifact_id": "contract.json",
        "artifact_sha256": digest,
        "json_path": "$",
    }
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        id="evt-dispatch",
        type="task.dispatched",
        task_id="T-read",
        correlation_id="run-read",
        payload={
            "workflow_run_id": "run-read",
            "attempt_id": "attempt-read",
            "dispatch_id": "dispatch-read",
            "attempt_domain": "task_rework",
            "required_reads": [required],
        },
    ))
    context = service.context(actor="operator")

    missing = service.attempt_missing_reads("attempt-read", context=context)
    assert missing["protocol_repair_required"] is True
    assert missing["semantic_rework_required"] is False
    assert missing["missing_reads"] == [required]

    read_attempt_artifact(
        state_dir,
        manifest=manifest,
        source_id="contract",
        artifact_id="contract.json",
        json_path="$",
        actor="dev-1",
        role="dev",
        provider="codex",
    )
    closed = service.attempt_missing_reads("attempt-read", context=context)
    assert closed["protocol_repair_required"] is False
    assert closed["missing_reads"] == []
    inspected = service.attempt_inspect("attempt-read", context=context)
    assert inspected["attempt_domain"] == "task_rework"
    assert inspected["read_count"] == 1


def test_goal_dossier_cache_invalidates_on_source_snapshot_change(
    tmp_path: Path,
) -> None:
    _, state_dir, service = _service(tmp_path)
    calls: list[int] = []

    def build() -> dict:
        calls.append(len(calls) + 1)
        return {"schema_version": "goal-dossier.v1", "build": len(calls)}

    first = service.cached_goal_dossier("run-cache", builder=build)
    second = service.cached_goal_dossier("run-cache", builder=build)
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert calls == [1]

    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        id="evt-cache-change",
        type="task.created",
        task_id="T-cache",
    ))
    third = service.cached_goal_dossier("run-cache", builder=build)
    assert third["cache"]["hit"] is False
    assert calls == [1, 2]


def test_reducer_projection_cache_enforces_entry_and_payload_bounds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, state_dir, _ = _service(tmp_path)
    monkeypatch.setattr(
        artifact_query_store,
        "MAX_REDUCER_PROJECTIONS",
        2,
    )
    for index in range(3):
        artifact_query_store.set_reducer_projection(
            state_dir,
            projection_kind="bounded",
            subject_id=f"subject-{index}",
            source_snapshot_key=f"snapshot-{index}",
            source_seq=index,
            reducer_version="test.v1",
            payload={"index": index},
        )
    with artifact_query_store.connect_projection_db(state_dir) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM artifact_reducer_projection"
        ).fetchone()[0]
    assert count == 2

    monkeypatch.setattr(
        artifact_query_store,
        "MAX_REDUCER_PAYLOAD_BYTES",
        10,
    )
    artifact_query_store.set_reducer_projection(
        state_dir,
        projection_kind="oversized",
        subject_id="subject-large",
        source_snapshot_key="snapshot-large",
        source_seq=4,
        reducer_version="test.v1",
        payload={"body": "x" * 100},
    )
    assert artifact_query_store.get_reducer_projection(
        state_dir,
        projection_kind="oversized",
        subject_id="subject-large",
        source_snapshot_key="snapshot-large",
    ) is None


def test_handoff_resolver_rejects_stale_or_missing_task_authority(
    tmp_path: Path,
) -> None:
    project_root, state_dir, _ = _service(tmp_path)
    task = Task(
        id="T-current",
        title="Current task",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(
            behavior="deliver current contract",
            source_ref="artifacts/task-maps/g2.json",
            acceptance_criteria=["AC sentinel R2"],
            verification="python -m pytest -q",
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    current = current_task_contract_identity(task)
    refs = state_dir / "refs"
    refs.mkdir()
    (refs / "task-index.json").write_text(
        json.dumps({
            "T-current": {
                "task_ref": "refs/zf/tasks/T-current",
            },
        }),
        encoding="utf-8",
    )
    resolver = CanonicalHandoffResolver(
        state_dir=state_dir,
        project_root=project_root,
        config=None,
    )
    snapshot = build_task_contract_snapshot(
        task,
        workflow_run_id="run-current",
        task_map_generation_id=current["task_map_generation"],
        base_commit="base-1",
        task_ref="refs/zf/tasks/T-current",
    )
    snapshot_ref = write_task_contract_snapshot(state_dir, snapshot)
    base = {
        **current,
        "task_ref": "refs/zf/tasks/T-current",
        "base_commit": "base-1",
        "output_profile_id": "implementation",
        "contract_snapshot_ref": snapshot_ref["ref"],
        "contract_snapshot_digest": snapshot_ref["sha256"],
    }

    manifest, descriptor = resolver.resolve_payload(
        payload=base,
        workflow_run_id="run-current",
        task_id="T-current",
        attempt_id="attempt-current",
        dispatch_id="dispatch-current",
    )
    assert manifest["contract_revision"] == current["contract_revision"]
    assert manifest["task_map_generation"] == current["task_map_generation"]
    assert manifest["task_ref"] == "refs/zf/tasks/T-current"
    assert manifest["resolver"]["schema_version"] == (
        "canonical-handoff-resolver.v1"
    )
    assert descriptor["kind"] == "attempt_source_manifest"

    with pytest.raises(ArtifactReadError, match="contract_revision"):
        resolver.resolve_payload(
            payload={**base, "contract_revision": "contract-r1"},
            workflow_run_id="run-current",
            task_id="T-current",
            attempt_id="attempt-stale",
            dispatch_id="dispatch-stale",
        )
    with pytest.raises(ArtifactReadError, match="task_map_generation"):
        resolver.resolve_payload(
            payload={
                key: value
                for key, value in base.items()
                if key != "task_map_generation"
            },
            workflow_run_id="run-current",
            task_id="T-current",
            attempt_id="attempt-missing",
            dispatch_id="dispatch-missing",
        )
    with pytest.raises(ArtifactReadError, match="contract snapshot"):
        resolver.resolve_payload(
            payload={
                **base,
                "contract_snapshot_digest": "0" * 64,
            },
            workflow_run_id="run-current",
            task_id="T-current",
            attempt_id="attempt-hash-mismatch",
            dispatch_id="dispatch-hash-mismatch",
        )


def test_handoff_resolver_materializes_current_required_plan_ports(
    tmp_path: Path,
) -> None:
    project_root, state_dir, _ = _service(tmp_path)
    run_contract_body = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    }
    run_contract = write_run_contract_snapshot(
        state_dir,
        {
            **run_contract_body,
            "contract_digest": stable_json_sha256(run_contract_body),
        },
    )
    ports = []
    for logical_name in ("acceptance_matrix", "test_matrix"):
        descriptor = write_immutable_json_sidecar(
            state_dir,
            {"schema_version": f"{logical_name}.v1", "rows": [{"id": "AC-1"}]},
            root=f"fixtures/{logical_name}",
            kind=logical_name,
            schema_version=f"{logical_name}.v1",
            created_by="test",
        )
        ports.append({
            "logical_name": logical_name,
            "artifact_kind": logical_name,
            "schema_version": f"{logical_name}.v1",
            "producer_stage_id": "prd-plan",
            "ref": descriptor["ref"],
            "sha256": descriptor["sha256"],
        })
    package = build_plan_artifact_package(
        workflow_run_id="run-ports",
        flow_kind="prd",
        producer_stage_id="prd-plan",
        run_contract=run_contract,
        plan_revision="R2",
        task_map_generation="G2",
        produced=ports,
        required_ports=["acceptance_matrix", "test_matrix"],
    )
    package_descriptor = write_plan_artifact_package(state_dir, package)
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="plan.artifact_package.admitted",
        correlation_id="run-ports",
        payload=package_event_payload(
            package,
            package_descriptor,
            status="admitted",
        ),
    ))
    task = Task(
        id="T-ports",
        title="Read current plan ports",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(
            behavior="implement against current matrices",
            acceptance_criteria=["AC-1"],
            verification="python -m pytest -q",
            evidence_contract={
                "required_plan_ports": [
                    "acceptance_matrix",
                    "test_matrix",
                ],
                "source_refs": {
                    "task_map_ref": "artifacts/task-maps/g2.json",
                    "task_map_generation": "G2",
                    "plan_artifact_package_id": package_descriptor["package_id"],
                    "plan_artifact_package_ref": package_descriptor["ref"],
                    "plan_artifact_package_digest": package_descriptor["sha256"],
                },
            },
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    refs = state_dir / "refs"
    refs.mkdir()
    (refs / "task-index.json").write_text(
        json.dumps({"T-ports": {"task_ref": "refs/zf/tasks/T-ports"}}),
        encoding="utf-8",
    )
    current = current_task_contract_identity(task)
    snapshot = build_task_contract_snapshot(
        task,
        workflow_run_id="run-ports",
        task_map_generation_id="G2",
        base_commit="base-ports",
        task_ref="refs/zf/tasks/T-ports",
    )
    snapshot_ref = write_task_contract_snapshot(state_dir, snapshot)
    resolver = CanonicalHandoffResolver(
        state_dir=state_dir,
        project_root=project_root,
        config=None,
    )

    manifest, _ = resolver.resolve_payload(
        payload={
            **current,
            "workflow_run_id": "run-ports",
            "task_ref": "refs/zf/tasks/T-ports",
            "base_commit": "base-ports",
            "output_profile_id": "implementation",
            "plan_artifact_package_id": package_descriptor["package_id"],
            "plan_artifact_package_ref": package_descriptor["ref"],
            "plan_artifact_package_digest": package_descriptor["sha256"],
            "contract_snapshot_ref": snapshot_ref["ref"],
            "contract_snapshot_digest": snapshot_ref["sha256"],
        },
        workflow_run_id="run-ports",
        task_id="T-ports",
        attempt_id="attempt-ports",
        dispatch_id="dispatch-ports",
    )

    sources = {source["source_id"]: source for source in manifest["sources"]}
    assert {
        "plan-port-acceptance_matrix",
        "plan-port-test_matrix",
    } <= set(sources)
    assert manifest["plan_artifact_package_ref"] == package_descriptor["ref"]
    assert manifest["plan_artifact_package_digest"] == package_descriptor["sha256"]

    tampered = state_dir / ports[0]["ref"]
    tampered.write_text('{"tampered": true}\n', encoding="utf-8")
    with pytest.raises(
        ArtifactReadError,
        match="current Plan Artifact Package cannot be hydrated",
    ):
        resolver.resolve_payload(
            payload={
                **current,
                "workflow_run_id": "run-ports",
                "task_ref": "refs/zf/tasks/T-ports",
                "base_commit": "base-ports",
                "output_profile_id": "implementation",
                "plan_artifact_package_id": package_descriptor["package_id"],
                "plan_artifact_package_ref": package_descriptor["ref"],
                "plan_artifact_package_digest": package_descriptor["sha256"],
                "contract_snapshot_ref": snapshot_ref["ref"],
                "contract_snapshot_digest": snapshot_ref["sha256"],
            },
            workflow_run_id="run-ports",
            task_id="T-ports",
            attempt_id="attempt-tampered-port",
            dispatch_id="dispatch-tampered-port",
        )
