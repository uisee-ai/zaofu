"""Agent-session raw output artifacts.

Large provider/tool outputs should not bloat events.jsonl. The event keeps a
bounded preview plus a ref; the raw artifact remains under project.state_dir.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.sidecar_refs import (
    sidecar_path,
    verify_sidecar_ref,
    write_sidecar_text,
)


RAW_OUTPUT_SCHEMA_VERSION = "agent-output-raw.v1"
DEFAULT_RAW_OUTPUT_THRESHOLD_BYTES = 64 * 1024
RAW_OUTPUT_PREVIEW_CHARS = 12_000
RAW_OUTPUT_HEAD_CHARS = 8_000
RAW_OUTPUT_TAIL_CHARS = 4_000
RAW_OUTPUT_READ_LIMIT_BYTES = 512 * 1024


def raw_output_threshold_bytes() -> int:
    raw = os.environ.get("ZF_AGENT_SESSION_RAW_OUTPUT_THRESHOLD_BYTES", "").strip()
    if not raw:
        return DEFAULT_RAW_OUTPUT_THRESHOLD_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RAW_OUTPUT_THRESHOLD_BYTES
    return max(1, value)


def apply_agent_output_contract(
    state_dir: Path,
    payload: dict[str, Any],
    *,
    text_keys: tuple[str, ...] = ("content", "delta", "output", "answer"),
    metadata: dict[str, Any] | None = None,
    threshold_bytes: int | None = None,
) -> dict[str, Any]:
    """Replace oversized text fields with a preview and attach raw_output ref.

    The function is conservative: it spills at most one largest text value per
    event and only rewrites fields that carried that exact raw text. Small
    payloads are returned unchanged.
    """

    threshold = threshold_bytes if threshold_bytes is not None else raw_output_threshold_bytes()
    candidates: list[tuple[str, str]] = []
    for key in text_keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            candidates.append((key, value))
    refs = payload.get("refs")
    refs_dict = dict(refs) if isinstance(refs, dict) else {}
    refs_output = refs_dict.get("output")
    if isinstance(refs_output, str) and refs_output:
        candidates.append(("refs.output", refs_output))
    oversized = [
        (key, text, len(text.encode("utf-8", errors="replace")))
        for key, text in candidates
        if len(text.encode("utf-8", errors="replace")) > threshold
    ]
    if not oversized:
        return payload

    _key, raw_text, _size = max(oversized, key=lambda item: item[2])
    artifact = write_agent_output_artifact(
        state_dir,
        raw_text,
        metadata=metadata or {},
    )
    preview = str(artifact["preview"])
    next_payload = dict(payload)
    for key in text_keys:
        if next_payload.get(key) == raw_text:
            next_payload[key] = preview
    if refs_dict.get("output") == raw_text:
        refs_dict["output"] = preview
    refs_dict["raw_output"] = _public_artifact_ref(artifact)
    next_payload["refs"] = refs_dict
    return next_payload


def write_agent_output_artifact(
    state_dir: Path,
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    encoded = text.encode("utf-8", errors="replace")
    digest = hashlib.sha256(encoded).hexdigest()
    meta = dict(metadata or {})
    run_id = _safe_slug(str(meta.get("run_id") or meta.get("turn_id") or meta.get("request_id") or "run"))
    part_id = _safe_slug(str(meta.get("part_id") or meta.get("message_type") or meta.get("kind") or "output"))
    rel_path = PurePosixPath("artifacts") / "agent-session-output" / run_id / f"{part_id}-{digest[:16]}.txt"
    line_count = 0 if text == "" else text.count("\n") + 1
    preview = _preview_text(text)
    descriptor = write_sidecar_text(
        state_dir,
        rel_path,
        text,
        kind="agent_session_output",
        schema_version=RAW_OUTPUT_SCHEMA_VERSION,
        created_by="agent_session_output",
        retention={"class": "audit_required", "redaction_profile": "default"},
        content_type="text/plain",
        preview=preview,
    )
    artifact = {
        **descriptor,
        "schema_version": RAW_OUTPUT_SCHEMA_VERSION,
        "artifact_type": RAW_OUTPUT_SCHEMA_VERSION,
        "raw_ref": descriptor["ref"],
        "line_count": line_count,
        "mime": "text/plain",
        "encoding": "utf-8",
        "preview": preview,
        "head": text[:RAW_OUTPUT_HEAD_CHARS],
        "tail": text[-RAW_OUTPUT_TAIL_CHARS:] if len(text) > RAW_OUTPUT_TAIL_CHARS else "",
        "truncated": True,
        "metadata": meta,
    }
    meta_ref = rel_path.with_suffix(".json")
    artifact["meta_ref"] = meta_ref.as_posix()
    atomic_write_text(
        Path(state_dir) / Path(meta_ref.as_posix()),
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
    )
    return artifact


def read_agent_output_artifact(
    state_dir: Path,
    raw_ref: str,
    *,
    offset: int = 0,
    limit: int = RAW_OUTPUT_READ_LIMIT_BYTES,
) -> dict[str, Any]:
    rel_path = _safe_raw_ref(raw_ref)
    path = sidecar_path(state_dir, rel_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(raw_ref)
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or RAW_OUTPUT_READ_LIMIT_BYTES), 4 * 1024 * 1024))
    size = path.stat().st_size
    meta = _read_artifact_meta(path.with_suffix(".json"))
    descriptor = {
        "kind": "agent_session_output",
        "ref": rel_path.as_posix(),
        "sha256": meta.get("sha256") or "",
        "byte_count": int(meta.get("byte_count") or size),
        "content_type": meta.get("content_type") or meta.get("mime") or "text/plain",
        "schema_version": meta.get("schema_version") or RAW_OUTPUT_SCHEMA_VERSION,
        "encoding": meta.get("encoding") or "utf-8",
    }
    verified = verify_sidecar_ref(state_dir, descriptor)
    with path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read(limit)
    text = chunk.decode("utf-8", errors="replace")
    return {
        "schema_version": RAW_OUTPUT_SCHEMA_VERSION,
        "raw_ref": rel_path.as_posix(),
        "ref": rel_path.as_posix(),
        "kind": "agent_session_output",
        "content": text,
        "offset": offset,
        "limit": limit,
        "byte_count": int(verified["byte_count"]),
        "line_count": int(meta.get("line_count") or (text.count("\n") + 1 if text else 0)),
        "sha256": str(verified["sha256"]),
        "mime": str(meta.get("mime") or "text/plain"),
        "encoding": str(meta.get("encoding") or "utf-8"),
        "truncated": offset + len(chunk) < size,
        "next_offset": offset + len(chunk) if offset + len(chunk) < size else None,
        "metadata": meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {},
    }


def _public_artifact_ref(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref_schema_version": artifact.get("ref_schema_version"),
        "kind": artifact.get("kind") or "agent_session_output",
        "ref": artifact["raw_ref"],
        "schema_version": RAW_OUTPUT_SCHEMA_VERSION,
        "artifact_type": RAW_OUTPUT_SCHEMA_VERSION,
        "raw_ref": artifact["raw_ref"],
        "meta_ref": artifact["meta_ref"],
        "sha256": artifact["sha256"],
        "byte_count": artifact["byte_count"],
        "content_type": artifact.get("content_type") or artifact.get("mime") or "text/plain",
        "created_by": artifact.get("created_by") or "agent_session_output",
        "source_event_id": artifact.get("source_event_id") or "",
        "access_scope": artifact.get("access_scope") if isinstance(artifact.get("access_scope"), dict) else {},
        "retention": artifact.get("retention") if isinstance(artifact.get("retention"), dict) else {"class": "audit_required"},
        "required": bool(artifact.get("required", False)),
        "line_count": artifact["line_count"],
        "mime": artifact["mime"],
        "encoding": artifact["encoding"],
        "preview": artifact["preview"],
        "head": artifact["head"],
        "tail": artifact["tail"],
        "truncated": True,
    }


def _preview_text(text: str) -> str:
    if len(text) <= RAW_OUTPUT_PREVIEW_CHARS:
        return text
    head = text[:RAW_OUTPUT_HEAD_CHARS].rstrip()
    tail = text[-RAW_OUTPUT_TAIL_CHARS:].lstrip()
    return f"{head}\n\n[... output truncated; open raw artifact for full content ...]\n\n{tail}"


def _safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())[:96].strip(".-")
    return cleaned or "output"


def _safe_raw_ref(raw_ref: str) -> PurePosixPath:
    text = str(raw_ref or "").strip()
    if not text:
        raise ValueError("raw_ref is required")
    rel = PurePosixPath(text)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise ValueError("raw_ref must be a clean relative path")
    if not rel.parts or rel.parts[0] != "artifacts":
        raise ValueError("raw_ref must point under artifacts/")
    return rel


def _read_artifact_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


__all__ = [
    "RAW_OUTPUT_SCHEMA_VERSION",
    "apply_agent_output_contract",
    "read_agent_output_artifact",
    "write_agent_output_artifact",
]
