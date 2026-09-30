from pathlib import Path

import pytest

from .release import ReleaseArtifactError, inspect_artifact, run_release_artifact_checks, run_release_scenarios


def test_release_artifact_contract_checks_kind_size_and_digest(tmp_path: Path):
    wheel = tmp_path / "product-1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    record = inspect_artifact(wheel, kind="wheel")
    assert record["kind"] == "wheel" and len(record["digest"]) == 64
    with pytest.raises(ReleaseArtifactError):
        inspect_artifact(tmp_path / "missing.whl", kind="wheel")
    with pytest.raises(ReleaseArtifactError):
        inspect_artifact(tmp_path / "bad.txt", kind="wheel")


def test_release_rejects_empty_sdist(tmp_path: Path):
    archive = tmp_path / "product-1.0.tar.gz"
    archive.touch()
    with pytest.raises(ReleaseArtifactError):
        inspect_artifact(archive, kind="sdist")


def test_release_check_copies_artifact_and_rechecks_digest(tmp_path: Path):
    project = tmp_path / "project"
    output = tmp_path / "out"
    source = project / "dist" / "product-1.0-py3-none-any.whl"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"wheel-bytes")

    [row] = run_release_artifact_checks(
        [{"id": "wheel", "path": "dist/product-1.0-py3-none-any.whl", "kind": "wheel"}],
        project_root=project,
        output_dir=output,
    )

    assert row["passed"] is True
    assert row["artifact_ref"] == "release/wheel/product-1.0-py3-none-any.whl"
    assert (output / row["artifact_ref"]).read_bytes() == b"wheel-bytes"
    assert (output / "release.wheel.receipt.json").is_file()


def test_release_check_reports_missing_output_without_throwing(tmp_path: Path):
    [row] = run_release_artifact_checks(
        [{"id": "wheel", "path": "dist/missing.whl", "kind": "wheel"}],
        project_root=tmp_path / "project",
        output_dir=tmp_path / "out",
    )
    assert row["passed"] is False
    assert row["error_type"] == "ReleaseArtifactError"


def test_release_scenario_runs_all_lifecycle_phases_and_records_each_receipt(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    [row] = run_release_scenarios(
        [{"id": "cycle", "install_command": "printf install", "upgrade_command": "printf upgrade",
          "rollback_command": "printf rollback", "timeout_seconds": 10}],
        project_root=project, output_dir=tmp_path / "out",
    )
    assert row["passed"] is True
    assert [step["command_id"] for step in row["steps"]] == [
        "release.cycle.install", "release.cycle.upgrade", "release.cycle.rollback",
    ]
    assert (tmp_path / "out" / "release.cycle.receipt.json").is_file()


def test_release_scenario_stops_after_failed_phase(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    [row] = run_release_scenarios(
        [{"id": "cycle", "install_command": "exit 4", "upgrade_command": "printf never",
          "rollback_command": "printf never", "timeout_seconds": 10}],
        project_root=project, output_dir=tmp_path / "out",
    )
    assert row["passed"] is False
    assert row["failed_phase"] == "install"
    assert len(row["steps"]) == 1


def test_release_scenario_missing_executable_is_environment_failure(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    [row] = run_release_scenarios(
        [{"id": "missing", "install_command": "definitely-not-installed-zf install",
          "upgrade_command": "definitely-not-installed-zf upgrade",
          "rollback_command": "definitely-not-installed-zf rollback", "timeout_seconds": 1}],
        project_root=project, output_dir=tmp_path / "out",
    )
    assert row["passed"] is False
    assert row["verdict"] == "environment_failure"
