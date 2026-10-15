"""HTTP boundary for bounded Channel document reads."""
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from zf.runtime.channel_document_source import ChannelDocumentError, read_channel_document
from zf.runtime.sidecar_refs import SidecarRefError


def channel_file_preview(*, project_root: Path, state_dir: Path,
                         channel: dict[str, Any], thread_id: str, ref: str) -> dict[str, Any]:
    try:
        return read_channel_document(project_root=project_root, state_dir=state_dir,
                                     channel=channel, thread_id=thread_id, ref=ref)
    except ChannelDocumentError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except SidecarRefError as exc:
        raise HTTPException(409, "Channel message cannot be verified") from exc
