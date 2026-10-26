from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.artifact_query.handoff import (
    CanonicalHandoffResolver,
    build_handoff_authority_contract,
)
from zf.runtime.artifact_query import store as artifact_query_store
from zf.runtime.artifact_query import service as artifact_query_service
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
    build_target_snapshot,
    build_task_contract_snapshot,
    write_target_snapshot,
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


def _write_test_plan_package(
    state_dir: Path,
    *,
    workflow_run_id: str,
    task_map_generation: str,
    plan_revision: str = "R1",
) -> tuple[dict, dict]:
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
    package = build_plan_artifact_package(
        workflow_run_id=workflow_run_id,
        flow_kind="prd",
        producer_stage_id="prd-plan",
        run_contract=run_contract,
        plan_revision=plan_revision,
        task_map_generation=task_map_generation,
        produced=[],
    )
    return package, write_plan_artifact_package(state_dir, package)


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

    first = service.catalog_list(
        context=context,
        task_id="T1",
        view="occurrences",
    )
    assert first["projection_state"] == "ready"
    assert len(first["items"]) == 3
    visible = [row for row in first["items"] if row["authorized"]]
    assert len(visible) == 2
    assert [row for row in first["items"] if not row["authorized"]] == [
        {"authorized": False, "redacted": True}
    ]
    assert len({row["object_id"] for row in visible}) == 1
    assert len({row["locator_id"] for row in visible}) == 2
    assert {
        row["event_id"]: row["authorized"]
        for row in visible
    } == {
        "evt-a": True,
        "evt-copy": True,
    }
    restricted_view = service.catalog_list(
        context=service.context(
            actor="worker-b",
            role="dev",
            purpose="implementation",
        ),
        task_id="T1",
        view="occurrences",
    )
    restricted_occurrence = next(
        row["occurrence_id"]
        for row in restricted_view["items"]
        if row.get("event_id") == "evt-b"
    )
    with pytest.raises(ArtifactQueryError):
        service.hydrate(
            restricted_occurrence,
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
        for row in [*visible, *restricted_view["items"]]
        if row.get("authorized")
    }
    for path in projection_db_path(state_dir).parent.glob("read_model.sqlite*"):
        path.unlink()
    rebuilt = service.catalog_list(
        context=context,
        task_id="T1",
        view="occurrences",
    )
    rebuilt_restricted = service.catalog_list(
        context=service.context(
            actor="worker-b",
            role="dev",
            purpose="implementation",
        ),
        task_id="T1",
        view="occurrences",
    )
    assert {
        (
            row["object_id"],
            row["locator_id"],
            row["occurrence_id"],
        )
        for row in [*rebuilt["items"], *rebuilt_restricted["items"]]
        if row.get("authorized")
    } == identities
    assert rebuilt["source_snapshot"]["event_cursor"]["projected_seq"] == 3
    assert "session_store_digest" in rebuilt["source_snapshot"]
    assert str(project_root) == rebuilt["items"][0]["project_scope"]
    bounded = service.catalog_list(
        context=service.context(limit=1),
        task_id="T1",
        view="occurrences",
    )
    assert len(bounded["items"]) == 1
    assert bounded["has_more"] is True
    object_view = service.catalog_list(
        context=context,
        task_id="T1",
    )
    assert len(object_view["items"]) == 1
    assert object_view["items"][0]["occurrence_count"] == 2
    assert object_view["items"][0]["locator_count"] == 2
    exact_restricted = service.catalog_show(
        restricted_occurrence,
        context=context,
    )
    assert exact_restricted["item"] == {
        "authorized": False,
        "redacted": True,
        "matched_by": "occurrence",
    }


def test_catalog_defaults_to_typed_content_objects_and_expands_occurrences(
    tmp_path: Path,
) -> None:
    _, state_dir, service = _service(tmp_path)
    descriptor = write_sidecar_json(
        state_dir,
        "artifacts/results/verify.json",
        {"schema_version": "verification-result.v1", "status": "passed"},
        kind="call_control_result",
        schema_version="verification-result.v1",
        created_by="call-result-admission",
    )
    _append(state_dir, event_id="evt-verify-1", descriptor=descriptor)
    _append(state_dir, event_id="evt-verify-2", descriptor=descriptor)

    objects = service.catalog_list(
        context=service.context(),
        semantic_kind="verification_result",
    )

    assert objects["view"] == "objects"
    assert len(objects["items"]) == 1
    item = objects["items"][0]
    assert item["semantic_kind"] == "verification_result"
    assert item["storage_kinds"] == ["call_control_result"]
    assert item["occurrence_count"] == 2
    assert "event_id" not in item

    occurrences = service.catalog_list(
        context=service.context(),
        semantic_kind="verification_result",
        view="occurrences",
    )
    assert occurrences["view"] == "occurrences"
    assert {row["event_id"] for row in occurrences["items"]} == {
        "evt-verify-1",
        "evt-verify-2",
    }

    shown = service.catalog_show(item["object_id"], context=service.context())
    assert shown["item"]["object"]["object_id"] == item["object_id"]
    assert len(shown["item"]["locators"]) == 1
    assert len(shown["item"]["occurrences"]) == 2


