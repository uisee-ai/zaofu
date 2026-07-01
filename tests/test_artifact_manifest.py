from __future__ import annotations

from pathlib import Path

from zf.runtime.artifact_manifest import (
    contract_refs_from_manifest,
    validate_artifact_manifest,
)


_SHA = "a" * 64


def test_valid_artifact_manifest_maps_contract_refs(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "arch",
            "skills_used": ["agent-skills:design"],
            "artifact_refs": [
                {
                    "kind": "sdd",
                    "path": "docs/specs/task.md",
                    "sha256": _SHA,
                    "summary": "系统设计说明",
                },
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": "b" * 64,
                    "summary": "实施计划",
                },
                {
                    "kind": "tdd",
                    "path": "docs/plans/task-tdd.md",
                    "sha256": "c" * 64,
                    "summary": "测试计划",
                },
            ],
            "handoff_contract": {
                "required_for_dev": ["spec", "plan", "tdd"],
            },
        },
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert result.ok is True
    assert result.manifest is not None
    refs = contract_refs_from_manifest(result.manifest, event_id="evt-manifest")
    assert refs["spec_ref"] == "docs/specs/task.md"
    assert refs["plan_ref"] == "docs/plans/task-plan.md"
    assert refs["tdd_ref"] == "docs/plans/task-tdd.md"
    assert refs["evidence_contract"]["artifact_manifest_event_id"] == "evt-manifest"


