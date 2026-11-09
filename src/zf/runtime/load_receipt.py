"""Startup evidence of the effective config and code executing in this process."""

from dataclasses import asdict
import hashlib
import json
import marshal
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from zf.runtime.sidecar_refs import write_sidecar_json


def runtime_load_payload(*, config, state_dir: Path, project_root: Path) -> dict:
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.run_manager import run_manager_tick
    from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler

    functions = (Orchestrator.run_once, run_manager_tick, TaskPipelineReconciler.reconcile)
    code = {f"{fn.__module__}.{fn.__qualname__}": {
        "file": fn.__code__.co_filename,
        "loaded_code_sha256": hashlib.sha256(marshal.dumps(fn.__code__)).hexdigest(),
    } for fn in functions}
    source_root = Path(__file__).resolve().parents[3]
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source_root,
                              capture_output=True, text=True, timeout=5)
    source_files = {str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted((source_root / "src" / "zf").rglob("*.py"))}
    config_digest = hashlib.sha256(json.dumps(asdict(config), sort_keys=True, default=str).encode()).hexdigest()
    config_path = project_root / "zf.yaml"
    lockfile = state_dir / "skills.lock.json"
    body = {
        "schema_version": "runtime-load.v1", "pid": os.getpid(), "python": sys.executable,
        "source_root": str(source_root), "source_revision": revision.stdout.strip() if revision.returncode == 0 else "",
        "source_tree_sha256": hashlib.sha256(json.dumps(source_files, sort_keys=True).encode()).hexdigest(),
        "loaded_consumers": code, "effective_config_sha256": config_digest,
        "config_file_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path.is_file() else "",
        "state_dir": str(state_dir.resolve()),
        "normal_frontier_owner": config.workflow.orchestration.normal_frontier_owner,
        "task_pipeline": config.workflow.flow_metadata.get("task_pipeline"),
        "skills_lock_sha256": hashlib.sha256(lockfile.read_bytes()).hexdigest() if lockfile.is_file() else "",
        "roles": [{"instance_id": role.instance_id or role.name, "backend": role.backend,
                   "skills": role.skills, "constraints": asdict(role.constraints)} for role in config.roles],
        "limits": "Startup snapshot; per-attempt provenance proves later delivery, not model compliance.",
    }
    ref = write_sidecar_json(state_dir, f"diagnostics/runtime-load/{uuid4().hex}.json", body,
        kind="runtime-load", schema_version="runtime-load.v1", created_by="zf-cli", required=True)
    return {"runtime_load_ref": ref, "effective_config_sha256": config_digest, "pid": os.getpid()}
