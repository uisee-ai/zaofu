from __future__ import annotations

import json
from pathlib import Path

import yaml

from zf.cli.project import init_flow_project


def test_prd_project_init_snapshots_external_source(tmp_path: Path) -> None:
    external = tmp_path / "source" / "requirements.md"
    external.parent.mkdir()
    external.write_text("# Product requirements\n", encoding="utf-8")
    project_root = tmp_path / "project"

    result = init_flow_project(
        kind="prd",
        name="demo",
        project_root=project_root,
        source_ref=str(external),
        request_kind="prd",
        backend="claude-code",
        lanes=1,
        state_dir=".zf",
        request_id="demo-request",
        create_root=True,
        workspace_register=False,
    )

    local_ref = "docs/prd/requirements.md"
    assert (project_root / local_ref).read_text(encoding="utf-8") == (
        "# Product requirements\n"
    )
    docs = list(yaml.safe_load_all((project_root / "zf.yaml").read_text()))
    assert docs[0]["spec"]["prdRef"] == local_ref
    manifest = json.loads(
        (
            project_root
            / "artifacts/workflow/demo-request/workflow-input-manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["source_ref"] == local_ref
    assert result["request"]["request_id"] == "demo-request"


def test_project_init_canonicalizes_claude_product_alias(tmp_path: Path) -> None:
    project_root = tmp_path / "project"

    result = init_flow_project(
        kind="prd",
        name="demo",
        project_root=project_root,
        objective="Build a deterministic text counter.",
        backend="claude",
        lanes=1,
        state_dir=".zf",
        create_root=True,
        workspace_register=False,
    )

    docs = list(yaml.safe_load_all((project_root / "zf.yaml").read_text()))
    assert docs[0]["spec"]["backend"] == "claude-code"
    assert docs[0]["spec"]["prdRef"] == "docs/intake/project-init-request.md"
    assert docs[0]["spec"]["targetRoot"] == "."
    profile = next(doc for doc in docs if doc["kind"] == "ConfigProfile")
    assert profile["spec"]["runtime"]["run_manager"]["backend"] == "claude-code"
    request = json.loads(
        Path(result["request"]["workflow_input_manifest_ref"]).read_text(
            encoding="utf-8"
        )
    )
    assert request["requested_backend"] == "claude-code"
    assert request["source_ref"] == "docs/intake/project-init-request.md"
    assert result["readiness"] == {
        "launch_ready": True,
        "missing_required_fields": [],
        "source_ref": "docs/intake/project-init-request.md",
    }
    assert result["next_actions"]
