"""Browser links for verified text outputs in one task's artifact manifest."""

import re
from pathlib import Path

from zf.runtime.channel_document_source import ChannelDocumentError, MAX_PREVIEW_BYTES, document_locations


def verified_task_files(ledger: dict, *, project_root: Path, state_dir: Path) -> dict[str, Path]:
    accepted = {
        (row.get("path"), row.get("sha256"))
        for row in ledger.get("accepted_artifact_refs", [])
    }
    result = {}
    for row in ledger.get("hash_status", []):
        digest = row.get("expected_sha256") or ""
        if (row.get("status") != "ok" or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or (row.get("path"), digest) not in accepted):
            continue
        path = Path(row.get("resolved_path") or "")
        if not path.is_absolute():
            continue
        try:
            document_locations(path, project_root=project_root, state_dir=state_dir)
            if path.resolve(strict=True) == path and path.is_file() and path.stat().st_size <= MAX_PREVIEW_BYTES:
                result[digest] = path
        except (OSError, RuntimeError, ChannelDocumentError):
            continue
    return result
