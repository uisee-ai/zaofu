"""Credential-bound input accounting and compatible baseline comparison."""
import argparse
import json
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_input_costs import input_cost_report, compare_input_costs
from zf.runtime.artifact_read_capability import authorize_artifact_read_from_environment
from zf.runtime.artifact_query import ArtifactQueryService


def run_input_costs(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(explicit_state_dir=args.state_dir)
        manifest = load_attempt_source_manifest(context.state_dir, args.attempt)
        log = event_log_from_project(context.state_dir, config=context.config)
        identity = authorize_artifact_read_from_environment(context.state_dir, manifest=manifest, event_log=log)
        service = ArtifactQueryService(state_dir=context.state_dir, project_root=context.project_root, config=context.config)
        result = input_cost_report(state_dir=context.state_dir, manifest=manifest, identity=identity,
            events=log.read_all(), reads=service._read_rows(args.attempt), consumer_actor=identity['actor'])
        if args.compare:
            with Path(args.compare).open('rb') as stream:
                raw = stream.read(8193)
            if len(raw) > 8192:
                raise ValueError('baseline exceeds 8192 bytes')
            result['comparison'] = compare_input_costs(json.loads(raw), result)
        output = json.dumps(result, ensure_ascii=False)
        if len(output.encode()) > 8191:
            raise ValueError('cost report exceeds output budget')
    except (ConfigError, OSError, ValueError, TypeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    print(output)
    return 0
