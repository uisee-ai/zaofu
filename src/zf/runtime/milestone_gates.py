"""Bind actual checks to a milestone without scheduling another execution."""

from __future__ import annotations

import subprocess
from zf.core.workflow.flow_metadata import flow_metadata_for
from pathlib import Path


def clean_commit(root: Path) -> str:
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            timeout=10,
            text=True,
        )

    try:
        status = git("status", "--porcelain=v1", "--untracked-files=normal")
        head = git("rev-parse", "--verify", "HEAD")
        if status.returncode == 0 and not status.stdout and head.returncode == 0:
            return head.stdout.strip()
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        pass
    return ""


def static_target(config, project_root, payload=None):
    metadata = flow_metadata_for(config, payload=payload)
    if metadata.get("milestone_handoff") is True:
        return clean_commit(project_root)
    return ""


def bind_static_result(event, trigger, config, root, initial_commit):
    """Only a kernel-issued bound trigger and an unchanged clean checkout qualify."""
    if not initial_commit or trigger.origin != "kernel":
        return
    keys = (
        "workflow_run_id",
        "task_map_generation",
        "target_commit",
        "contract_snapshot_digest",
    )
    if not all(trigger.payload.get(k) for k in keys):
        return
    if (
        trigger.payload["target_commit"] != initial_commit
        or clean_commit(root) != initial_commit
    ):
        return
    gates = getattr(config, "quality_gates", {}) or {}
    checks = event.payload.get("checks", [])
    commands = [c["command"] for c in checks]
    # A configured alias must have the same complete, ordered command set.
    ids = [
        name
        for name, gate in gates.items()
        if gate.enabled and commands and list(gate.required_checks) == commands
    ]
    if not ids or event.payload.get("skipped"):
        return
    event.payload.update({k: trigger.payload[k] for k in keys})
    event.origin = "kernel"
    event.payload.update(
        gate_id="static",
        static_gate_ids=sorted(ids),
        evidence_refs=["event:" + event.id],
    )


def freeze_gate_commands(config, contract, gates):
    by_command = {}
    for row in contract.get("verification_commands", []):
        by_command.setdefault(row["command"], []).append(row["command_id"])
    frozen = {}
    for gate_id in gates:
        gate = (getattr(config, "quality_gates", {}) or {}).get(gate_id)
        if (
            gate
            and gate.enabled
            and gate.required_checks
            and all(c in by_command for c in gate.required_checks)
        ):
            frozen[gate_id] = sorted(
                {i for cmd in gate.required_checks for i in by_command[cmd]}
            )
    return frozen
