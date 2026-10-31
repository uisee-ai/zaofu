"""Read-only inspection of a plan against a project's effective execution policy."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.workflow.flow_metadata import flow_metadata_for
from zf.runtime.plan_execution_review import execution_policy, review_execution
from zf.runtime.execution_usage_facts import execution_usage_facts


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("inspect-execution", help="Inspect plan execution facts without approval or dispatch")
    parser.add_argument("--config", type=Path, default=Path("zf.yaml"))
    parser.add_argument("--task-map", type=Path, required=True)
    parser.add_argument("--flow-kind", choices=("prd", "issue", "refactor"), required=True)
    parser.add_argument("--plan-package", type=Path, help="Optional admitted package inside configured state_dir")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config.resolve())
        task_map = json.loads(args.task_map.read_text(encoding="utf-8"))
        if not isinstance(task_map, dict):
            raise ValueError("task map must be an object")
        ports = {"task_map": task_map}
        package_identity = {}
        state_dir = Path(config.project.state_dir)
        if not state_dir.is_absolute():
            state_dir = args.config.resolve().parent / state_dir
        if args.plan_package:
            from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
            from zf.runtime.sidecar_refs import hydrate_sidecar_ref

            state_dir = Path(config.project.state_dir)
            if not state_dir.is_absolute():
                state_dir = args.config.resolve().parent / state_dir
            path = args.plan_package.resolve()
            package = hydrate_plan_artifact_package(state_dir, {
                "ref": path.relative_to(state_dir.resolve()).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
            if package.get("flow_kind") != args.flow_kind:
                raise ValueError("flow kind differs from the admitted package")
            ports = {port["logical_name"]: hydrate_sidecar_ref(state_dir, {
                "ref": port["ref"], "sha256": port["sha256"],
            }).payload for port in package["produced"] + package["inherited"]}
            if ports.get("task_map") != task_map:
                raise ValueError("task map differs from the admitted package")
            package_identity = package
        metadata = flow_metadata_for(config, args.flow_kind)
        report = review_execution(
            task_map, ports=ports, policy=execution_policy(config), metadata=metadata,
            identity=package_identity,
            usage_facts=execution_usage_facts(
                state_dir, workflow_run_id=str(package_identity.get("workflow_run_id") or ""),
            ),
        )
        report["config_source"] = str(args.config.resolve())
        report["config_scope"] = "current_file_not_frozen_run"
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
