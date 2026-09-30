from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from .runner import run_manifest_and_collect


def _docker_available() -> bool:
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_available(), reason="Docker daemon unavailable")
def test_manifest_container_lifecycle_is_part_of_product_run(tmp_path: Path):
    if subprocess.run(
        ["docker", "image", "inspect", "alpine:3.20"], capture_output=True
    ).returncode != 0:
        pytest.skip("alpine fixture image unavailable")
    project = tmp_path / "project"
    project.mkdir()
    (project / "product.txt").write_text("fixture\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "product.txt"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True
    ).strip()
    domains = [f"P-{index:02d}" for index in range(1, 19)]
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "library",
        "source_root": ".",
        "target_commit": commit,
        "setup": [],
        "commands": [{
            "id": "container-smoke",
            "command": (
                f"{sys.executable} -c \"import os; assert os.environ['ZF_CONTAINER_DB_NAME'].startswith('zf-product-db-')\""
            ),
            "tier": "integration",
            "acceptance_ids": ["AC-1"],
            "domain_ids": domains,
            "mandatory": True,
            "deterministic": True,
            "timeout_seconds": 20,
        }],
        "acceptances": [{"id": "AC-1"}],
        "entrypoints": [],
        "capabilities": {},
        "budgets": {},
        "not_applicable": {},
        "cleanup": [],
        "dependency_containers": [{
            "id": "db",
            "image": "alpine:3.20",
            "command": "while true; do sleep 1; done",
            "health_command": "test -f /etc/alpine-release",
            "ports": [],
            "timeout_seconds": 10,
            "mandatory": True,
        }],
    }
    manifest_path = project / "product-test-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "evidence"

    result = run_manifest_and_collect(
        manifest_path, project_root=project, output_dir=output, run_id="container-e2e"
    )

    assert result["verdict"] == "pass"
    [container] = result["containers"]
    assert container["healthy"] is True
    assert container["stopped"] is True
    assert container["passed"] is True
    assert container["name"].startswith("zf-product-db-")
    indexed = {row["ref"] for row in result["artifact_index"]}
    assert any(ref.startswith("container.") and ref.endswith("receipt.json") for ref in indexed)
    assert not subprocess.run(
        ["docker", "inspect", container["name"]], capture_output=True
    ).returncode == 0