def test_catalog_extracts_registered_typed_refs_from_known_envelopes(
    tmp_path: Path,
) -> None:
    _, state_dir, service = _service(tmp_path)
    typed_refs = {
        "run_contract": write_sidecar_json(
            state_dir,
            "artifacts/results/run-contract.json",
            {"schema_version": "run-contract.v1", "goal": "ship"},
            kind="call_control_result",
            schema_version="run-contract.v1",
            created_by="test",
        ),
        "verification_result": write_sidecar_json(
            state_dir,
            "artifacts/results/verification.json",
            {"schema_version": "verification-result.v1", "status": "passed"},
            kind="call_control_result",
            schema_version="verification-result.v1",
            created_by="test",
        ),
        "goal_closure_result": write_sidecar_json(
            state_dir,
            "artifacts/results/closure.json",
            {"schema_version": "goal-closure-result.v1", "status": "closed"},
            kind="call_control_result",
            schema_version="goal-closure-result.v1",
            created_by="test",
        ),
    }
    receipt = write_sidecar_json(
        state_dir,
        "artifacts/results/receipt.json",
        {
            "schema_version": "goal-completion-receipt.v1",
            "verification_ref": typed_refs["verification_result"],
            "closure_ref": typed_refs["goal_closure_result"],
        },
        kind="goal_completion_receipt",
        schema_version="goal-completion-receipt.v1",
        created_by="test",
    )
    envelope = write_sidecar_json(
        state_dir,
        "artifacts/results/envelope.json",
        {
            "schema_version": "call-result-envelope.v1",
            "artifact_refs": [*typed_refs.values(), receipt],
        },
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="test",
    )
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        id="evt-envelope",
        type="call.result.admitted",
        actor="kernel",
        task_id="T-envelope",
        correlation_id="run-envelope",
        payload={
            "workflow_run_id": "run-envelope",
            "workflow_operation_id": "wop-envelope",
            "attempt_id": "attempt-envelope",
            "claim_id": "claim-envelope",
            "admitted_call_result_ref": envelope,
        },
    ))

    expected_storage_kinds = {
        "run_contract": "call_control_result",
        "verification_result": "call_control_result",
        "goal_closure_result": "call_control_result",
        "goal_completion_receipt": "goal_completion_receipt",
    }
    for semantic_kind, storage_kind in expected_storage_kinds.items():
        result = service.catalog_list(
            context=service.context(),
            semantic_kind=semantic_kind,
        )
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["semantic_kind"] == semantic_kind
        assert item["storage_kinds"] == [storage_kind]
        assert item["occurrence_count"] == 1
        assert item["lineage"]["claim_ids"] == ["claim-envelope"]
        assert item["lineage"]["operation_ids"] == ["wop-envelope"]


def test_catalog_extracts_legacy_run_contract_binding_from_plan_package(
    tmp_path: Path,
) -> None:
    _, state_dir, service = _service(tmp_path)
    package, descriptor = _write_test_plan_package(
        state_dir,
        workflow_run_id="run-plan-package",
        task_map_generation="generation-1",
    )
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        id="evt-plan-package",
        type="fanout.started",
        actor="kernel",
        correlation_id="run-plan-package",
        payload={
            "workflow_run_id": "run-plan-package",
            "input_refs": [{
                "ref": descriptor["ref"],
                "sha256": descriptor["sha256"],
                "kind": "plan_artifact_package",
            }],
        },
    ))

    result = service.catalog_list(
        context=service.context(),
        semantic_kind="run_contract",
    )

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["semantic_kind"] == "run_contract"
    assert item["latest_locator"]["ref"] == package["run_contract_ref"]
    assert item["sha256"] == package["run_contract_sha256"]
    assert item["lineage"]["run_ids"] == ["run-plan-package"]


def test_typed_nested_occurrence_inherits_container_access_scope(
    tmp_path: Path,
) -> None:
    _, state_dir, service = _service(tmp_path)
    verification = write_sidecar_json(
        state_dir,
        "artifacts/results/restricted-verification.json",
        {"schema_version": "verification-result.v1", "status": "passed"},
        kind="verification_result",
        schema_version="verification-result.v1",
        created_by="test",
    )
    envelope = write_sidecar_json(
        state_dir,
        "artifacts/results/restricted-envelope.json",
        {
            "schema_version": "call-result-envelope.v1",
            "verification_ref": verification,
        },
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="test",
        access_scope={
            "visibility": "project",
            "actor": "worker-b",
            "purpose": "verification",
        },
    )
    _append(
        state_dir,
        event_id="evt-restricted-envelope",
        descriptor=envelope,
    )

    worker_b = service.context(
        actor="worker-b",
        purpose="verification",
    )
    visible = service.catalog_list(
        context=worker_b,
        semantic_kind="verification_result",
        view="occurrences",
    )
    occurrence_id = visible["items"][0]["occurrence_id"]

    worker_a = service.context(
        actor="worker-a",
        purpose="verification",
    )
    objects = service.catalog_list(
        context=worker_a,
        semantic_kind="verification_result",
    )
    assert objects["items"] == []
    assert service.catalog_show(
        occurrence_id,
        context=worker_a,
    )["item"] == {
        "authorized": False,
        "redacted": True,
        "matched_by": "occurrence",
    }


def test_typed_goal_dossier_hydrate_preserves_delivery_readiness(
    tmp_path: Path,
) -> None:
    _, state_dir, service = _service(tmp_path)
    descriptor = write_sidecar_json(
        state_dir,
        "artifacts/goals/run-1/dossier.json",
        {
            "schema_version": "goal-dossier.v1",
            "delivery_readiness": {
                "schema_version": "goal-dossier-delivery-readiness.v1",
                "status": "ready",
                "issues": [],
            },
        },
        kind="goal_dossier",
        schema_version="goal-dossier.v1",
        created_by="test",
    )
    _append(
        state_dir,
        event_id="evt-dossier",
        descriptor=descriptor,
    )

    occurrences = service.catalog_list(
        context=service.context(),
        semantic_kind="goal_dossier",
        view="occurrences",
    )
    body = service.hydrate(
        occurrences["items"][0]["occurrence_id"],
        context=service.context(),
    )

    assert body["delivery_readiness"]["status"] == "ready"


