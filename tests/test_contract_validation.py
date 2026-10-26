from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.task.contract_validation import validate_task_contract
from zf.core.task.schema import Task, TaskContract, TaskExecutionBinding


def _task(*, workflow: bool = False) -> Task:
    return Task(
        id="TASK-PATHS",
        title="Deliver the supplied handoff",
        execution_binding=TaskExecutionBinding(owner="workflow" if workflow else ""),
        contract=TaskContract(
            behavior="Implement the supplied specification.",
            verification="Run the acceptance checks.",
            verification_tiers=["runtime"],
            owner_role="" if workflow else "dev",
            source_ref="docs/spec.md",
            scope=["src/**"],
        ),
    )


def _validate(task: Task, root: Path) -> list[str]:
    return validate_task_contract(
        task,
        config=ZfConfig(
            project=ProjectConfig(name="example", state_dir=".runtime"),
            roles=[RoleConfig(name="dev", role_kind="writer")],
        ),
        project_root=root,
    )


def test_exclusions_can_protect_git_and_configured_runtime(tmp_path: Path) -> None:
    task = _task()
    task.contract.exclusions = [".git/**", ".runtime/**", "zf.yaml"]

    assert _validate(task, tmp_path) == []


@pytest.mark.parametrize("owner_source", ["binding", "legacy_evidence"])
def test_workflow_handoff_accepts_existing_external_inputs(
    tmp_path: Path, owner_source: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    handoff = tmp_path / "handoff.md"
    handoff.write_text("Specification", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    task = _task(workflow=True)
    if owner_source == "legacy_evidence":
        task.execution_binding = TaskExecutionBinding()
        task.contract.evidence_contract = {"execution_owner": "workflow"}
    task.contract.handoff_artifacts = [str(handoff), f"{assets}/"]

    assert _validate(task, root) == []


@pytest.mark.parametrize("field", ["scope", "shared_files", "exclusive_files"])
@pytest.mark.parametrize("path", [".git/**", ".runtime/**", "../outside", "/tmp/outside"])
def test_workflow_write_paths_stay_restricted(
    tmp_path: Path, field: str, path: str,
) -> None:
    task = _task(workflow=True)
    setattr(task.contract, field, [path])

    assert any(f"contract.{field}" in error for error in _validate(task, tmp_path))


@pytest.mark.parametrize("field", ["scope", "exclusions", "handoff_artifacts"])
def test_relative_paths_cannot_escape_through_symlinks(
    tmp_path: Path, field: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "outside").symlink_to(tmp_path, target_is_directory=True)
    task = _task(workflow=True)
    setattr(task.contract, field, ["outside/input.md"])

    assert any("path escapes project root" in error for error in _validate(task, root))


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside"])
def test_exclusions_remain_project_relative(tmp_path: Path, path: str) -> None:
    task = _task()
    task.contract.exclusions = [path]

    assert any("contract.exclusions" in error for error in _validate(task, tmp_path))


def test_worker_handoff_cannot_use_external_output_paths(tmp_path: Path) -> None:
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    task = _task()
    task.contract.handoff_artifacts = [str(handoff)]

    assert any("absolute paths are not allowed" in error for error in _validate(task, tmp_path))


@pytest.mark.parametrize("path_kind", ["missing", "internal", "traversal", "special"])
def test_workflow_external_input_must_be_a_valid_file_or_directory(
    tmp_path: Path, path_kind: str,
) -> None:
    task = _task(workflow=True)
    path = tmp_path / "handoff"
    if path_kind == "internal":
        path = tmp_path / ".runtime" / "events.jsonl"
        path.parent.mkdir()
        path.touch()
    elif path_kind == "traversal":
        path = tmp_path / ".." / "handoff"
    elif path_kind == "special":
        path = Path("/dev/null")
    task.contract.handoff_artifacts = [str(path)]

    assert any("contract.handoff_artifacts" in error for error in _validate(task, tmp_path))
