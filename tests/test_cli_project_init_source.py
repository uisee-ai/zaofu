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
