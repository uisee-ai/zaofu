from __future__ import annotations

import subprocess
from pathlib import Path

from .artifacts import capture_artifact
from .executor import run_product_plan
from .manifest import ProductManifest, validate_manifest
from .matrix import validate_matrix


def test_generic_foundation_fixture_runs_p01_to_p05(tmp_path: Path):
    root = tmp_path / "product"
    root.mkdir()
    (root / "product.py").write_text("def main():\n    return 'ok'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "TestKit"], cwd=root, check=True)
    subprocess.run(["git", "add", "product.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "library",
        "source_root": ".",
        "target_commit": commit,
        "setup": [],
        "commands": [{"id": "foundation-smoke", "command": "python -c 'from product import main; print(main())'",
                       "tier": "unit", "acceptance_ids": ["AC-foundation"],
                       "domain_ids": ["P-01", "P-02", "P-03", "P-04", "P-05", "P-17", "P-18"],
                       "mandatory": True, "deterministic": True, "timeout_seconds": 30}],
        "acceptances": [{"id": "AC-foundation"}],
        "entrypoints": [], "capabilities": {"library": True}, "budgets": {},
        "not_applicable": {"web": {"reason": "fixture has no web surface", "source": "fixture profile"}}, "cleanup": [],
    }
    validate_manifest(manifest)
    validate_matrix(
        capabilities=[{"id": "cap.foundation", "blocking": True}],
        acceptances=[{"id": "AC-foundation", "capability_ids": ["cap.foundation"]}],
        commands=manifest["commands"],
    )
    output = tmp_path / "output"
    result = run_product_plan(ProductManifest(manifest), project_root=root, output_dir=output,
                              verify_commit=True, enforce_domain_coverage=True)
    assert result.passed
    record = capture_artifact(output / "foundation-smoke.stdout.log", root=output)
    assert record["ref"] == "foundation-smoke.stdout.log"