def test_process_and_backlog_artifacts_map_into_contract_refs(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "orchestrator",
            "artifact_refs": [
                {
                    "kind": "process_plan",
                    "path": "docs/process/task-process.md",
                    "sha256": _SHA,
                    "summary": "process",
                },
                {
                    "kind": "backlog_map",
                    "path": "tasks/active/task-map.md",
                    "sha256": "b" * 64,
                    "summary": "backlog map",
                },
                {
                    "kind": "task_map",
                    "path": ".zf/artifacts/F-1/task_map.json",
                    "sha256": "c" * 64,
                    "summary": "task map",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert result.ok is True
    assert result.manifest is not None
    refs = contract_refs_from_manifest(result.manifest, event_id="evt-manifest")
    assert refs["plan_ref"] == "docs/process/task-process.md"
    artifact_refs = refs["evidence_contract"]["artifact_refs"]
    assert artifact_refs["process_plan_ref"] == "docs/process/task-process.md"
    assert artifact_refs["backlog_map_ref"] == "tasks/active/task-map.md"
    assert artifact_refs["task_map_ref"] == ".zf/artifacts/F-1/task_map.json"


def test_plan_artifact_aliases_map_to_contract_and_evidence_refs(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "orchestrator",
            "artifact_refs": [
                {
                    "kind": "full_stage_plan",
                    "path": "docs/plans/full.md",
                    "sha256": _SHA,
                    "summary": "full plan",
                    "status": "accepted",
                },
                {
                    "kind": "p3_backlog",
                    "path": "docs/plans/phase-3/backlog.md",
                    "sha256": "b" * 64,
                    "summary": "phase backlog",
                    "status": "accepted",
                },
                {
                    "kind": "work-unit-map",
                    "path": "artifacts/TASK-1/task-map.json",
                    "sha256": "c" * 64,
                    "summary": "task map",
                    "status": "accepted",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert result.ok is True
    assert result.manifest is not None
    refs = contract_refs_from_manifest(result.manifest, event_id="evt-manifest")
    assert refs["plan_ref"] == "docs/plans/full.md"
    artifact_refs = refs["evidence_contract"]["artifact_refs"]
    assert artifact_refs["backlog_plan_ref"] == "docs/plans/phase-3/backlog.md"
    assert artifact_refs["task_map_ref"] == "artifacts/TASK-1/task-map.json"


def test_unknown_artifact_kind_does_not_fill_contract_refs(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "custom_brainstorm",
                    "path": "docs/plans/ideas.md",
                    "sha256": _SHA,
                    "summary": "ideas",
                    "status": "accepted",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert result.ok is True
    assert result.manifest is not None
    refs = contract_refs_from_manifest(result.manifest, event_id="evt-manifest")
    assert "plan_ref" not in refs


def test_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "sdd",
                    "path": "../secret.md",
                    "sha256": _SHA,
                    "summary": "bad",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert result.ok is False
    assert any("outside allowed artifact roots" in error for error in result.errors)


def test_manifest_rejects_missing_sha256(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "sdd",
                    "path": "docs/specs/task.md",
                    "summary": "missing sha",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert result.ok is False
    assert any("sha256 is required" in error for error in result.errors)


def test_manifest_workdir_path_uses_configured_state_dir(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    state_dir = tmp_path / "runtime-state"
    project_root.mkdir()
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "dev",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": _SHA,
                    "summary": "计划",
                    "workdir_path": "dev-1/project",
                },
            ],
        },
        project_root=project_root,
        state_dir=state_dir,
    )

    assert result.ok is True


def test_manifest_accepts_artifact_ledger_metadata(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": _SHA,
                    "summary": "plan",
                    "artifact_id": "plan-TASK-1-v2",
                    "version": 2,
                    "supersedes": "plan-TASK-1-v1",
                    "status": "accepted",
                    "source_event_id": "evt-source",
                    "accepted_event_id": "evt-accepted",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert result.ok is True
    assert result.manifest is not None
    ref = result.manifest.artifact_refs[0].to_dict()
    assert ref["artifact_id"] == "plan-TASK-1-v2"
    assert ref["version"] == 2
    assert ref["supersedes"] == "plan-TASK-1-v1"
    assert ref["status"] == "accepted"


def test_manifest_uses_default_role_when_payload_omits_role(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": _SHA,
                    "summary": "plan",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
        default_role="arch",
    )

    assert result.ok is True
    assert result.manifest is not None
    assert result.manifest.role == "arch"


def test_manifest_uses_default_task_id_when_payload_omits_task_id(tmp_path: Path) -> None:
    # Live LLMs routinely omit task_id from the manifest payload (the kernel
    # already knows it from the worker's dispatch context). Mirror the
    # default_role fallback so the validator fills it instead of rejecting.
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": _SHA,
                    "summary": "plan",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
        default_task_id="TASK-1",
    )

    assert result.ok is True
    assert result.manifest is not None
    assert result.manifest.task_id == "TASK-1"


def test_manifest_accepts_semver_artifact_version_label(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": _SHA,
                    "summary": "plan",
                    "version": "0.1.0",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert result.ok is True
    assert result.manifest is not None
    assert result.manifest.artifact_refs[0].version == 0


def test_manifest_accepts_proposed_artifact_status_without_contract_ref(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": _SHA,
                    "summary": "candidate implementation plan",
                    "status": "proposed",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert result.ok is True
    assert result.manifest is not None
    refs = contract_refs_from_manifest(result.manifest, event_id="evt-manifest")
    assert "plan_ref" not in refs


def test_contract_refs_ignore_terminal_bad_artifact_statuses(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/rejected-plan.md",
                    "sha256": _SHA,
                    "summary": "rejected plan",
                    "status": "rejected",
                },
                {
                    "kind": "plan",
                    "path": "docs/plans/superseded-plan.md",
                    "sha256": "b" * 64,
                    "summary": "superseded plan",
                    "status": "superseded",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert result.ok is True
    assert result.manifest is not None
    refs = contract_refs_from_manifest(result.manifest, event_id="evt-manifest")
    assert "plan_ref" not in refs


def test_manifest_rejects_invalid_artifact_ledger_metadata(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": _SHA,
                    "summary": "plan",
                    "version": 0,
                    "status": "published",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert result.ok is False
    assert any("version must be a positive integer" in error for error in result.errors)
    assert any("status must be one of" in error for error in result.errors)


def test_manifest_normalizes_approved_status_alias(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = validate_artifact_manifest(
        {
            "task_id": "TASK-1",
            "role": "critic",
            "artifact_refs": [
                {
                    "kind": "critic_review",
                    "path": ".zf/artifacts/critic/review.md",
                    "sha256": _SHA,
                    "summary": "critic approved",
                    "status": "approved",
                },
            ],
        },
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert result.ok is True
    assert result.manifest is not None
    assert result.manifest.artifact_refs[0].status == "accepted"
