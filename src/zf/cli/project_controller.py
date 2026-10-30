"""Project bindings for the Dashboard's versioned PRD controller template."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

from zf.cli.flow_draft_support import default_tmux_session
from zf.core.profile.detector import declared_profile, detect
from zf.core.profile.flows import read_flow_yaml
from zf.core.profile.schema import ProjectProfile

DEFAULT_PROJECT_CONTROLLER = "zaofu-prd-v5-production-codex"


def draft_project_controller(
    *, controller_id: str, project_root: Path, project_name: str,
    project_description: str = "", backend: str = "codex", verify_backend: str = "",
    state_dir: str = "", lanes: int = 0, source_ref: str = "", target_root: str = "",
    stack: str = "", surface: str = "",
) -> list[dict[str, Any]]:
    if controller_id != DEFAULT_PROJECT_CONTROLLER:
        raise ValueError("unsupported project controller")
    content = read_flow_yaml(controller_id)
    if content is None:
        raise ValueError("registered PRD v5 project controller is unavailable")
    docs = list(yaml.safe_load_all(content))
    flow = next(d["spec"] for d in docs if d["kind"] == "PrdFlow")
    spec = next(d["spec"] for d in docs if d["kind"] == "ZfConfig")
    if lanes and lanes != flow["lanes"]:
        raise ValueError("PRD v5 initialization preserves its lane pools; revise the Workflow configuration after initialization")
    flow.update(backend=backend, targetRoot=target_root or ".")
    if source_ref:
        flow["prdRef"] = source_ref
    if verify_backend:
        flow["verifyBackend"] = verify_backend
    if backend != "codex" or verify_backend:
        # A Codex model must not be sent to a Claude primary or verify lane.
        # Mixed teams use each provider's default until an explicit revision.
        flow["roleDefaults"].pop("model", None)
    spec["project"] = {
        "name": project_name, "description": project_description,
        "state_dir": state_dir or f".zf-{project_name}",
    }
    spec["session"] = {"tmux_session": f"${{ZF_TMUX_SESSION:-{default_tmux_session(project_name)}}}"}
    spec["orchestrator"]["backend"] = backend
    for role in spec.get("roles", []):
        role["backend"] = backend
    runtime = spec["runtime"]
    runtime["run_manager"]["backend"] = backend
    branch = subprocess.run(
        ["git", "-C", str(project_root), "symbolic-ref", "--short", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    branch_name = branch.stdout.strip() if branch.returncode == 0 else "main"
    runtime["git"].update(candidate_base_ref=branch_name, ship_target_branch=branch_name)
    runtime["workdirs"]["provision_paths"] = [
        path for path in runtime["workdirs"].get("provision_paths", [])
        if (project_root / path).exists()
    ]
    profile = declared_profile(stack, surface) if stack else (
        detect(project_root) if project_root.is_dir() else ProjectProfile()
    )
    # Keep the static gate, binding its commands to this project's stack rather
    # than the ZaoFu repository's src/tests + web toolchain.
    spec["quality_gates"]["static"]["required_checks"] = list(profile.all_gate_cmds)
    return docs
