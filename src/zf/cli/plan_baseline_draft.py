"""Export selected, authorized baseline files for a Planner's local replan draft."""

from __future__ import annotations

import io
import hashlib
import shutil
import sys
import tarfile
from tempfile import TemporaryFile

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
                "acceptance_matrix", "real_e2e_matrix", "capability_matrix", "coverage_ledger")


def register(sub):
    parser = sub.add_parser("draft-baseline", help="Export pinned Plan ports as a tar stream for local editing")
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--port", action="append", required=True, choices=_DRAFT_PORTS, help="Logical port name, repeat to include more")
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


def write_draft(state, manifest, identity, names, stream):
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
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name in dict.fromkeys(names):
            if name not in _DRAFT_PORTS or name not in ports:
                raise ValueError(f"unknown baseline port: {name}")
            source = _source(manifest, f"baseline-plan-port-{name}", identity)
            if any(source[key] != ports[name][key] for key in ("ref", "sha256")):
                raise ValueError(f"baseline descriptor differs for {name}")
            raw = read_plan_port_bytes(sidecar_path(state, source["ref"]), name)
            if hashlib.sha256(raw).hexdigest() != source["sha256"]:
                raise ValueError(f"baseline bytes differ for {name}")
            entry = tarfile.TarInfo(f"{name}.json")
            entry.mode = 0o600
            entry.size = len(raw)
            archive.addfile(entry, io.BytesIO(raw))


def run(args):
    try:
        if sys.stdout.isatty():
            raise ValueError("redirect the archive to a file or pipe")
        context = resolve_project_context(explicit_state_dir=args.state_dir)
        manifest = load_attempt_source_manifest(context.state_dir, args.attempt)
        log = event_log_from_project(context.state_dir, config=context.config)
        try:
            identity = authorize_artifact_read_from_environment(
                context.state_dir, manifest=manifest, event_log=log, use_artifact_context=True,
            )
        finally:
            log.close()
        # Validate every member before publishing even the first byte.
        with TemporaryFile() as archive:
            write_draft(context.state_dir, manifest, identity, args.port, archive)
            archive.seek(0)
            shutil.copyfileobj(archive, sys.stdout.buffer)
    except (ConfigError, EventSigningConfigError, KeyError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
