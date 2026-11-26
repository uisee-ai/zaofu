"""Mount an authorized virtual input directory for a configured isolated worker."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.artifact_context_events import artifact_context_events
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_input_recovery import (
    build_input_recovery,
    current_task_input_recovery,
)
from zf.runtime.artifact_mount import (
    MountAccess,
    mounted_scope,
    peer_identity,
    serve_mount,
)
from zf.runtime.artifact_native_delivery import NATIVE_CODEX_VERSIONS
from zf.runtime.artifact_query import ArtifactQueryService
from zf.runtime.artifact_read_capability import (
    authorize_artifact_read_from_environment,
    role_credential_lock,
    _binding_path,
)
from zf.runtime.artifact_read_requirements import required_reads_for_attempt
from zf.runtime.artifact_workdir_view import AttemptFiles, pinned_input_cgroup


def run_workspace(args):
    try:
        ctx = resolve_project_context(
            explicit_state_dir=args.state_dir, require_config=True
        )
        state = ctx.state_dir
        log = event_log_from_project(state, config=ctx.config)
        manifest = load_attempt_source_manifest(state, args.attempt)

        def authorize():
            return authorize_artifact_read_from_environment(
                state, manifest=manifest, event_log=log, use_artifact_context=True
            )

        identity = authorize()
        if not identity.get("operation_id") or not identity.get("attempt_id"):
            raise ValueError(
                "a native workspace requires a current operation and attempt"
            )
        group = mounted_scope(ctx.config, identity["actor"])
        if identity["provider"] != "codex" or pinned_input_cgroup(
            state, log, identity["operation_id"], identity["actor"], identity["attempt_id"]
        ) != group:
            raise ValueError("the original request has no matching Codex worker cgroup")
        peer_identity(os.getpid(), group)
        config_stat = ctx.config_path.stat().st_mtime_ns

        def epoch(expected_generation=None):
            generation = json.loads(_binding_path(state, args.attempt).read_text())["credential_generation"]
            if expected_generation is not None and generation != expected_generation:
                raise ValueError("mounted input lease expired")
            registry = RoleSessionRegistry(
                state / "role_sessions.yaml", ctx.project_root
            )
            sid = str(registry.get(identity["actor"]) or "")
            rollout = registry.get_path(identity["actor"])
            if (
                not sid
                or rollout is None
                or not rollout.is_file()
                or identity != authorize()
                or ctx.config_path.stat().st_mtime_ns != config_stat
            ):
                raise ValueError(
                    "worker session, operation or project configuration changed"
                )
            with rollout.open("rb") as stream:
                meta = json.loads(stream.readline())
                if (
                    meta.get("type") != "session_meta"
                    or (meta.get("payload") or {}).get("id") != sid
                ):
                    raise ValueError(
                        "provider session header does not match its bound rollout"
                    )
            return sid, generation

        with role_credential_lock(state, identity["actor"]):
            original = epoch()

        def check():
            with role_credential_lock(state, identity["actor"]):
                if epoch(original[1]) != original:
                    raise ValueError("mounted input lease expired")
                return original[1]

        events = artifact_context_events(log)
        required = required_reads_for_attempt(
            events, args.attempt, state_dir=state, operation_id=identity["operation_id"]
        )
        query = ArtifactQueryService(
            state_dir=state, project_root=ctx.project_root, config=ctx.config
        )

        def recover():
            from zf.core.task.store import TaskStore

            now = artifact_context_events(log)
            task = TaskStore(state / "kanban.json").get(manifest.get("task_id", ""))
            current = current_task_input_recovery(
                state, now, task, actor=identity["actor"]
            )
            checkpoint = (
                current.get("checkpoint")
                if current.get("identity", {}).get("attempt_id") == args.attempt
                else None
            )
            return build_input_recovery(
                state_dir=state,
                manifest=manifest,
                identity=identity,
                events=now,
                required=required,
                reads=query._read_rows(args.attempt),
                checkpoint=checkpoint,
            )

        view = AttemptFiles(state, manifest, identity, required, recover)
        version = ""
        if identity["provider"] == "codex" and shutil.which("codex"):
            probe = subprocess.run(
                ["codex", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if probe.returncode == 0:
                version = probe.stdout.strip().removeprefix("codex-cli ")
        if version not in NATIVE_CODEX_VERSIONS:
            raise ValueError("this Codex version has no verified native input receipts")
        # mkdtemp atomically reserves a new, private mount point outside project Git.
        with tempfile.TemporaryDirectory(prefix="zf-input-view-", dir="/tmp") as tmp:
            path = Path(tmp)
            if path.is_relative_to(ctx.project_root.resolve()):
                raise ValueError("input view must be outside the product checkout")
            access = MountAccess(state, view, path, group, check, version)
            serve_mount(access)
    except (ConfigError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(
            f"Input workspace unavailable: {exc}. Use zf artifact index/read/recover.",
            file=sys.stderr,
        )
        return 1
    return 0
