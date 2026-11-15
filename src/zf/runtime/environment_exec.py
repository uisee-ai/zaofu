"""Exec a native provider with inherited resource locks (stdlib only)."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys

RESOURCE_ENV = 'ZF_ENVIRONMENT_RESOURCES'


def register_process_holder(plan: dict) -> None:
    """Persist PID/start identity before exec, so inaccessible known holders block."""
    fields = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
    body = json.dumps({'pid': os.getpid(), 'start_ticks': fields[19]})
    for lock in plan['locks']:
        directory = Path(lock + '.holders')
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f'{os.getpid()}-{fields[19]}.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(body)
        os.replace(temporary, target)


def registered_holders(plan: dict) -> list[dict]:
    holders = {}
    for lock in plan['locks']:
        for receipt in Path(lock + '.holders').glob('*.json'):
            try:
                body = json.loads(receipt.read_text())
                pid = int(body['pid'])
                fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                if fields[0] == 'Z' or fields[19] != body['start_ticks']:
                    receipt.unlink(missing_ok=True)
                    continue
                holders[pid] = {**body, 'reason': 'registered_provider'}
            except FileNotFoundError:
                receipt.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, IndexError):
                holders[str(receipt)] = {'pid': 'unknown', 'reason': 'registered_identity_unknown'}
    return list(holders.values())


def resource_environment(plan: dict) -> dict[str, str]:
    resources = sorted(set([plan.get('workspace', ''), *plan['bindings'].values()]) - {''})
    return {RESOURCE_ENV: json.dumps(resources)}


def resource_holders(plan: dict) -> list[dict]:
    """Linux host evidence beyond advisory locks, including detached children.

    Controlled providers inherit a non-secret resource declaration even if they
    close lock descriptors or change cwd. Unregistered same-user holders are
    found through cwd, executable, open files and mappings. This is not a
    sandbox against a process intentionally hiding its resource use.
    """
    proc = Path('/proc')
    if not (proc / 'self' / 'stat').exists():
        raise OSError('environment holder inspection requires Linux procfs')
    resources = json.loads(resource_environment(plan)[RESOURCE_ENV])
    def intersects(value: str) -> bool:
        value = value.removesuffix(' (deleted)')
        return any(value == root or value.startswith(root.rstrip('/') + '/') for root in resources)

    ancestors = set()
    pid = os.getpid()
    while pid and pid not in ancestors:
        ancestors.add(pid)
        try:
            pid = int((proc / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    holders = registered_holders(plan)
    marker = (RESOURCE_ENV + '=').encode()
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] == 'Z':
                continue
            declarations = [part[len(marker):] for part in (entry / 'environ').read_bytes().split(b'\0')
                            if part.startswith(marker)]
            declared = json.loads(declarations[0]) if declarations else []
            reason = 'declared_resource' if any(isinstance(root, str) and intersects(root) for root in declared) else ''
            if not reason and int(entry.name) in ancestors:
                continue  # Interactive operators are not workspace holders merely by cwd.
            if not reason:
                links = [entry / 'cwd', entry / 'exe', *list((entry / 'fd').iterdir())]
                for link in links:
                    try:
                        target = os.readlink(link)
                    except FileNotFoundError:
                        continue
                    if intersects(target):
                        reason = 'open_resource'
                        break
            if not reason:
                with (entry / 'maps').open() as handle:
                    for line in handle:
                        parts = line.split(maxsplit=5)
                        if len(parts) == 6 and intersects(parts[5].strip()):
                            reason = 'mapped_resource'
                            break
            if reason:
                holders.append({'pid': int(entry.name), 'start_ticks': fields[19], 'reason': reason})
        except (FileNotFoundError, ProcessLookupError):
            continue  # Process exited during this snapshot.
        except (PermissionError, ValueError, IndexError):
            # Unrelated host processes may be non-dumpable. Known managed
            # holders are fenced by PID/start receipts above; do not turn an
            # unrelated inaccessible process into a global installer outage.
            continue
    return holders


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
        if mode == fcntl.LOCK_EX:
            holders = resource_holders(plan)
            if holders:
                raise BlockingIOError('environment resource has live or unknown process holders: '
                                      + ','.join(str(row['pid']) for row in holders))
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
            register_process_holder(plan)
            os.execvpe(sys.argv[3], sys.argv[3:], {**os.environ, **resource_environment(plan)})
    except (OSError, ValueError, KeyError):
        print('Environment is busy or changed; provider launch must wait.', file=sys.stderr)
        return 75
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
