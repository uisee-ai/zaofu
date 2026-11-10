"""Execute trusted fixture contracts; these receipts never imply a real provider."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from zf.runtime.call_result_envelope import write_immutable_json_sidecar


class ContractProbeFailed(AssertionError):
    def __init__(self, command_id: str, exit_code: int, evidence: dict[str, Any]):
        super().__init__(f"contract command {command_id} exited {exit_code}")
        self.evidence = evidence


def run_contract_probe(
    *, state_dir: Path, workdir: Path, target_commit: str,
    command: dict[str, Any], receipt_id: str,
) -> dict[str, Any]:
    """Bind a real command result to the fixture's immutable candidate and sidecar."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workdir,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if head != target_commit:
        raise AssertionError(f"probe target mismatch: {head} != {target_commit}")
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=workdir)
    if dirty.returncode:
        raise AssertionError("probe target has uncommitted tracked changes")
    # Only repository-owned test fixture commands reach this helper. Shell is
    # necessary to execute the literal contract string, not its JSON encoding.
    result = subprocess.run(
        command["command"], shell=True, cwd=workdir,
        capture_output=True, text=True, timeout=command.get("timeout_seconds", 30),
    )
    evidence = write_immutable_json_sidecar(
        state_dir,
        {
            "command_id": command["command_id"],
            "command": command["command"],
            "cwd": str(workdir),
            "target_commit": target_commit,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "method": "local-command/mock-provider",
        },
        root="e2e/command-evidence", kind="fixture_command_evidence",
        schema_version="fixture-command-evidence.v1", created_by="pytest",
    )
    if result.returncode:
        raise ContractProbeFailed(command["command_id"], result.returncode, evidence)
    return {
        "receipt_id": receipt_id,
        "command_id": command["command_id"],
        "command_digest": command["command_digest"],
        "command": command["command"],
        "target_commit": target_commit,
        "status": "passed",
        "exit_code": result.returncode,
        "evidence_refs": [evidence],
    }
