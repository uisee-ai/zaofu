"""Rebuild read-only media projections from authenticated container evidence."""
from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath

from zf.core.config.host_verification import HostVerificationConfig, parse_host_verification
from zf.runtime.sidecar_refs import (
    build_sidecar_ref_descriptor, hydrate_sidecar_ref, write_sidecar_json,
)


def materialize_container_media(state_dir: Path, receipt_refs: list[dict], *, image: str,
                                policy: HostVerificationConfig | None = None) -> list[dict]:
    """No command execution or verdict; originals remain the required evidence."""
    configured = policy or HostVerificationConfig()
    limits = parse_host_verification({key: getattr(configured, key) for key in (
        'max_artifact_bytes', 'max_total_evidence_bytes')})
    indices = []
    seen = set()
    for receipt_ref in receipt_refs:
        receipt = hydrate_sidecar_ref(state_dir, receipt_ref).payload
        evidence_ref = receipt.get('evidence_ref')
        if not evidence_ref:
            continue  # Historical/non-container receipts have no media.
        source = hydrate_sidecar_ref(state_dir, evidence_ref, max_bytes=200 * 1024 * 1024)
        evidence = source.payload
        if (receipt.get('execution_origin') != 'preauthorized_container'
                or receipt.get('source_stable') is not True
                or receipt.get('image') != image or evidence.get('image') != image
                or not receipt.get('target_commit')
                or evidence.get('target_commit') != receipt['target_commit']):
            raise ValueError('container media differs from the bound receipt')
        if source.sha256 in seen:
            continue
        seen.add(source.sha256)
        files = evidence.get('files')
        if not isinstance(files, list) or len(files) > 256:
            raise ValueError('container media file list is invalid')
        decoded = []
        names = set()
        total_bytes = 0
        for item in files:
            if not isinstance(item, dict):
                raise ValueError('container media file entry is invalid')
            name = item.get('path', '')
            if (not isinstance(name, str) or not name or '\\' in name
                    or any(ord(c) < 32 for c in name) or name in names
                    or PurePosixPath(name).is_absolute()
                    or any(part in {'', '.', '..'} for part in name.split('/'))):
                raise ValueError('container media path is invalid')
            names.add(name)
            encoded = item.get('base64')
            if (not isinstance(encoded, str)
                    or len(encoded) > ((limits.max_artifact_bytes + 2) // 3) * 4):
                raise ValueError('container media exceeds file limit')
            data = base64.b64decode(encoded, validate=True)
            total_bytes += len(data)
            sha = hashlib.sha256(data).hexdigest()
            if len(data) > limits.max_artifact_bytes or total_bytes > limits.max_total_evidence_bytes:
                raise ValueError('container media exceeds configured size limits')
            if sha != item.get('sha256'):
                raise ValueError('container media digest mismatch')
            decoded.append((name, data, sha))
        if not decoded:
            continue
        base = f'artifacts/container-media/{source.sha256}'
        rows = []
        for name, data, sha in decoded:
            suffix = PurePosixPath(name).suffix.lower()
            types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                     '.webp': 'image/webp', '.webm': 'video/webm', '.zip': 'application/zip',
                     '.json': 'application/json'}
            ref = f'{base}/{sha}{suffix if suffix in types else ".bin"}'
            target = _projection_path(state_dir, ref)
            if target.exists():
                if target.read_bytes() != data:
                    raise ValueError('existing container media projection differs')
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(dir=target.parent)
                try:
                    with os.fdopen(fd, 'wb') as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                        os.fchmod(handle.fileno(), 0o444)
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            rows.append({'path': name, **build_sidecar_ref_descriptor(
                kind='container_media_projection', ref=ref, sha256=sha,
                byte_count=len(data), content_type=types.get(suffix, 'application/octet-stream'),
                encoding='binary', schema_version='container-media-file.v1', created_by='run-manager')})
        index_path = f'{base}/index.json'
        _projection_path(state_dir, index_path)
        indices.append(write_sidecar_json(state_dir, index_path, {
            'schema_version': 'container-media-index.v1', 'source_evidence_ref': evidence_ref,
            'target_commit': receipt['target_commit'], 'image': image, 'files': rows,
        }, kind='container_media_index', schema_version='container-media-index.v1',
            created_by='run-manager'))
    return indices


def _projection_path(state_dir: Path, ref: str) -> Path:
    path = Path(state_dir).resolve()
    for part in PurePosixPath(ref).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('container media projection cannot traverse symlinks')
    return path
