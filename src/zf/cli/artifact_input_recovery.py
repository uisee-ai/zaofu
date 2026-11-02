"""Authenticated input reconstruction entry point."""
import argparse
import json
import sys
from time import perf_counter

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.runtime.artifact_context_events import artifact_context_events
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_input_recovery import build_input_recovery, current_task_input_recovery
from zf.runtime.artifact_query import ArtifactQueryService
from zf.runtime.artifact_read_capability import authorize_artifact_read_from_environment
from zf.runtime.artifact_read_requirements import required_reads_for_attempt


def run_input_recovery(args: argparse.Namespace) -> int:
    started = perf_counter()
    try:
        context = resolve_project_context(explicit_state_dir=args.state_dir)
        manifest = load_attempt_source_manifest(context.state_dir, args.attempt)
        log = event_log_from_project(context.state_dir, config=context.config)
        identity = authorize_artifact_read_from_environment(context.state_dir, manifest=manifest,
            event_log=log, use_artifact_context=True)
        events = artifact_context_events(log)
        required = required_reads_for_attempt(events, args.attempt, state_dir=context.state_dir, operation_id=identity["operation_id"])
        service = ArtifactQueryService(state_dir=context.state_dir, project_root=context.project_root, config=context.config)
        from zf.core.task.store import TaskStore

        task = TaskStore(context.state_dir / "kanban.json").get(manifest.get("task_id", ""))
        current = current_task_input_recovery(context.state_dir, events, task, actor=identity["actor"])
        checkpoint = (current.get("checkpoint") if current.get("identity", {}).get("attempt_id") == args.attempt else None)
        result = build_input_recovery(state_dir=context.state_dir, manifest=manifest, identity=identity,
            events=events, required=required, reads=service._read_rows(args.attempt), cursor=args.cursor, checkpoint=checkpoint)
    except (ConfigError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    output = json.dumps(result, ensure_ascii=False)
    print(output, flush=True)
    from zf.runtime.artifact_input_costs import record_input_observation
    record_input_observation(log, manifest, identity, kind="recovery", output=output, elapsed_ms=(perf_counter()-started)*1000)
    return 0
