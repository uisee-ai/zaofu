"""Export selected, authorized baseline files for a Planner's local replan draft."""

from __future__ import annotations

import io
import hashlib
import json
import shutil
import sys
import tarfile
from pathlib import Path
from tempfile import TemporaryFile, mkdtemp

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import EventSigningConfigError, event_log_from_project
from zf.runtime.artifact_access import require_artifact_access
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_read_capability import authorize_artifact_read_from_environment
from zf.runtime.plan_candidate_ports import read_plan_port_bytes
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.sidecar_refs import sidecar_path


# These JSON bodies are edited by the Planner. Approvals, claims and source
# requirements are issued by their own authorities.
_DRAFT_PORTS = ("task_map", "test_matrix", "source_index", "source_inventory",
                "acceptance_matrix", "real_e2e_matrix", "capability_matrix", "coverage_ledger",
                "execution_spec", "task_execution_input", "product_acceptance_spec")


def register(sub):
    parser = sub.add_parser("draft-baseline", help="Export pinned Plan ports as a tar stream for local editing")
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--port", action="append", required=True, choices=_DRAFT_PORTS, help="Logical port name, repeat to include more")
    parser.add_argument("--workspace", action="store_true", help="Create an editable private draft in this Planner workdir")
    parser.add_argument("--seed-goals", action="store_true", help="Preserve pinned goals in a local task map draft")
    parser.set_defaults(func=run)


def _source(manifest, source_id, identity):
    matches = [s for s in manifest["sources"] if s["source_id"] == source_id]
    if len(matches) != 1:
        raise ValueError(f"expected one assigned source for {source_id}")
    source = matches[0]
    require_artifact_access(source.get("access_scope"), actor=identity["actor"],
                            role=identity["role"], purpose=identity["purpose"])
    if "$" not in (source.get("allowed_paths") or ["$"]):
        raise ValueError(f"whole-file access is not granted for {source_id}")
    return source


def draft_files(state, manifest, identity, names, *, seed_goals=False):
    if identity["role"] != "planner" or not identity["operation_id"]:
        raise ValueError("a current Planner operation is required")
    package_source = _source(manifest, "plan-rework-package", identity)
    if (package_source["ref"] != manifest.get("plan_artifact_package_ref")
            or package_source["sha256"] != manifest.get("plan_artifact_package_digest")):
        raise ValueError("replan package differs from the assigned baseline")
    package = hydrate_plan_artifact_package(state, package_source, validate_ports=False)
    if (package["workflow_run_id"] != manifest["workflow_run_id"]
            or package["task_map_generation"] != manifest.get("task_map_generation")):
        raise ValueError("replan baseline Run or generation differs")
    ports = {p["logical_name"]: p for p in [*package["produced"], *package["inherited"]]}
    def assigned_bytes(name):
        if name not in ports:
            raise ValueError(f"unknown baseline port: {name}")
        source = _source(manifest, f"baseline-plan-port-{name}", identity)
        if any(source[key] != ports[name][key] for key in ("ref", "sha256")):
            raise ValueError(f"baseline descriptor differs for {name}")
        raw = read_plan_port_bytes(sidecar_path(state, source["ref"]), name)
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError(f"baseline bytes differ for {name}")
        return raw

    for name in dict.fromkeys(names):
        if name not in _DRAFT_PORTS:
            raise ValueError(f"unknown editable port: {name}")
        raw = assigned_bytes(name)
        if seed_goals and name == "task_map":
            from zf.runtime.goal_claim_set import validate_goal_claim_set
            from zf.runtime.task_map_goal_coverage import validate_goal_coverage

            goal_bytes = assigned_bytes("goal_claim_set")
            goals = validate_goal_claim_set(json.loads(goal_bytes),
                workflow_run_id=package["workflow_run_id"], task_map_generation=package["task_map_generation"])
            task_map = json.loads(raw)
            if not task_map.get("goal_claims"):
                candidate = {**task_map, "goal_claims": goals["claims"]}
                coverage, errors = validate_goal_coverage(candidate, task_map.get("tasks", []))
                if not errors and not coverage["diagnostics"]:
                    raw = json.dumps(candidate, ensure_ascii=False, indent=2).encode() + b"\n"
                else:
                    # A legacy map may contain inert labels while its admitted
                    # claims derive from ACs. Do not force a mapping migration
                    # that changes completed Task rows merely to make a draft.
                    yield "baseline-goal-claims.json", goal_bytes
        yield f"{name}.json", raw


def write_draft(state, manifest, identity, names, stream):
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for filename, raw in draft_files(state, manifest, identity, names):
            entry = tarfile.TarInfo(filename)
            entry.mode = 0o600
            entry.size = len(raw)
            archive.addfile(entry, io.BytesIO(raw))


def workspace_draft(context, manifest, identity, names, *, seed_goals=False):
    from zf.core.safety.path_guard import assert_owned_workdir
    from zf.runtime.workdirs import WorkdirManager

    manager = WorkdirManager(state_dir=context.state_dir, project_root=context.project_root, config=context.config)
    role_dir = manager.root / identity["actor"]
    marker = assert_owned_workdir(role_dir, state_dir=context.state_dir, workdir_root=manager.root)
    project = role_dir / "project"
    if (marker.instance_id != identity["actor"] or Path(marker.project_root) != context.project_root
            or project != Path.cwd() or project != project.resolve()):
        raise ValueError("draft must be created in the bound Planner project")
    folder = project / "artifacts" / "plan"
    if folder.resolve() != folder:
        raise ValueError("draft output must stay in the Planner project")
    files = list(draft_files(context.state_dir, manifest, identity, names, seed_goals=seed_goals))
    folder.mkdir(parents=True, exist_ok=True)
    draft = Path(mkdtemp(prefix="replan-", dir=folder))
    try:
        for filename, raw in files:
            (draft / filename).write_bytes(raw)
    except BaseException:
        shutil.rmtree(draft)
        raise
    result = {"draft_dir": str(draft.relative_to(project)), "files": [filename for filename, _ in files]}
    if "baseline-goal-claims.json" in result["files"]:
        result["goal_seeding"] = {
            "status": "legacy_derived_preserved",
            "reference_only": "baseline-goal-claims.json",
            "reason": "Explicit seeding would change Task bindings. Keep completed rows intact; "
                      "the normal derived-claim continuity checks still apply. This reference is not a Plan output port.",
        }
    return result


def run(args):
    try:
        if args.seed_goals and (not args.workspace or "task_map" not in args.port):
            raise ValueError("--seed-goals requires --workspace and --port task_map")
        if not args.workspace and sys.stdout.isatty():
            raise ValueError("redirect the archive to a file or pipe")
        context = resolve_project_context(explicit_state_dir=args.state_dir, require_config=args.workspace)
        manifest = load_attempt_source_manifest(context.state_dir, args.attempt)
        log = event_log_from_project(context.state_dir, config=context.config)
        try:
            identity = authorize_artifact_read_from_environment(
                context.state_dir, manifest=manifest, event_log=log, use_artifact_context=True,
            )
        finally:
            log.close()
        if args.workspace:
            print(json.dumps(workspace_draft(context, manifest, identity, args.port, seed_goals=args.seed_goals)))
            return 0
        # Validate every member before publishing even the first byte.
        with TemporaryFile() as archive:
            write_draft(context.state_dir, manifest, identity, args.port, archive)
            archive.seek(0)
            shutil.copyfileobj(archive, sys.stdout.buffer)
    except (ConfigError, EventSigningConfigError, KeyError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
