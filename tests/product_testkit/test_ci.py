import json
from pathlib import Path
import subprocess
import sys

import pytest

from .ci import run_ci_matrix


def _fixture(tmp_path: Path, *, fail: bool = False) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "check.py").write_text(
        "raise SystemExit(3)\n" if fail else "print('ok')\n", encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"], ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"], ["git", "add", "check.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1", "project_type": "library",
        "source_root": ".", "target_commit": commit, "setup": [],
        "commands": [{"id": "smoke", "command": f"{sys.executable} check.py", "tier": "unit",
                       "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
                       "mandatory": True, "deterministic": True, "timeout_seconds": 10}],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {},
        "budgets": {}, "not_applicable": {}, "cleanup": [],
    }
    path = project / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return project, path


def test_ci_matrix_keeps_each_tier_and_separate_evidence(tmp_path: Path):
    project, manifest = _fixture(tmp_path)
    summary = run_ci_matrix(
        manifest, project_root=project, output_dir=tmp_path / "ci", run_id="ci-1",
        tiers=("T0", "T1"),
    )
    assert summary["verdict"] == "pass"
    assert summary["all_requested_tiers_completed"] is True
    assert [row["tier"] for row in summary["tiers"]] == ["T0", "T1"]
    assert (tmp_path / "ci" / "T0" / "verification-result.json").is_file()
    assert (tmp_path / "ci" / "T1" / "verification-result.json").is_file()
    assert (tmp_path / "ci" / "T0" / "smoke.stdout.log").is_file()
    assert (tmp_path / "ci" / "T1" / "smoke.stdout.log").is_file()
    assert (tmp_path / "ci" / "ci-matrix.json").is_file()


def test_ci_matrix_aggregate_never_hides_product_failure(tmp_path: Path):
    project, manifest = _fixture(tmp_path, fail=True)
    summary = run_ci_matrix(
        manifest, project_root=project, output_dir=tmp_path / "ci", run_id="ci-fail",
        tiers=("T0", "T3"),
    )
    assert summary["verdict"] == "fail"
    assert all(row["verdict"] == "fail" for row in summary["tiers"])


def test_ci_matrix_does_not_mark_earlier_tier_not_run_for_deferred_domains(tmp_path: Path):
    """A T0 result is scoped to T0 units, not every product capability."""

    project, manifest = _fixture(tmp_path)
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    core = [f"P-{index:02d}" for index in range(1, 6)] + ["P-17", "P-18"]
    raw["commands"][0]["domain_ids"] = core
    raw["capabilities"] = {"release": True}
    raw["release_artifacts"] = [{
        "id": "release-artifact",
        "path": "check.py",
        "kind": "binary",
        "tier": "release",
        "acceptance_ids": ["AC-1"],
        "domain_ids": ["P-15"],
        "mandatory": True,
        "deterministic": True,
    }]
    manifest.write_text(json.dumps(raw), encoding="utf-8")

    summary = run_ci_matrix(
        manifest, project_root=project, output_dir=tmp_path / "ci", run_id="ci-tier-scope",
        tiers=("T0", "T3"),
    )

    assert summary["verdict"] == "pass"
    t0 = json.loads((tmp_path / "ci" / "T0" / "verification-result.json").read_text())
    assert t0["verdict"] == "pass"
    assert t0["domain_coverage"]["missing"] == []
    assert "P-15" not in t0["domain_coverage"]["selected"]
    t3 = json.loads((tmp_path / "ci" / "T3" / "verification-result.json").read_text())
    assert t3["verdict"] == "pass"
    assert "P-15" in t3["domain_coverage"]["selected"]


@pytest.mark.parametrize("tiers", [(), ("T9",), ("T0", "T0")])
def test_ci_matrix_rejects_invalid_tier_selection(tmp_path: Path, tiers):
    project, manifest = _fixture(tmp_path)
    with pytest.raises(ValueError):
        run_ci_matrix(manifest, project_root=project, output_dir=tmp_path / "ci", run_id="x", tiers=tiers)
