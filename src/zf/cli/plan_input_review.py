"""Read-only producer preflight over explicit local artifact paths."""
import json
import sys
from pathlib import Path

from zf.runtime.plan_input_review import inspect_plan_inputs, PORTS


def register(sub):
    parser = sub.add_parser('inspect-inputs', help='Inspect declared task input coverage without approving semantic completeness')
    parser.add_argument('--bundle', required=True, help='JSON mapping Plan port names to local JSON paths')
    parser.add_argument('--task', default='')
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--limit', type=int, default=5)
    parser.set_defaults(func=run)
    scope = sub.add_parser('prepare-scope-review', help='Assemble scope evidence; does not approve or establish runtime eligibility')
    scope.add_argument('--baseline-bundle', required=True)
    scope.add_argument('--candidate-bundle', required=True)
    scope.add_argument('--claim', required=True, help='Agent scope claim JSON; semantic assertions are never inferred')
    scope.set_defaults(func=run_scope)


def _bounded(path: Path, limit: int) -> bytes:
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f'input exceeds {limit} bytes')
    return raw


def run_scope(args):
    from zf.runtime.plan_scope_evidence import prepare_scope_evidence
    try:
        bundles = []
        for filename in (args.baseline_bundle, args.candidate_bundle):
            path = Path(filename).resolve()
            mapping = json.loads(_bounded(path, 8192))
            if not isinstance(mapping, dict) or 'task_map' not in mapping or len(mapping) > 32:
                raise ValueError('bundle must map task_map and complete relevant ports to files')
            bundles.append({name: _bounded(path.parent / ref, 8*1024*1024) for name, ref in mapping.items()})
        claim = json.loads(_bounded(Path(args.claim), 8192))
        if not isinstance(claim, dict):
            raise ValueError('scope claim must be an object')
        result = prepare_scope_evidence(*bundles, claim)
        output = json.dumps(result, ensure_ascii=False)
        if len(output.encode()) > 8192:
            raise ValueError('scope certificate exceeds 8192 bytes')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    print(output)
    return 0


def run(args):
    try:
        path = Path(args.bundle).resolve()
        with path.open('rb') as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError('bundle map exceeds 8192 bytes')
        mapping = json.loads(raw)
        if not isinstance(mapping, dict) or set(mapping)-set(PORTS):
            raise ValueError('bundle contains unsupported Plan ports')
        artifacts = {}
        for name, ref in mapping.items():
            with (path.parent / ref).open('rb') as stream:
                body = stream.read(8*1024*1024+1)
            if len(body) > 8*1024*1024:
                raise ValueError('artifact exceeds 8 MiB')
            artifacts[name] = json.loads(body)
        report = inspect_plan_inputs(artifacts, task_id=args.task, offset=args.offset, limit=args.limit)
    except (OSError, ValueError, TypeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0
