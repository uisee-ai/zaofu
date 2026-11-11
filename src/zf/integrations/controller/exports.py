"""Bounded, checkpointed project export; platform credentials never enter the artifact."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import zipfile

from zf.integrations.controller.binding import ManagedDenied
from zf.integrations.controller.commands import digest
from zf.integrations.controller.installation import Installation
from zf.integrations.controller.ledger import Ledger


EXCLUDED = {'.env', '.codex', '.claude', '.ssh', '.aws', '.azure', '.kube', '.config',
            '.venv', 'node_modules', '__pycache__', 'installation', 'controller'}


def sources(contexts):
    paths = {}
    for context in contexts:
        project = context.config.observability.controller.project_id
        for category, root in (('project', context.project_root), ('state', context.state_dir)):
            root = root.resolve()
            if not root.exists():
                continue
            for directory, names, files in os.walk(root, followlinks=False):
                names[:] = sorted(name for name in names if name not in EXCLUDED
                                  and not name.startswith('.env') and not (Path(directory) / name).is_symlink())
                for name in sorted(files):
                    path = Path(directory) / name
                    if (name in EXCLUDED or name.startswith('.env') or name.endswith(('.pem', '.key'))
                            or name in {'auth.json', 'credentials.json'} or path.is_symlink()):
                        continue
                    if path == root / '.git' / 'config':
                        continue
                    relative = path.relative_to(root)
                    if category == 'project' and (context.state_dir == path or context.state_dir in path.parents):
                        continue
                    paths[project + '/' + category + '/' + relative.as_posix()] = path
    return paths


def read_source(path):
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).absolute().parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 64 * 1024 * 1024:
            raise ManagedDenied('export requires bounded regular files without hard links')
        body = stream.read(64 * 1024 * 1024 + 1)
        if len(body) != info.st_size:
            raise ManagedDenied('export source changed during checkpoint')
        return body


def build_export(config, credentials, contexts, job):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', job['id']):
        raise ManagedDenied('invalid export identity')
    selected = set(job['request']['projects'])
    if not selected <= {ctx.config.observability.controller.project_id for ctx in contexts}:
        raise ManagedDenied('export scope changed')
    contexts = [ctx for ctx in contexts if ctx.config.observability.controller.project_id in selected]
    ledger = Ledger(config.state_dir)
    if any(row['status'] in {'running', 'preparing'} and row['body']['project_id'] in selected
           for row in ledger.rows('execution')):
        raise ManagedDenied('export awaits an execution checkpoint')
    root = Installation(config).path / 'exports'
    root.mkdir(mode=0o700, exist_ok=True)
    destination = root / (job['id'] + '.zip')
    temp = root / (job['id'] + '.partial')
    # synchronize_workspace serializes this installation; an old partial has no manifest.
    temp.unlink(missing_ok=True)
    files, checkpoint, total = sources(contexts), {}, 0
    if len(files) > 100000:
        raise ManagedDenied('export file count limit exceeded')
    secrets = [credentials['client_secret'].encode()]
    secrets.extend(value.encode() for name, value in os.environ.items() if len(value) >= 8 and (
        name.startswith(('ZF_CONTROLLER_', 'ZF_CPA_', 'CPA_')) and name.endswith(('SECRET', 'TOKEN', 'KEY'))
        or config.effective_mode == 'managed_cpa' and name in {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'}))
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, path in sorted(files.items()):
                    body = read_source(path)
                    if any(secret in body for secret in secrets):
                        raise ManagedDenied('platform credential found in export source')
                    total += len(body)
                    if total > 512 * 1024 * 1024:
                        raise ManagedDenied('export size limit exceeded')
                    checkpoint[name] = hashlib.sha256(body).hexdigest()
                    archive.writestr(name, body)
                archive.writestr('checkpoint.json', json.dumps({'files': checkpoint,
                    'excluded_credentials': ['controller', 'cpa', 'machine'],
                    'excluded_directories': sorted(EXCLUDED)}, sort_keys=True))
            stream.flush()
            os.fsync(stream.fileno())
        # A changing Project is not presented as a consistent recovery checkpoint.
        if sources(contexts) != files or any(hashlib.sha256(read_source(path)).hexdigest() != checkpoint[name]
                                            for name, path in files.items()):
            raise ManagedDenied('Project changed during export; retry at a quiet checkpoint')
        os.replace(temp, destination)
        with destination.open('rb') as artifact:
            checksum = hashlib.file_digest(artifact, 'sha256').hexdigest()
        return {'artifact_id': job['id'], 'checkpoint_digest': digest(checkpoint), 'sha256': checksum,
                'projects': sorted(selected), 'excluded_credentials': ['controller', 'cpa', 'machine'],
                'size_bytes': destination.stat().st_size}
    finally:
        temp.unlink(missing_ok=True)


def collect_exports(config, credentials, transport, contexts):
    ledger = Ledger(config.state_dir)
    for job in transport.request('GET', '/internal/v1/me/exports'):
        if (job.get('deployment_id') != credentials['deployment_id'] or job.get('generation') != credentials['generation']
                or job.get('workspace_id') != config.workspace_id or job.get('expires_at', 0) <= time.time()):
            raise ManagedDenied('invalid export job binding')
        row = ledger.get('export:' + job['id'])
        if row and row['body']['job'] != job:
            raise ManagedDenied('export identity conflict')
        if row and row['status'] == 'ready':
            manifest = row['body']['manifest']
        else:
            ledger.insert('export:' + job['id'], 'export', 'building', {'job': job})
            manifest = build_export(config, credentials, contexts, job)
            ledger.put('export:' + job['id'], 'ready', {'job': job, 'manifest': manifest})
        transport.request('POST', '/internal/v1/me/exports/' + job['id'], manifest)
    for row in ledger.rows('export'):
        if row['body']['job']['expires_at'] <= time.time():
            (Installation(config).path / 'exports' / (row['body']['job']['id'] + '.zip')).unlink(missing_ok=True)
            ledger.put(row['id'], 'expired', row['body'])


def install_export_route(app, config):
    from fastapi import HTTPException, Request
    from fastapi.responses import FileResponse

    @app.get('/__zf_exports/{export_id}')
    def download(export_id: str, request: Request):
        row = Ledger(config.state_dir).get('export:' + export_id)
        claims = request.scope.get('zf.access', {})
        if (not row or row['status'] != 'ready' or row['body']['job']['owner_id'] != claims.get('sub')
                or row['body']['job']['generation'] != claims.get('generation')
                or row['body']['job']['expires_at'] <= time.time()):
            raise HTTPException(404, 'export_not_found')
        path = Installation(config).path / 'exports' / (row['body']['job']['id'] + '.zip')
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, 'export_not_found')
        return FileResponse(path, media_type='application/zip', filename='zaofu-projects.zip',
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
