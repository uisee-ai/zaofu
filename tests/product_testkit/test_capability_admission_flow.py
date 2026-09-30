"""Cross-module admission flow for a capability-complete product manifest."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .runner import run_manifest_and_collect


def _git_product(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "product"
    project.mkdir()
    (project / "smoke.py").write_text("print('smoke')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "smoke.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True
    ).strip()
    return project, commit


def test_strict_runner_records_selected_and_not_applicable_capabilities(tmp_path: Path):
    project, commit = _git_product(tmp_path)
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "library",
        "source_root": ".",
        "target_commit": commit,
        "setup": [],
        "commands": [{
            "id": "smoke",
            "command": "python smoke.py",
            "tier": "unit",
            "acceptance_ids": ["AC-1"],
            "domain_ids": ["P-01", "P-02", "P-03", "P-04", "P-05", "P-17", "P-18"],
            "mandatory": True,
            "deterministic": True,
            "timeout_seconds": 20,
        }],
        "acceptances": [{"id": "AC-1"}],
        "entrypoints": [],
        "capabilities": {"api": False, "web": False},
        "budgets": {},
        "not_applicable": {
            "api": {"reason": "library has no HTTP surface", "source": "project profile"},
            "web": {"reason": "library has no browser surface", "source": "project profile"},
        },
        "cleanup": [],
    }
    manifest_path = project / "product-test-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = run_manifest_and_collect(
        manifest_path,
        project_root=project,
        output_dir=tmp_path / "out",
        run_id="capability-flow",
    )

    assert result["verdict"] == "pass"
    assert result["domain_coverage"]["missing"] == []
    rows = {row["capability"]: row for row in result["domains"]}
    assert rows["api"]["status"] == "not_applicable"
    assert rows["web"]["status"] == "not_applicable"
    persisted = json.loads(
        (tmp_path / "out" / "verification-result.json").read_text(encoding="utf-8")
    )
    assert persisted["domains"] == result["domains"]