def test_catalog_active_append_catches_up_without_deleting_existing_rows(
    tmp_path: Path,
) -> None:
    project_root, state_dir, service = _service(tmp_path)
    first_descriptor = write_sidecar_json(
        state_dir,
        "artifacts/first.json",
        {"value": "first"},
        kind="contract_snapshot",
        schema_version="test.contract.v1",
        created_by="test",
    )
    _append(state_dir, event_id="evt-first", descriptor=first_descriptor)
    initial = service.catalog_list(
        context=service.context(),
        view="occurrences",
    )
    first_occurrence = initial["items"][0]["occurrence_id"]
    artifact_query_store.set_reducer_projection(
        state_dir,
        projection_kind="sentinel",
        subject_id="keep-me",
        source_snapshot_key="snapshot-1",
        source_seq=1,
        reducer_version="test.v1",
        payload={"kept": True},
    )

    second_descriptor = write_sidecar_json(
        state_dir,
        "artifacts/second.json",
        {"value": "second"},
        kind="verification_result",
        schema_version="test.result.v1",
        created_by="test",
    )
    _append(state_dir, event_id="evt-second", descriptor=second_descriptor)
    result = artifact_query_store.catch_up_catalog(
        state_dir,
        project_root=project_root,
    )

    assert result["records_projected"] == 1
    assert result["occurrences_inserted"] == 1
    rows = service.catalog_list(
        context=service.context(),
        view="occurrences",
    )["items"]
    assert {row["event_id"] for row in rows} == {"evt-first", "evt-second"}
    assert first_occurrence in {row["occurrence_id"] for row in rows}
    assert artifact_query_store.get_reducer_projection(
        state_dir,
        projection_kind="sentinel",
        subject_id="keep-me",
        source_snapshot_key="snapshot-1",
    ) == {"kept": True}


