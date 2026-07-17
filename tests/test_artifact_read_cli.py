from __future__ import annotations

import hashlib
import json
from pathlib import Path

from zf.cli.main import main
from zf.runtime.artifact_read_ledger import (
    build_attempt_source_manifest,
    write_attempt_source_manifest,
)


def test_artifact_list_and_read_cli_record_attempt_ledger(
    tmp_path: Path,
    capsys,
) -> None:
    state_dir = tmp_path / ".zf"
    artifact = state_dir / "artifacts" / "inputs" / "facts.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({"facts": ["one", "two"]}), encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-cli",
        task_id="T-CLI",
        attempt_id="attempt-cli",
        dispatch_id="attempt-cli",
        sources=[{
            "source_id": "context",
            "artifact_id": "facts",
            "ref": "artifacts/inputs/facts.json",
            "sha256": digest,
            "allowed_paths": ["$.facts"],
        }],
    )
    write_attempt_source_manifest(state_dir, manifest)

    assert main([
        "artifact", "list", "--attempt", "attempt-cli",
        "--state-dir", str(state_dir),
    ]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["sources"][0]["artifact_id"] == "facts"

    assert main([
        "artifact", "read", "--attempt", "attempt-cli",
        "--source", "context", "--artifact", "facts",
        "--json-path", "$.facts", "--state-dir", str(state_dir),
    ]) == 0
    assert '"one"' in capsys.readouterr().out
    assert (
        state_dir / "artifacts/attempts/attempt-cli/read-ledger.active.jsonl"
    ).exists()
