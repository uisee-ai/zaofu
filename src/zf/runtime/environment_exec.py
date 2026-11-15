"""Exec a native provider with inherited resource locks (stdlib only)."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys


def path_identity(path: str) -> list[int] | None:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return None
    return [stat.st_dev, stat.st_ino, stat.st_mode]


def validate_bindings(plan: dict) -> None:
    for logical, target in plan['bindings'].items():
        if os.path.realpath(logical) != target:
            raise OSError('environment resource binding changed; prepare again')
        if path_identity(logical) != plan['instances'][logical]:
            raise OSError('environment resource instance changed; prepare again')
    manifest = Path(plan['manifest'])
    actual = hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.exists() else None
    if actual != plan['manifest_digest']:
        raise OSError('environment resource declaration changed; prepare again')


@contextmanager
def resource_locks(plan: dict, mode: int):
    descriptors = []
    try:
        for name in plan['locks']:
            path = Path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            descriptors.append(descriptor)
            fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
        validate_bindings(plan)
        yield descriptors
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[2] != '--':
        return 64
    try:
        plan = json.loads(sys.argv[1])
        with resource_locks(plan, fcntl.LOCK_SH) as descriptors:
            for descriptor in descriptors:
                os.set_inheritable(descriptor, True)
            os.execvp(sys.argv[3], sys.argv[3:])
    except (OSError, ValueError, KeyError):
        print('Environment is busy or changed; provider launch must wait.', file=sys.stderr)
        return 75
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