def test_catalog_catch_up_rolls_back_rows_and_cursor_on_write_fault(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, state_dir, service = _service(tmp_path)
    first_descriptor = write_sidecar_json(
        state_dir,
        "artifacts/first.json",
        {"value": "first"},
        kind="contract_snapshot",
        schema_version="test.contract.v1",
        created_by="test",
    )
    _append(state_dir, event_id="evt-first", descriptor=first_descriptor)
    service.catalog_list(context=service.context())
    second_descriptor = write_sidecar_json(
        state_dir,
        "artifacts/second.json",
        {"value": "second"},
        kind="verification_result",
        schema_version="test.result.v1",
        created_by="test",
    )
    _append(state_dir, event_id="evt-second", descriptor=second_descriptor)

    real_insert = artifact_query_store._insert_descriptor

    def fail_after_insert(*args, **kwargs):
        real_insert(*args, **kwargs)
        raise RuntimeError("injected catalog write fault")

    monkeypatch.setattr(
        artifact_query_store,
        "_insert_descriptor",
        fail_after_insert,
    )
    with pytest.raises(RuntimeError, match="injected catalog write fault"):
        artifact_query_store.catch_up_catalog(
            state_dir,
            project_root=project_root,
        )

    with artifact_query_store.connect_projection_db(state_dir) as conn:
        meta = artifact_query_store._meta(conn)
        occurrence_count = conn.execute(
            "SELECT COUNT(*) FROM artifact_occurrence"
        ).fetchone()[0]
    assert int(meta["source_seq"]) == 1
    assert occurrence_count == 1

    monkeypatch.setattr(
        artifact_query_store,
        "_insert_descriptor",
        real_insert,
    )
    repaired = artifact_query_store.catch_up_catalog(
        state_dir,
        project_root=project_root,
    )
    assert repaired["source_seq"] == 2
    assert repaired["occurrences_inserted"] == 1


def test_catalog_concurrent_queries_share_one_incremental_catch_up(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, state_dir, service = _service(tmp_path)
    first_descriptor = write_sidecar_json(
        state_dir,
        "artifacts/first.json",
        {"value": "first"},
        kind="contract_snapshot",
        schema_version="test.contract.v1",
        created_by="test",
    )
    _append(state_dir, event_id="evt-first", descriptor=first_descriptor)
    service.catalog_list(context=service.context())
    second_descriptor = write_sidecar_json(
        state_dir,
        "artifacts/second.json",
        {"value": "second"},
        kind="verification_result",
        schema_version="test.result.v1",
        created_by="test",
    )
    _append(state_dir, event_id="evt-second", descriptor=second_descriptor)

    calls: list[str] = []
    real_catch_up = artifact_query_service.catch_up_catalog

    def counted_catch_up(*args, **kwargs):
        calls.append(threading.current_thread().name)
        time.sleep(0.05)
        return real_catch_up(*args, **kwargs)

    monkeypatch.setattr(
        artifact_query_service,
        "catch_up_catalog",
        counted_catch_up,
    )
    barrier = threading.Barrier(6)
    results: list[dict] = []

    def query() -> None:
        barrier.wait()
        results.append(service.catalog_list(
            context=service.context(),
            view="occurrences",
        ))

    threads = [
        threading.Thread(target=query, name=f"catalog-query-{index}")
        for index in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(calls) == 1
    assert len(results) == 6
    assert all(
        {row["event_id"] for row in result["items"]}
        == {"evt-first", "evt-second"}
        for result in results
    )


def test_catalog_truncation_requires_explicit_rebuild_and_uses_fallback(
    tmp_path: Path,
) -> None:
    project_root, state_dir, service = _service(tmp_path)
    descriptor = write_sidecar_json(
        state_dir,
        "artifacts/current.json",
        {"value": "current"},
        kind="contract_snapshot",
        schema_version="test.contract.v1",
        created_by="test",
    )
    _append(state_dir, event_id="evt-current", descriptor=descriptor)
    service.catalog_list(context=service.context())

    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    status = artifact_query_store.catalog_status(state_dir)
    assert status["projection_state"] == "rebuild_required"
    assert status["rebuild_reason"] == "event_segment_truncated"
    fallback = service.catalog_list(context=service.context(mode="canonical"))
    assert fallback["fallback"]["used"] is True
    assert fallback["items"] == []
    with artifact_query_store.connect_projection_db(state_dir) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM artifact_occurrence"
        ).fetchone()[0] == 1

    artifact_query_store.rebuild_catalog(
        state_dir,
        project_root=project_root,
        force=True,
    )
    assert artifact_query_store.catalog_status(state_dir)[
        "projection_state"
    ] == "ready"


def test_catalog_version_watermarks_are_not_overwritten_by_schema_init(
    tmp_path: Path,
) -> None:
    project_root, state_dir, service = _service(tmp_path)
    descriptor = write_sidecar_json(
        state_dir,
        "artifacts/current.json",
        {"value": "current"},
        kind="contract_snapshot",
        schema_version="test.contract.v1",
        created_by="test",
    )
    _append(state_dir, event_id="evt-current", descriptor=descriptor)
    service.catalog_list(context=service.context())
    with artifact_query_store.connect_projection_db(state_dir) as conn:
        artifact_query_store._set_meta(
            conn,
            "descriptor_extractor_version",
            "sidecar-descriptor-extractor.v1",
        )
        conn.commit()
        artifact_query_store.ensure_catalog_schema(conn)
        meta = artifact_query_store._meta(conn)
    assert meta["descriptor_extractor_version"] == (
        "sidecar-descriptor-extractor.v1"
    )

    status = artifact_query_store.catalog_status(state_dir)
    assert status["projection_state"] == "rebuild_required"
    assert status["rebuild_reason"] == "extractor_version_changed"
    artifact_query_store.rebuild_catalog(
        state_dir,
        project_root=project_root,
        force=True,
    )
    rebuilt = artifact_query_store.catalog_status(
        state_dir,
        count_source=True,
    )
    assert rebuilt["projection_state"] == "ready"
    assert rebuilt["descriptor_extractor_version"] == (
        artifact_query_store.EXTRACTOR_VERSION
    )
    assert rebuilt["catalog_projector_version"] == (
        artifact_query_store.CATALOG_PROJECTOR_VERSION
    )
    assert rebuilt["catalog_build_watermark"]
    assert rebuilt["last_full_rebuild_at"]
    assert rebuilt["projection_lag"] == 0
    assert rebuilt["db_bytes"] > 0


def test_catalog_lineage_relation_is_schema_aware_not_kind_heuristic(
    tmp_path: Path,
) -> None:
    _, state_dir, service = _service(tmp_path)
    target_like = write_sidecar_json(
        state_dir,
        "artifacts/target-like.json",
        {"value": "not a target"},
        kind="target-looking-custom",
        schema_version="custom.v1",
        created_by="test",
    )
    explicit = {
        **write_sidecar_json(
            state_dir,
            "artifacts/superseding.json",
            {"value": "replacement"},
            kind="custom",
            schema_version="custom.v1",
            created_by="test",
        ),
        "relation": "supersedes",
    }
    target = write_sidecar_json(
        state_dir,
        "artifacts/target.json",
        {"target_commit": "abc"},
        kind="target_snapshot",
        schema_version="target-snapshot.v1",
        created_by="test",
    )
    _append(state_dir, event_id="evt-custom", descriptor=target_like)
    _append(state_dir, event_id="evt-explicit", descriptor=explicit)
    _append(state_dir, event_id="evt-target", descriptor=target)

    lineage = service.lineage(
        subject_kind="task",
        subject_id="T1",
        context=service.context(),
    )
    relations = {
        item["source_event_id"]: item["relation"]
        for item in lineage["items"]
    }
    assert relations == {
        "evt-custom": "output",
        "evt-explicit": "supersedes",
        "evt-target": "target",
    }


def test_catalog_rotation_and_partial_tail_require_bounded_handling(
    tmp_path: Path,
) -> None:
    project_root, state_dir, service = _service(tmp_path)
    first = write_sidecar_json(
        state_dir,
        "artifacts/first.json",
        {"value": "first"},
        kind="contract_snapshot",
        schema_version="test.contract.v1",
        created_by="test",
    )
    _append(state_dir, event_id="evt-first", descriptor=first)
    service.catalog_list(context=service.context())

    with (state_dir / "events.jsonl").open("ab") as handle:
        handle.write(b'{"id":"partial"')
    partial = artifact_query_store.catch_up_catalog(
        state_dir,
        project_root=project_root,
    )
    assert partial["records_projected"] == 0
    assert artifact_query_store.catalog_status(state_dir)[
        "occurrence_count"
    ] == 1

    active = state_dir / "events.jsonl"
    archive = state_dir / "events" / "2026-07-24.jsonl"
    archive.parent.mkdir(parents=True)
    active.replace(archive)
    second = write_sidecar_json(
        state_dir,
        "artifacts/second.json",
        {"value": "second"},
        kind="verification_result",
        schema_version="verification-result.v1",
        created_by="test",
    )
    _append(state_dir, event_id="evt-second", descriptor=second)
    rotated = artifact_query_store.catalog_status(state_dir)
    assert rotated["projection_state"] == "rebuild_required"
    assert rotated["rebuild_reason"] == "event_segment_layout_changed"


def test_catalog_recovery_quarantines_db_wal_and_shm_before_rebuild(
    tmp_path: Path,
) -> None:
    project_root, state_dir, service = _service(tmp_path)
    descriptor = write_sidecar_json(
        state_dir,
        "artifacts/current.json",
        {"value": "current"},
        kind="contract_snapshot",
        schema_version="test.contract.v1",
        created_by="test",
    )
    _append(state_dir, event_id="evt-current", descriptor=descriptor)
    service.catalog_list(context=service.context())
    db_path = projection_db_path(state_dir)
    db_path.write_bytes(b"not sqlite")
    Path(str(db_path) + "-wal").write_bytes(b"bad wal")
    Path(str(db_path) + "-shm").write_bytes(b"bad shm")

    recovered = artifact_query_store.recover_catalog_projection(
        state_dir,
        project_root=project_root,
    )

    assert recovered["projection_state"] == "ready"
    assert recovered["affected_components"] == [
        "artifact-catalog",
        "event-index",
    ]
    assert {Path(path).name for path in recovered["quarantined"]} == {
        "read_model.sqlite",
        "read_model.sqlite-wal",
        "read_model.sqlite-shm",
    }
    assert all(Path(path).is_file() for path in recovered["quarantined"])
    assert artifact_query_store.catalog_status(state_dir)[
        "occurrence_count"
    ] == 1


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
    result = service.catalog_list(
        context=context,
        view="occurrences",
    )

    assert result["fallback"] == {
        "used": True,
        "source": "event-log-descriptor-scan",
    }
    assert result["projection_state"] == "corrupt"
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
    monkeypatch,
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

    monkeypatch.setattr(
        "zf.runtime.artifact_query.service.GOAL_DOSSIER_CACHE_VERSION",
        "goal-dossier-cache.next",
    )
    fourth = service.cached_goal_dossier("run-cache", builder=build)
    assert fourth["cache"]["hit"] is False
    assert calls == [1, 2, 3]


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
    assert manifest["handoff_authority_profile"] == "implementation-initial"

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


def test_task_bound_handoff_uses_its_admitted_generation_package(
    tmp_path: Path,
) -> None:
    project_root, state_dir, _ = _service(tmp_path)
    first, first_ref = _write_test_plan_package(
        state_dir,
        workflow_run_id="run-generations",
        task_map_generation="G1",
        plan_revision="R1",
    )
    second, second_ref = _write_test_plan_package(
        state_dir,
        workflow_run_id="run-generations",
        task_map_generation="G2",
        plan_revision="R2",
    )
    log = EventLog(state_dir / "events.jsonl")
    for event_id, package, descriptor in (
        ("evt-package-g1", first, first_ref),
        ("evt-package-g2", second, second_ref),
    ):
        log.append(ZfEvent(
            id=event_id,
            type="plan.artifact_package.admitted",
            correlation_id="run-generations",
            payload=package_event_payload(package, descriptor, status="admitted"),
        ))
    task = Task(
        id="T-generation-g1",
        title="Generation-local handoff",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(
            behavior="finish an admitted earlier generation",
            acceptance_criteria=["AC generation-local"],
            verification="pytest -q",
            evidence_contract={
                "source_refs": {
                    "task_map_generation": "G1",
                    "task_map_ref": "artifacts/task-maps/g1.json",
                    "plan_artifact_package_id": first_ref["package_id"],
                    "plan_artifact_package_ref": first_ref["ref"],
                    "plan_artifact_package_digest": first_ref["sha256"],
                },
            },
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    current = current_task_contract_identity(task)
    snapshot = build_task_contract_snapshot(
        task,
        workflow_run_id="run-generations",
        task_map_generation_id="G1",
        base_commit="base-g1",
        task_ref="refs/zf/tasks/T-generation-g1",
    )
    snapshot_ref = write_task_contract_snapshot(state_dir, snapshot)
    payload = {
        **current,
        "workflow_run_id": "run-generations",
        "task_ref": "refs/zf/tasks/T-generation-g1",
        "base_commit": "base-g1",
        "output_profile_id": "implementation",
        "artifact_package_mode": "blocking",
        "contract_snapshot_ref": snapshot_ref["ref"],
        "contract_snapshot_digest": snapshot_ref["sha256"],
        "plan_artifact_package_id": first_ref["package_id"],
        "plan_artifact_package_ref": first_ref["ref"],
        "plan_artifact_package_digest": first_ref["sha256"],
    }
    payload["handoff_authority_contract"] = build_handoff_authority_contract(
        payload,
        output_profile_id="implementation",
        stage_id="impl",
        operation_type="task-stage",
    )

    manifest, _ = CanonicalHandoffResolver(
        state_dir=state_dir,
        project_root=project_root,
        config=None,
    ).resolve_payload(
        payload=payload,
        workflow_run_id="run-generations",
        task_id=task.id,
        attempt_id="attempt-g1",
        dispatch_id="dispatch-g1",
    )

    assert manifest["task_map_generation"] == "G1"
    assert manifest["plan_artifact_package_id"] == first_ref["package_id"]
    assert manifest["plan_artifact_package_ref"] == first_ref["ref"]
    assert manifest["plan_artifact_package_digest"] == first_ref["sha256"]


def test_blocking_handoff_distinguishes_initial_impl_from_rework(
    tmp_path: Path,
) -> None:
    project_root, state_dir, _ = _service(tmp_path)
    package, package_descriptor = _write_test_plan_package(
        state_dir,
        workflow_run_id="run-authority",
        task_map_generation="G1",
    )
    task = Task(
        id="T-authority",
        title="Authority matrix",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(
            behavior="respect stage authority",
            acceptance_criteria=["AC-1"],
            verification="pytest -q",
            evidence_contract={
                "source_refs": {
                    "task_map_generation": "G1",
                    "task_map_ref": "artifacts/task-maps/g1.json",
                    "plan_artifact_package_id": package_descriptor["package_id"],
                    "plan_artifact_package_ref": package_descriptor["ref"],
                    "plan_artifact_package_digest": package_descriptor["sha256"],
                },
            },
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    current = current_task_contract_identity(task)
    snapshot = build_task_contract_snapshot(
        task,
        workflow_run_id="run-authority",
        task_map_generation_id=current["task_map_generation"],
        base_commit="base-1",
        task_ref="refs/zf/tasks/T-authority",
    )
    snapshot_ref = write_task_contract_snapshot(state_dir, snapshot)
    resolver = CanonicalHandoffResolver(
        state_dir=state_dir,
        project_root=project_root,
        config=None,
    )
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        id="evt-package-authority",
        type="plan.artifact_package.admitted",
        correlation_id="run-authority",
        payload=package_event_payload(
            package,
            package_descriptor,
            status="admitted",
        ),
    ))
    initial = {
        **current,
        "artifact_package_mode": "blocking",
        "task_ref": "refs/zf/tasks/T-authority",
        "base_commit": "base-1",
        "output_profile_id": "implementation",
        "contract_snapshot_ref": snapshot_ref["ref"],
        "contract_snapshot_digest": snapshot_ref["sha256"],
        "plan_artifact_package_id": package_descriptor["package_id"],
        "plan_artifact_package_ref": package_descriptor["ref"],
        "plan_artifact_package_digest": package_descriptor["sha256"],
    }
    initial["handoff_authority_contract"] = (
        build_handoff_authority_contract(
            initial,
            output_profile_id="implementation",
            stage_id="impl",
            operation_type="writer",
        )
    )

    manifest, _ = resolver.resolve_payload(
        payload=initial,
        workflow_run_id="run-authority",
        task_id="T-authority",
        attempt_id="attempt-initial",
        dispatch_id="dispatch-initial",
    )
    assert manifest["handoff_authority_profile"] == "implementation-initial"

    with pytest.raises(ArtifactReadError, match="accepted TaskRef"):
        resolver.resolve_payload(
            payload={
                **initial,
                "attempt_domain": "task_rework",
                "rework_of_attempt_id": "attempt-initial",
                "rework_feedback_ref": "artifacts/rework.json",
                "handoff_authority_contract": build_handoff_authority_contract(
                    {
                        **initial,
                        "attempt_domain": "task_rework",
                        "rework_of_attempt_id": "attempt-initial",
                    },
                    output_profile_id="implementation",
                    stage_id="impl",
                    operation_type="writer",
                ),
            },
            workflow_run_id="run-authority",
            task_id="T-authority",
            attempt_id="attempt-rework",
            dispatch_id="dispatch-rework",
        )

    with pytest.raises(ArtifactReadError, match="current canonical task"):
        resolver.resolve_payload(
            payload=initial,
            workflow_run_id="run-authority",
            task_id="T-missing",
            attempt_id="attempt-missing-task",
            dispatch_id="dispatch-missing-task",
        )


def test_candidate_verify_binds_current_candidate_and_integrated_task_refs(
    tmp_path: Path,
) -> None:
    project_root, state_dir, _ = _service(tmp_path)
    package, package_descriptor = _write_test_plan_package(
        state_dir,
        workflow_run_id="run-candidate",
        task_map_generation="G2",
    )
    source_refs = {
        "task_map_generation": "G2",
        "task_map_ref": "artifacts/task-maps/g2.json",
        "plan_artifact_package_id": package_descriptor["package_id"],
        "plan_artifact_package_ref": package_descriptor["ref"],
        "plan_artifact_package_digest": package_descriptor["sha256"],
    }
    task = Task(
        id="T-candidate",
        title="Candidate authority",
        status="done",
        assigned_to="dev",
        contract=TaskContract(
            behavior="bind candidate lineage",
            acceptance_criteria=["AC-C"],
            verification="pytest -q",
            evidence_contract={"source_refs": source_refs},
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    current = current_task_contract_identity(task)
    contract = build_task_contract_snapshot(
        task,
        workflow_run_id="run-candidate",
        task_map_generation_id=current["task_map_generation"],
        base_commit="base-candidate",
        task_ref="refs/zf/tasks/T-candidate",
    )
    contract_ref = write_task_contract_snapshot(state_dir, contract)
    candidate_commit = "c" * 40
    target = build_target_snapshot(
        contract_ref,
        target_commit=candidate_commit,
        contract_snapshot=contract,
    )
    target_ref = write_target_snapshot(state_dir, target)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-package-candidate",
        type="plan.artifact_package.admitted",
        correlation_id="run-candidate",
        payload=package_event_payload(
            package,
            package_descriptor,
            status="admitted",
        ),
    ))
    task_commit = "d" * 40
    log.append(ZfEvent(
        id="evt-task-ref-candidate",
        type="task.ref.updated",
        task_id="T-candidate",
        correlation_id="run-candidate",
        payload={
            "workflow_run_id": "run-candidate",
            "task_id": "T-candidate",
            "source_commit": task_commit,
        },
    ))
    log.append(ZfEvent(
        id="evt-candidate-current",
        type="candidate.ready",
        correlation_id="run-candidate",
        payload={
            "workflow_run_id": "run-candidate",
            "candidate_base_commit": "base-candidate",
            "candidate_head_commit": candidate_commit,
            "completed_task_ids": ["T-candidate"],
            "task_map_generation": "G2",
        },
    ))
    payload = {
        **current,
        **source_refs,
        "artifact_package_mode": "blocking",
        "output_profile_id": "candidate-verify",
        "base_commit": "base-candidate",
        "target_commit": candidate_commit,
        "task_ref": "refs/zf/tasks/T-candidate",
        "contract_snapshot_ref": contract_ref["ref"],
        "contract_snapshot_digest": contract_ref["sha256"],
        "target_snapshot_ref": target_ref["ref"],
        "target_snapshot_digest": target_ref["sha256"],
    }
    payload["handoff_authority_contract"] = (
        build_handoff_authority_contract(
            payload,
            output_profile_id="candidate-verify",
            stage_id="candidate-verify",
            operation_type="verifier",
        )
    )
    resolver = CanonicalHandoffResolver(
        state_dir=state_dir,
        project_root=project_root,
        config=None,
    )

    manifest, _ = resolver.resolve_payload(
        payload=payload,
        workflow_run_id="run-candidate",
        task_id="T-candidate",
        attempt_id="attempt-candidate",
        dispatch_id="dispatch-candidate",
    )
    assert manifest["candidate_snapshot"]["candidate_event_id"] == (
        "evt-candidate-current"
    )
    assert manifest["candidate_snapshot"]["integrated_task_refs"] == [{
        "task_id": "T-candidate",
        "source_commit": task_commit,
    }]

    with pytest.raises(ArtifactReadError, match="target_commit"):
        resolver.resolve_payload(
            payload={**payload, "target_commit": "e" * 40},
            workflow_run_id="run-candidate",
            task_id="T-candidate",
            attempt_id="attempt-stale-candidate",
            dispatch_id="dispatch-stale-candidate",
        )


def test_candidate_verify_binds_frozen_candidate_not_workflow_anchor_contract(
    tmp_path: Path,
) -> None:
    project_root, state_dir, _ = _service(tmp_path)
    package, package_descriptor = _write_test_plan_package(
        state_dir,
        workflow_run_id="run-candidate-anchor",
        task_map_generation="G3",
    )
    TaskStore(state_dir / "kanban.json").add(Task(
        id="FLOW-ANCHOR",
        title="Workflow anchor",
        status="in_progress",
        assigned_to="orchestrator",
        contract=TaskContract(
            behavior="track workflow lifecycle",
            acceptance_criteria=["workflow closes"],
            verification="",
        ),
    ))
    candidate_commit = "a" * 40
    task_commit = "b" * 40
    freeze_receipt = {
        "schema_version": "candidate-freeze-receipt.v1",
        "freeze_id": "freeze-g3",
        "workflow_run_id": "run-candidate-anchor",
        "task_map_generation": "G3",
        "candidate_generation": "CG3",
        "candidate_base_commit": "base-candidate",
        "candidate_head": candidate_commit,
        "candidate_head_commit": candidate_commit,
        "candidate_ref": "refs/heads/candidate/demo",
        "integration_ledger_digest": "ledger-g3",
        "completed_task_ids": ["T-delivery"],
        "task_ids": ["T-delivery"],
        "status": "frozen",
    }
    freeze_descriptor = write_immutable_json_sidecar(
        state_dir,
        freeze_receipt,
        root="candidate-freeze-receipts",
        kind="candidate_freeze_receipt",
        schema_version="candidate-freeze-receipt.v1",
        created_by="test",
    )
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-package-candidate-anchor",
        type="plan.artifact_package.admitted",
        correlation_id="run-candidate-anchor",
        payload=package_event_payload(
            package,
            package_descriptor,
            status="admitted",
        ),
    ))
    log.append(ZfEvent(
        id="evt-task-ref-delivery",
        type="task.ref.updated",
        task_id="T-delivery",
        correlation_id="run-candidate-anchor",
        payload={
            "workflow_run_id": "run-candidate-anchor",
            "task_id": "T-delivery",
            "source_commit": task_commit,
        },
    ))
    log.append(ZfEvent(
        id="evt-candidate-anchor",
        type="candidate.ready",
        correlation_id="run-candidate-anchor",
        payload={
            **freeze_receipt,
            "freeze_receipt_ref": freeze_descriptor,
            "freeze_receipt_digest": freeze_descriptor["sha256"],
        },
    ))
    newer_package, newer_package_descriptor = _write_test_plan_package(
        state_dir,
        workflow_run_id="run-candidate-anchor",
        task_map_generation="G4",
        plan_revision="R2",
    )
    log.append(ZfEvent(
        id="evt-package-candidate-anchor-newer",
        type="plan.artifact_package.admitted",
        correlation_id="run-candidate-anchor",
        payload=package_event_payload(
            newer_package,
            newer_package_descriptor,
            status="admitted",
        ),
    ))
    aggregate_contract = {
        "schema_version": "task-contract-snapshot.v1",
        "workflow_run_id": "run-candidate-anchor",
        "task_id": "FLOW-ANCHOR",
        "contract_revision": "candidate-contract-g3",
        "task_map_generation": "G3",
        "base_commit": "base-candidate",
        "task_ref": "refs/heads/candidate/demo",
        "plan_artifact_package_id": package_descriptor["package_id"],
        "plan_artifact_package_ref": package_descriptor["ref"],
        "plan_artifact_package_digest": package_descriptor["sha256"],
        "title": "candidate verification",
        "behavior": "verify the frozen candidate",
        "allowed_paths": [],
        "protected_paths": [".zf/**"],
        "acceptance_criteria": [{
            "acceptance_id": "candidate-ac-1",
            "statement": "integrated delivery remains valid",
            "verification_owner": "candidate_verify",
            "verification_tier": "integration",
            "verification_command_ids": [],
        }],
        "verification_command": "",
        "verification_commands": [],
        "verification_tiers": ["integration"],
        "required_source_outputs": [],
        "required_contract_tests": [],
        "source_refs": {
            "freeze_receipt_ref": freeze_descriptor["ref"],
            "freeze_receipt_digest": freeze_descriptor["sha256"],
        },
        "evidence_contract": {"authority_scope": "candidate"},
        "authority_scope": "candidate",
        "candidate_event_id": "evt-candidate-anchor",
        "completed_task_ids": ["T-delivery"],
        "source_ref": freeze_descriptor["ref"],
        "source_index_ref": "",
        "product_contract_ref": "",
        "risk_class": "candidate",
        "integration_admission_profile": "candidate-wide",
    }
    contract_descriptor = write_task_contract_snapshot(
        state_dir,
        aggregate_contract,
    )
    target_descriptor = write_target_snapshot(
        state_dir,
        {
            **build_target_snapshot(
                contract_descriptor,
                target_commit=candidate_commit,
                contract_snapshot=aggregate_contract,
            ),
            "authority_scope": "candidate",
        },
    )
    payload = {
        "artifact_package_mode": "blocking",
        "output_profile_id": "candidate-verify",
        "contract_revision": "candidate-contract-g3",
        "task_map_generation": "G3",
        "base_commit": "base-candidate",
        "task_ref": "refs/heads/candidate/demo",
        "target_commit": candidate_commit,
        "contract_snapshot_ref": contract_descriptor["ref"],
        "contract_snapshot_digest": contract_descriptor["sha256"],
        "target_snapshot_ref": target_descriptor["ref"],
        "target_snapshot_digest": target_descriptor["sha256"],
    }
    payload["handoff_authority_contract"] = build_handoff_authority_contract(
        payload,
        output_profile_id="candidate-verify",
        stage_id="candidate-verify",
        operation_type="fanout_reader_child",
    )
    resolver = CanonicalHandoffResolver(
        state_dir=state_dir,
        project_root=project_root,
        config=None,
    )

    manifest, _ = resolver.resolve_payload(
        payload=payload,
        workflow_run_id="run-candidate-anchor",
        task_id="FLOW-ANCHOR",
        attempt_id="attempt-candidate-anchor",
        dispatch_id="dispatch-candidate-anchor",
    )

    assert manifest["handoff_authority_profile"] == "candidate-verify"
    assert manifest["plan_artifact_package_ref"] == package_descriptor["ref"]
    assert manifest["candidate_snapshot"]["candidate_event_id"] == (
        "evt-candidate-anchor"
    )
    assert manifest["candidate_snapshot"]["freeze_receipt_digest"] == (
        freeze_descriptor["sha256"]
    )
    assert manifest["candidate_snapshot"]["integrated_task_refs"] == [{
        "task_id": "T-delivery",
        "source_commit": task_commit,
    }]
    assert "candidate-freeze" in {
        source["source_id"] for source in manifest["sources"]
    }
    assert any(
        section["source_id"] == "candidate-freeze" and section["required"]
        for section in manifest["context_sections"]
    )

    log.append(ZfEvent(
        id="evt-candidate-anchor-tampered",
        type="candidate.ready",
        correlation_id="run-candidate-anchor",
        payload={
            **freeze_receipt,
            "freeze_receipt_ref": freeze_descriptor,
            "freeze_receipt_digest": "0" * 64,
        },
    ))
    with pytest.raises(ArtifactReadError, match="freeze receipt digest mismatch"):
        resolver.resolve_payload(
            payload=payload,
            workflow_run_id="run-candidate-anchor",
            task_id="FLOW-ANCHOR",
            attempt_id="attempt-candidate-anchor-tampered",
            dispatch_id="dispatch-candidate-anchor-tampered",
        )


@pytest.mark.parametrize("scoped", [False, True])
def test_handoff_resolver_materializes_current_required_plan_ports(
    tmp_path: Path, scoped: bool,
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
    names = ["acceptance_matrix", "test_matrix"] + (["execution_spec"] if scoped else [])
    for logical_name in names:
        body = ({"schema_version": "task-scoped-plan-input.v1",
                 "shared": {"rule": "access control"},
                 "tasks": [{"task_id": "T-ports", "body": {"action": "publish"}},
                           {"task_id": "other", "body": {"action": "archive"}}]}
                if logical_name == "execution_spec"
                else {"schema_version": f"{logical_name}.v1", "rows": [{"id": "AC-1"}]})
        descriptor = write_immutable_json_sidecar(
            state_dir,
            body,
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
        required_ports=names,
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
    if scoped:
        from zf.runtime.artifact_read_ledger import read_attempt_artifact
        from zf.runtime.artifact_read_requirements import build_canonical_required_reads

        reads = build_canonical_required_reads(manifest, output_profile_id="implementation")
        selected = [r for r in reads if r["source_id"] == "plan-port-execution_spec"]
        assert [r["json_path"] for r in selected] == ["$.shared", "$.tasks[0]"]
        for row in selected:
            read_attempt_artifact(state_dir, manifest=manifest,
                                  source_id=row["source_id"], artifact_id=row["artifact_id"],
                                  json_path=row["json_path"])
        with pytest.raises(ArtifactReadError, match="not allowed"):
            read_attempt_artifact(state_dir, manifest=manifest,
                                  source_id="plan-port-execution_spec", artifact_id="execution_spec",
                                  json_path="$.tasks[1]")
        candidate_sources = resolver._current_plan_port_sources(
            workflow_run_id="run-ports", task_id="",
            currentness={"task_map_generation": "G2",
                         "plan_artifact_package_ref": package_descriptor["ref"],
                         "plan_artifact_package_digest": package_descriptor["sha256"]},
            allow_without_task=True, candidate=True)
        assert all(s["allowed_paths"] == ["$"] for s in candidate_sources)

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
