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
