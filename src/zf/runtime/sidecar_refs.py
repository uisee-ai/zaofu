"""Sidecar ref descriptors and hydration helpers.

Sidecars are payload files under ``project.state_dir`` referenced by
``events.jsonl`` envelopes. They are evidence/content stores, not workflow
truth. This module keeps the path, hash, and hydration rules shared by Web,
Run Manager, read_model, and doctor checks.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from zf.core.state.atomic_io import atomic_write_text


SIDECAR_REF_SCHEMA_VERSION = "sidecar-ref.v1"
DEFAULT_HYDRATE_LIMIT_BYTES = 4 * 1024 * 1024
KNOWN_SIDECAR_ROOTS = (
    "artifacts",
    "channels",
    "diagnostics",
    "operator",
    "snapshots",
    "transcripts",
)


class SidecarRefError(ValueError):
    """Machine-readable sidecar failure."""

    def __init__(self, code: str, message: str, *, ref: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.ref = ref


@dataclass(frozen=True)
class HydratedSidecar:
    ok: bool
    kind: str
    ref: str
    sha256: str
    byte_count: int
    content_type: str
    encoding: str
    payload: Any
    redacted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "kind": self.kind,
            "ref": self.ref,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "content_type": self.content_type,
            "encoding": self.encoding,
            "payload": self.payload,
            "redacted": self.redacted,
        }


def write_sidecar_text(
    state_dir: Path,
    relative_ref: str | PurePosixPath,
    text: str,
    *,
    kind: str,
    schema_version: str,
    created_by: str,
    source_event_id: str = "",
    access_scope: dict[str, Any] | None = None,
    retention: dict[str, Any] | None = None,
    required: bool = False,
    content_type: str = "text/plain",
    encoding: str = "utf-8",
    preview: str = "",
) -> dict[str, Any]:
    """Write a UTF-8 sidecar payload and return a normalized descriptor."""

    rel = safe_sidecar_ref(relative_ref)
    encoded = text.encode(encoding, errors="replace")
    digest = hashlib.sha256(encoded).hexdigest()
    target = sidecar_path(state_dir, rel)
    atomic_write_text(target, text, encoding=encoding)
    _fsync_parent(target)
    return build_sidecar_ref_descriptor(
        kind=kind,
        ref=rel.as_posix(),
        sha256=digest,
        byte_count=len(encoded),
        content_type=content_type,
        schema_version=schema_version,
        encoding=encoding,
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope=access_scope,
        retention=retention,
        required=required,
        preview=preview,
    )


def write_sidecar_json(
    state_dir: Path,
    relative_ref: str | PurePosixPath,
    payload: Any,
    *,
    kind: str,
    schema_version: str,
    created_by: str,
    source_event_id: str = "",
    access_scope: dict[str, Any] | None = None,
    retention: dict[str, Any] | None = None,
    required: bool = False,
    preview: str = "",
) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    return write_sidecar_text(
        state_dir,
        relative_ref,
        text,
        kind=kind,
        schema_version=schema_version,
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope=access_scope,
        retention=retention,
        required=required,
        content_type="application/json",
        preview=preview,
    )


def build_sidecar_ref_descriptor(
    *,
    kind: str,
    ref: str,
    sha256: str,
    byte_count: int,
    content_type: str,
    schema_version: str,
    encoding: str = "utf-8",
    created_by: str = "",
    source_event_id: str = "",
    access_scope: dict[str, Any] | None = None,
    retention: dict[str, Any] | None = None,
    required: bool = False,
    preview: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    descriptor: dict[str, Any] = {
        "ref_schema_version": SIDECAR_REF_SCHEMA_VERSION,
        "kind": str(kind or "sidecar"),
        "ref": safe_sidecar_ref(ref).as_posix(),
        "sha256": str(sha256 or ""),
        "byte_count": int(byte_count or 0),
        "content_type": str(content_type or "application/octet-stream"),
        "schema_version": str(schema_version or ""),
        "encoding": str(encoding or "utf-8"),
        "created_by": str(created_by or ""),
        "source_event_id": str(source_event_id or ""),
        "access_scope": dict(access_scope or {}),
        "retention": dict(retention or {"class": "audit_required"}),
        "required": bool(required),
        "preview": str(preview or ""),
    }
    if extra:
        descriptor.update(extra)
    return descriptor


def hydrate_sidecar_ref(
    state_dir: Path,
    descriptor: dict[str, Any],
    *,
    purpose: str = "",
    actor: str = "",
    max_bytes: int = DEFAULT_HYDRATE_LIMIT_BYTES,
) -> HydratedSidecar:
    """Read and validate a sidecar descriptor.

    Raises ``SidecarRefError`` with stable ``code`` on failure.
    """

    verified = verify_sidecar_ref(state_dir, descriptor)
    normalized = verified["descriptor"]
    ref = str(verified["ref"] or "")
    rel = safe_sidecar_ref(ref)
    path = sidecar_path(state_dir, rel)
    size = int(verified["byte_count"])
    if size > max(1, int(max_bytes or DEFAULT_HYDRATE_LIMIT_BYTES)):
        raise SidecarRefError("payload_too_large", f"sidecar payload too large: {ref}", ref=ref)
    raw = path.read_bytes()
    digest = str(verified["sha256"])
    encoding = str(normalized.get("encoding") or "utf-8")
    content_type = str(normalized.get("content_type") or normalized.get("mime") or "text/plain")
    try:
        text = raw.decode(encoding, errors="replace")
    except LookupError as exc:
        raise SidecarRefError("encoding_unsupported", str(exc), ref=ref) from exc
    if content_type == "application/json" or ref.endswith(".json"):
        try:
            payload: Any = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SidecarRefError("schema_unsupported", str(exc), ref=ref) from exc
    else:
        payload = text
    return HydratedSidecar(
        ok=True,
        kind=str(normalized.get("kind") or "sidecar"),
        ref=ref,
        sha256=digest,
        byte_count=size,
        content_type=content_type,
        encoding=encoding,
        payload=payload,
    )


def verify_sidecar_ref(state_dir: Path, descriptor: dict[str, Any]) -> dict[str, Any]:
    """Verify existence, byte_count, and sha256 without loading payload text."""

    normalized = normalize_sidecar_ref_descriptor(descriptor)
    ref = str(normalized.get("ref") or "")
    rel = safe_sidecar_ref(ref)
    path = sidecar_path(state_dir, rel)
    if not path.exists() or not path.is_file():
        raise SidecarRefError("ref_missing", f"sidecar ref missing: {ref}", ref=ref)
    size = path.stat().st_size
    expected_bytes = int(normalized.get("byte_count") or 0)
    if expected_bytes and expected_bytes != size:
        raise SidecarRefError(
            "byte_count_mismatch",
            f"sidecar byte_count mismatch for {ref}: expected {expected_bytes}, got {size}",
            ref=ref,
        )
    digest = _sha256_file(path)
    expected_hash = str(normalized.get("sha256") or "")
    if expected_hash and expected_hash != digest:
        raise SidecarRefError(
            "hash_mismatch",
            f"sidecar hash mismatch for {ref}: expected {expected_hash}, got {digest}",
            ref=ref,
        )
    return {
        "ok": True,
        "descriptor": normalized,
        "kind": str(normalized.get("kind") or "sidecar"),
        "ref": ref,
        "sha256": digest,
        "byte_count": size,
        "content_type": str(normalized.get("content_type") or "text/plain"),
        "encoding": str(normalized.get("encoding") or "utf-8"),
    }


def normalize_sidecar_ref_descriptor(value: dict[str, Any]) -> dict[str, Any]:
    """Return a 114-style descriptor, accepting legacy raw_output refs."""

    if not isinstance(value, dict):
        raise SidecarRefError("schema_unsupported", "sidecar descriptor must be an object")
    ref = str(value.get("ref") or value.get("raw_ref") or value.get("path") or "").strip()
    if not ref:
        raise SidecarRefError("ref_missing", "sidecar descriptor missing ref")
    kind = str(value.get("kind") or value.get("ref_kind") or "").strip()
    if not kind:
        artifact_type = str(value.get("artifact_type") or value.get("schema_version") or "")
        if artifact_type.startswith("agent-output-raw"):
            kind = "agent_session_output"
        else:
            kind = "sidecar"
    content_type = str(value.get("content_type") or value.get("mime") or "").strip()
    if not content_type:
        content_type = "application/json" if ref.endswith(".json") else "text/plain"
    descriptor = dict(value)
    descriptor.setdefault("ref_schema_version", SIDECAR_REF_SCHEMA_VERSION)
    descriptor["kind"] = kind
    descriptor["ref"] = safe_sidecar_ref(ref).as_posix()
    descriptor["content_type"] = content_type
    descriptor.setdefault("schema_version", value.get("artifact_type") or value.get("schema_version") or "")
    descriptor.setdefault("encoding", value.get("encoding") or "utf-8")
    descriptor.setdefault("access_scope", value.get("access_scope") if isinstance(value.get("access_scope"), dict) else {})
    descriptor.setdefault("retention", value.get("retention") if isinstance(value.get("retention"), dict) else {"class": "audit_required"})
    descriptor.setdefault("required", bool(value.get("required", False)))
    descriptor.setdefault("preview", str(value.get("preview") or ""))
    return descriptor


def iter_sidecar_ref_descriptors(payload: Any) -> Iterable[dict[str, Any]]:
    """Yield normalized sidecar descriptors nested inside an event payload."""

    seen: set[tuple[str, str]] = set()

    def walk(value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, dict):
            if _looks_like_sidecar_descriptor(value):
                try:
                    descriptor = normalize_sidecar_ref_descriptor(value)
                except SidecarRefError:
                    descriptor = {}
                if descriptor:
                    key = (str(descriptor.get("kind") or ""), str(descriptor.get("ref") or ""))
                    if key not in seen:
                        seen.add(key)
                        yield descriptor
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    yield from walk(payload)


def sidecar_path(state_dir: Path, relative_ref: str | PurePosixPath) -> Path:
    rel = safe_sidecar_ref(relative_ref)
    root = Path(state_dir).resolve()
    path = (root / Path(rel.as_posix())).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SidecarRefError("state_dir_mismatch", "sidecar ref escapes state_dir", ref=rel.as_posix()) from exc
    return path


def safe_sidecar_ref(relative_ref: str | PurePosixPath) -> PurePosixPath:
    text = str(relative_ref or "").strip()
    if not text:
        raise SidecarRefError("ref_missing", "sidecar ref is required")
    rel = PurePosixPath(text)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise SidecarRefError("state_dir_mismatch", "sidecar ref must be a clean relative path", ref=text)
    if not rel.parts or rel.parts[0] not in KNOWN_SIDECAR_ROOTS:
        raise SidecarRefError(
            "access_denied",
            f"sidecar ref must live under one of {', '.join(KNOWN_SIDECAR_ROOTS)}",
            ref=text,
        )
    return rel


def doctor_sidecar_refs(
    state_dir: Path,
    events: Iterable[Any],
    *,
    include_orphans: bool = True,
) -> dict[str, Any]:
    """Validate sidecar refs discoverable from event payloads."""

    event_rows = list(events)
    descriptors: list[dict[str, Any]] = []
    ref_events: dict[tuple[str, str], list[dict[str, str]]] = {}
    for event in event_rows:
        payload = getattr(event, "payload", {})
        event_id = str(getattr(event, "id", "") or "")
        event_type = str(getattr(event, "type", "") or "")
        for descriptor in iter_sidecar_ref_descriptors(payload):
            key = (str(descriptor.get("kind") or ""), str(descriptor.get("ref") or ""))
            descriptors.append(descriptor)
            ref_events.setdefault(key, []).append({"event_id": event_id, "event_type": event_type})

    checked: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    referenced_paths: set[str] = set()
    for descriptor in descriptors:
        ref = str(descriptor.get("ref") or "")
        referenced_paths.add(ref)
        item = {
            "kind": str(descriptor.get("kind") or ""),
            "ref": ref,
            "required": bool(descriptor.get("required", False)),
            "source_events": ref_events.get((str(descriptor.get("kind") or ""), ref), []),
        }
        try:
            verified = verify_sidecar_ref(state_dir, descriptor)
        except SidecarRefError as exc:
            issue = dict(item)
            issue.update({"code": exc.code, "message": str(exc)})
            issues.append(issue)
            checked.append({**item, "status": "failed", "code": exc.code})
            continue
        checked.append({
            **item,
            "status": "ok",
            "sha256": verified["sha256"],
            "byte_count": verified["byte_count"],
        })

    orphans: list[dict[str, Any]] = []
    if include_orphans:
        from zf.runtime.artifact_read_ledger import (
            active_ledger_attempt_id,
            live_attempt_ids,
        )

        live_attempts = live_attempt_ids(event_rows)
        for rel in _iter_known_sidecar_files(state_dir):
            rel_text = rel.as_posix()
            if rel_text in referenced_paths:
                continue
            # Agent output metadata JSON is paired with a raw text ref and is
            # not a standalone workflow ref in the legacy contract.
            if rel_text.startswith("artifacts/agent-session-output/") and rel_text.endswith(".json"):
                continue
            active_attempt = active_ledger_attempt_id(rel_text)
            if active_attempt and active_attempt in live_attempts:
                continue
            orphans.append({
                "ref": rel_text,
                "code": (
                    "stale_active_read_ledger"
                    if active_attempt
                    else "orphan_sidecar"
                ),
            })

    return {
        "schema_version": "sidecar-doctor.v1",
        "checked_ref_count": len(checked),
        "issue_count": len(issues),
        "orphan_count": len(orphans),
        "ok": not issues,
        "checked_refs": checked,
        "issues": issues,
        "orphans": orphans,
    }


def _looks_like_sidecar_descriptor(value: dict[str, Any]) -> bool:
    if not any(key in value for key in ("ref", "raw_ref", "path")):
        return False
    if "sha256" in value:
        return True
    schema = str(value.get("schema_version") or value.get("artifact_type") or "")
    return schema.startswith(("agent-output-raw", "sidecar-ref", "channel.", "provider.", "plan."))


def _iter_known_sidecar_files(state_dir: Path) -> Iterable[PurePosixPath]:
    root = Path(state_dir)
    for base in KNOWN_SIDECAR_ROOTS:
        directory = root / base
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_file():
                # ``locked_path`` leaves reusable coordination files beside
                # immutable sidecars. They contain no workflow evidence and
                # are neither references nor orphan business artifacts.
                if path.name.endswith(".lock"):
                    continue
                yield PurePosixPath(path.relative_to(root).as_posix())


def _fsync_parent(path: Path) -> None:
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "DEFAULT_HYDRATE_LIMIT_BYTES",
    "KNOWN_SIDECAR_ROOTS",
    "SIDECAR_REF_SCHEMA_VERSION",
    "HydratedSidecar",
    "SidecarRefError",
    "build_sidecar_ref_descriptor",
    "doctor_sidecar_refs",
    "hydrate_sidecar_ref",
    "iter_sidecar_ref_descriptors",
    "normalize_sidecar_ref_descriptor",
    "safe_sidecar_ref",
    "sidecar_path",
    "verify_sidecar_ref",
    "write_sidecar_json",
    "write_sidecar_text",
]
