"""Immutable versioned artifacts produced by WebChat runs."""

from __future__ import annotations

import difflib
import hashlib
import json
import mimetypes
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Mapping

from zf.core.security.redaction import redact_text
from zf.web.agent_runtime_db import connect, dumps, loads, transaction, utc_now


DEFAULT_MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
ALLOWED_UPLOAD_MEDIA_PREFIXES = ("text/", "image/")
ALLOWED_UPLOAD_MEDIA_TYPES = {
    "application/json",
    "application/pdf",
    "application/zip",
    "application/octet-stream",
}


class AgentArtifactConflict(RuntimeError):
    pass


class AgentArtifactAccessDenied(PermissionError):
    pass


class AgentArtifactNotFound(KeyError):
    pass


def validate_adoption_target(target: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the small, versioned target contract before changing visibility."""

    if not isinstance(target, Mapping):
        raise ValueError("artifact adoption target must be an object")
    normalized = dict(target)
    if str(normalized.get("kind") or "") != "task_input":
        raise ValueError("unsupported artifact adoption target kind")
    if not str(normalized.get("task_id") or "").strip():
        raise ValueError("artifact adoption target task_id is required")
    authority_revision = normalized.get(
        "contract_authority_revision", normalized.get("revision"),
    )
    if not str(authority_revision or "").strip():
        raise ValueError("artifact adoption target authority revision is required")
    return normalized


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{uuid.uuid5(uuid.NAMESPACE_URL, value).hex}"


def _safe_logical_name(value: str) -> str:
    text = value.strip().replace("\\", "/")
    if not text or text.startswith("/") or ".." in PurePath(text).parts:
        raise ValueError("artifact logical_name must be a safe relative name")
    return text[:240]


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


class AgentArtifactStore:
    def __init__(self, state_dir: Path, *, project_root: Path | None = None) -> None:
        self.state_dir = Path(state_dir)
        self.project_root = Path(project_root).resolve() if project_root is not None else None
        self.root = self.state_dir / "artifacts" / "webchat"
        with connect(self.state_dir):
            pass

    @property
    def max_bytes(self) -> int:
        return max(1, int(os.environ.get("ZF_WEBCHAT_ARTIFACT_MAX_BYTES", DEFAULT_MAX_ARTIFACT_BYTES)))

    def capture_bytes(
        self,
        *,
        project_id: str,
        logical_name: str,
        data: bytes,
        media_type: str = "",
        producer: dict[str, Any] | None = None,
        lineage: dict[str, Any] | None = None,
        expires_at: str = "",
    ) -> tuple[dict[str, Any], bool]:
        logical_name = _safe_logical_name(logical_name)
        if len(data) > self.max_bytes:
            raise ValueError(f"artifact exceeds {self.max_bytes} byte limit")
        media_type = media_type.strip().lower()
        guessed_media_type = mimetypes.guess_type(logical_name)[0] or ""
        if not media_type or media_type == "application/octet-stream":
            media_type = guessed_media_type or "application/octet-stream"
        self._validate_media_type(media_type)
        self._validate_media_content(media_type, data, logical_name=logical_name)
        digest = _sha256(data)
        artifact_id = _stable_id("artifact", f"{project_id}:{logical_name}")
        version_id = _stable_id("artifact-version", f"{artifact_id}:{digest}")
        storage_ref = f"artifacts/webchat/{artifact_id}/{version_id}/body"
        path = self.state_dir / storage_ref
        now = utc_now()
        with transaction(self.state_dir) as conn:
            existing = conn.execute(
                "SELECT * FROM webchat_artifact_version WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            if existing is not None:
                return self._public(existing, logical_name=logical_name), False
            artifact = conn.execute(
                "SELECT * FROM webchat_artifact WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if artifact is None:
                conn.execute(
                    """
                    INSERT INTO webchat_artifact(
                      artifact_id, project_id, logical_name, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (artifact_id, project_id, logical_name, now, now),
                )
            _atomic_write_bytes(path, data)
            conn.execute(
                """
                INSERT INTO webchat_artifact_version(
                  version_id, artifact_id, digest, media_type, size_bytes,
                  storage_ref, visibility, producer_json, lineage_json,
                  expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'private', ?, ?, ?, ?, ?)
                """,
                (
                    version_id, artifact_id, digest, media_type, len(data),
                    storage_ref, dumps(producer or {}), dumps(lineage or {}),
                    expires_at, now, now,
                ),
            )
            conn.execute(
                "UPDATE webchat_artifact SET updated_at = ? WHERE artifact_id = ?",
                (now, artifact_id),
            )
            row = conn.execute(
                "SELECT * FROM webchat_artifact_version WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            assert row is not None
            return self._public(row, logical_name=logical_name), True

    def capture_path(
        self,
        path: Path,
        *,
        project_id: str,
        logical_name: str = "",
        media_type: str = "",
        producer: dict[str, Any] | None = None,
        lineage: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if self.project_root is None:
            raise ValueError("project_root is required for path capture")
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.project_root) or resolved.is_relative_to(self.state_dir.resolve()):
            raise ValueError("artifact path must stay inside project root and outside runtime state")
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        if resolved.stat().st_size > self.max_bytes:
            raise ValueError(f"artifact exceeds {self.max_bytes} byte limit")
        return self.capture_bytes(
            project_id=project_id,
            logical_name=logical_name or str(resolved.relative_to(self.project_root)),
            data=resolved.read_bytes(),
            media_type=media_type,
            producer=producer,
            lineage=lineage,
        )

    def get(self, version_id: str, *, project_id: str = "") -> dict[str, Any]:
        with connect(self.state_dir) as conn:
            row = conn.execute(
                """
                SELECT v.*, a.logical_name, a.project_id
                FROM webchat_artifact_version v
                JOIN webchat_artifact a ON a.artifact_id = v.artifact_id
                WHERE v.version_id = ?
                """,
                (version_id,),
            ).fetchone()
        if row is None:
            raise AgentArtifactNotFound(version_id)
        item = self._public(row)
        if project_id and str(item["project_id"]) != project_id:
            raise AgentArtifactNotFound(version_id)
        return item

    def list_versions(
        self,
        *,
        project_id: str,
        conversation_id: str = "",
        turn_id: str = "",
        task_id: str = "",
        principal: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with connect(self.state_dir) as conn:
            rows = conn.execute(
                """
                SELECT v.*, a.logical_name, a.project_id
                FROM webchat_artifact_version v
                JOIN webchat_artifact a ON a.artifact_id = v.artifact_id
                WHERE a.project_id = ? ORDER BY v.created_at DESC LIMIT ?
                """,
                (project_id, max(1, min(int(limit), 500))),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = self._public(row)
            lineage = item["lineage"]
            if conversation_id and str(lineage.get("conversation_id") or "") != conversation_id:
                continue
            if turn_id and str(lineage.get("turn_id") or "") != turn_id:
                continue
            if task_id and str(lineage.get("task_id") or "") != task_id:
                continue
            if not self.access_allowed(item, principal=principal):
                continue
            out.append(item)
        return out

    def read(
        self,
        version_id: str,
        *,
        principal: str = "",
        offset: int = 0,
        limit: int | None = None,
        redact: bool = False,
        project_id: str = "",
    ) -> tuple[dict[str, Any], bytes]:
        item = self.get(version_id, project_id=project_id)
        if not self.access_allowed(item, principal=principal, capability="read"):
            raise AgentArtifactAccessDenied(version_id)
        path = self._storage_path(str(item["storage_ref"]))
        data = path.read_bytes()
        if _sha256(data) != str(item["digest"]):
            raise AgentArtifactConflict("artifact bytes digest mismatch")
        start = max(0, int(offset))
        end = len(data) if limit is None else start + max(0, int(limit))
        chunk = data[start:end]
        if redact and str(item["media_type"]).startswith("text/"):
            chunk = redact_text(chunk.decode("utf-8", errors="replace")).encode("utf-8")
        return item, chunk

    def publish(
        self,
        version_id: str,
        *,
        expected_digest: str,
        principal: str,
        project_id: str = "",
    ) -> dict[str, Any]:
        item = self.get(version_id, project_id=project_id)
        if str(item["digest"]) != expected_digest:
            raise AgentArtifactConflict("artifact digest changed")
        if not self.access_allowed(item, principal=principal, capability="publish"):
            raise AgentArtifactAccessDenied(version_id)
        if item["visibility"] in {"published", "adopted"}:
            return item
        with transaction(self.state_dir) as conn:
            conn.execute(
                """
                UPDATE webchat_artifact_version
                SET visibility = 'published', expires_at = '', updated_at = ?
                WHERE version_id = ? AND digest = ? AND visibility = 'private'
                """,
                (utc_now(), version_id, expected_digest),
            )
        return self.get(version_id, project_id=project_id)

    def mark_adopted(
        self,
        version_id: str,
        *,
        expected_digest: str,
        target: dict[str, Any],
        principal: str,
        project_id: str = "",
    ) -> dict[str, Any]:
        target = validate_adoption_target(target)
        item = self.get(version_id, project_id=project_id)
        if str(item["digest"]) != expected_digest:
            raise AgentArtifactConflict("artifact digest changed")
        if not self.access_allowed(item, principal=principal, capability="adopt"):
            raise AgentArtifactAccessDenied(version_id)
        if item["visibility"] not in {"published", "adopted"}:
            raise AgentArtifactConflict("artifact must be published before adoption")
        if item["visibility"] == "adopted":
            if item.get("adopted_target") != target:
                raise AgentArtifactConflict("artifact version is already adopted to another target")
            return item
        with transaction(self.state_dir) as conn:
            conn.execute(
                """
                UPDATE webchat_artifact_version SET visibility = 'adopted',
                  adopted_target_json = ?, updated_at = ?
                WHERE version_id = ? AND digest = ?
                """,
                (dumps(target), utc_now(), version_id, expected_digest),
            )
        return self.get(version_id, project_id=project_id)

    def grant(self, version_id: str, *, principal: str, capability: str = "read") -> None:
        if capability not in {"read", "publish", "adopt"}:
            raise ValueError("unsupported artifact capability")
        self.get(version_id)
        with transaction(self.state_dir) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO webchat_artifact_grant(
                  version_id, principal, capability, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (version_id, principal, capability, utc_now()),
            )

    def access_allowed(
        self,
        item: dict[str, Any],
        *,
        principal: str,
        capability: str = "read",
    ) -> bool:
        if capability not in {"read", "publish", "adopt"}:
            return False
        if item.get("visibility") in {"published", "adopted"}:
            return True
        producer = item.get("producer") if isinstance(item.get("producer"), dict) else {}
        lineage = item.get("lineage") if isinstance(item.get("lineage"), dict) else {}
        intrinsic = {
            str(producer.get("agent_id") or ""),
            str(producer.get("child_id") or ""),
            str(lineage.get("turn_id") or ""),
            str(lineage.get("parent_turn_id") or ""),
        } - {""}
        if principal and principal in intrinsic:
            return True
        if not principal:
            return False
        granted_capabilities = {
            "read": ("read", "publish", "adopt"),
            "publish": ("publish", "adopt"),
            "adopt": ("adopt",),
        }[capability]
        placeholders = ", ".join("?" for _ in granted_capabilities)
        with connect(self.state_dir) as conn:
            row = conn.execute(
                f"""
                SELECT 1 FROM webchat_artifact_grant
                WHERE version_id = ? AND principal = ? AND capability IN ({placeholders})
                """,
                (str(item["version_id"]), principal, *granted_capabilities),
            ).fetchone()
        return row is not None

    def compare(
        self,
        left_version_id: str,
        right_version_id: str,
        *,
        principal: str,
        project_id: str = "",
    ) -> str:
        left, left_data = self.read(
            left_version_id, principal=principal, project_id=project_id,
        )
        right, right_data = self.read(
            right_version_id, principal=principal, project_id=project_id,
        )
        if not str(left["media_type"]).startswith("text/") or not str(right["media_type"]).startswith("text/"):
            raise ValueError("compare supports text artifacts only")
        return "".join(difflib.unified_diff(
            left_data.decode("utf-8", errors="replace").splitlines(keepends=True),
            right_data.decode("utf-8", errors="replace").splitlines(keepends=True),
            fromfile=f"{left['logical_name']}@{left_version_id}",
            tofile=f"{right['logical_name']}@{right_version_id}",
        ))

    def expire_private(self, *, now: str | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        expired: list[str] = []
        with transaction(self.state_dir) as conn:
            rows = conn.execute(
                """
                SELECT version_id, storage_ref FROM webchat_artifact_version
                WHERE visibility = 'private' AND expires_at != '' AND expires_at <= ?
                """,
                (now,),
            ).fetchall()
            for row in rows:
                self._storage_path(str(row["storage_ref"])).unlink(missing_ok=True)
                conn.execute(
                    "UPDATE webchat_artifact_version SET visibility = 'expired', updated_at = ? WHERE version_id = ?",
                    (now, str(row["version_id"])),
                )
                expired.append(str(row["version_id"]))
        return expired

    def _public(self, row: Any, *, logical_name: str = "") -> dict[str, Any]:
        item = dict(row)
        if logical_name:
            item.setdefault("logical_name", logical_name)
        item["producer"] = loads(item.pop("producer_json", "{}"), {})
        item["lineage"] = loads(item.pop("lineage_json", "{}"), {})
        item["adopted_target"] = loads(item.pop("adopted_target_json", "{}"), {})
        item["storage_uri"] = f"artifact://{item['version_id']}"
        return item

    def _storage_path(self, storage_ref: str) -> Path:
        path = (self.state_dir / storage_ref).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise AgentArtifactNotFound(storage_ref)
        return path

    @staticmethod
    def _validate_media_type(media_type: str) -> None:
        if media_type in ALLOWED_UPLOAD_MEDIA_TYPES:
            return
        if any(media_type.startswith(prefix) for prefix in ALLOWED_UPLOAD_MEDIA_PREFIXES):
            return
        raise ValueError(f"unsupported artifact media type {media_type!r}")

    @staticmethod
    def _validate_media_content(media_type: str, data: bytes, *, logical_name: str) -> None:
        guessed = mimetypes.guess_type(logical_name)[0]
        strict_guesses = {
            "application/json", "application/pdf", "application/zip",
            "image/gif", "image/jpeg", "image/png", "image/svg+xml", "image/webp",
        }
        if (
            guessed in strict_guesses
            and media_type != "application/octet-stream"
            and guessed != media_type
        ):
            raise ValueError(
                f"artifact media type {media_type!r} conflicts with {logical_name!r}"
            )
        if media_type == "application/octet-stream":
            return
        if media_type.startswith("text/"):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("text artifact must be valid UTF-8") from exc
            if "\x00" in text:
                raise ValueError("text artifact cannot contain NUL bytes")
            return
        if media_type == "application/json":
            try:
                json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("JSON artifact content is invalid") from exc
            return
        signatures = {
            "application/pdf": data.startswith(b"%PDF-"),
            "application/zip": data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")),
            "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": data.startswith(b"\xff\xd8\xff"),
            "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP",
        }
        if media_type == "image/svg+xml":
            try:
                prefix = data.decode("utf-8").lstrip()[:256].lower()
            except UnicodeDecodeError as exc:
                raise ValueError("SVG artifact must be valid UTF-8") from exc
            if "<svg" not in prefix:
                raise ValueError("SVG artifact content signature is invalid")
            return
        valid = signatures.get(media_type)
        if valid is None:
            raise ValueError(f"artifact content validation is unavailable for {media_type!r}")
        if not valid:
            raise ValueError(f"artifact content does not match declared media type {media_type!r}")


__all__ = [
    "AgentArtifactAccessDenied",
    "AgentArtifactConflict",
    "AgentArtifactNotFound",
    "AgentArtifactStore",
]
