"""Digest-bound selected text blocks, published inside an attempt manifest.

The manifest owns the expected digests. Blocks are reconstructible from pinned
full sources; neither a cache path nor a cursor grants read authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, sidecar_path

BLOCK_CHARS = 32768
SNAPSHOT_SCHEMA = "artifact-selected-text.v1"


def selected_text(raw: bytes, json_path: str) -> str:
    from zf.runtime.artifact_read_ledger import _encode_selected, _select_json_path

    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        if json_path != "$":
            raise ValueError("json_path requires a JSON artifact")
        body = raw.decode("utf-8", errors="replace")
    return _encode_selected(_select_json_path(body, json_path))


def pin_read_selections(
    state_dir: Path, manifest: dict[str, Any], required: list[dict[str, Any]],
) -> None:
    """Write blocks before the source manifest is published or capability-bound."""
    from zf.runtime.artifact_source_versions import read_source_version
    from zf.runtime.artifact_navigation import build_navigation

    for source in manifest.get("sources", []):
        allowed = source.get("allowed_paths") or ["$"]
        paths = list(dict.fromkeys([*allowed, *[
            row.get("json_path", "$") for row in required
            if row.get("source_id") == source["source_id"]
            and row.get("artifact_id") == source["artifact_id"]
            and (not row.get("artifact_sha256") or row["artifact_sha256"] == source["sha256"])
        ]]))
        raw, _ = read_source_version(state_dir, source, path=sidecar_path(state_dir, source["ref"]))
        selections = []
        for path in paths:
            if "$" not in allowed and path not in allowed:
                raise ValueError("required selection is outside the published source scope")
            try:
                content = selected_text(raw, path)
            except ValueError:
                # The optional cache must not move the existing missing-selector
                # failure from controlled read/admission into dispatch. The
                # required policy stays intact; the legacy read will fail closed.
                continue
            chunks = []
            for start in range(0, len(content), BLOCK_CHARS):
                fragment = content[start:start + BLOCK_CHARS]
                descriptor = write_immutable_json_sidecar(
                    state_dir, {"schema_version": SNAPSHOT_SCHEMA, "content": fragment},
                    root="read-selections", kind="artifact_selected_text",
                    schema_version=SNAPSHOT_SCHEMA, created_by="artifact-read",
                )
                chunks.append({"start": start, "end": start + len(fragment),
                               "ref": descriptor["ref"], "sha256": descriptor["sha256"]})
            selections.append({
                "schema_version": SNAPSHOT_SCHEMA, "json_path": path,
                "source_sha256": source["sha256"], "source_bytes": len(raw),
                "selected_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "selected_bytes": len(content.encode()), "selected_chars": len(content),
                "chunks": chunks,
                "navigation": build_navigation(content),
            })
        source["read_selections"] = selections


@dataclass
class SelectedText:
    state_dir: Path
    source: dict[str, Any]
    json_path: str
    chars: int
    byte_count: int
    digest: str
    receipt: dict[str, Any]
    chunks: list[dict[str, Any]] = field(default_factory=list)
    content: str | None = None
    loaded: dict[str, str] = field(default_factory=dict)

    def read(self, start: int = 0, end: int | None = None) -> str:
        end = self.chars if end is None else min(end, self.chars)
        if not 0 <= start <= end:
            raise ValueError("selection range is invalid")
        if self.content is not None:
            return self.content[start:end]
        result = []
        try:
            for chunk in self.chunks:
                if chunk["end"] <= start or chunk["start"] >= end:
                    continue
                key = chunk["sha256"]
                if key not in self.loaded:
                    value = hydrate_sidecar_ref(self.state_dir, {
                        "ref": chunk["ref"], "sha256": key,
                    }).payload
                    if (not isinstance(value, dict) or value.get("schema_version") != SNAPSHOT_SCHEMA
                            or not isinstance(value.get("content"), str)
                            or len(value["content"]) != chunk["end"] - chunk["start"]):
                        raise ValueError("invalid selected-text block")
                    self.loaded[key] = value["content"]
                result.append(self.loaded[key][max(start - chunk["start"], 0):end - chunk["start"]])
            content = "".join(result)
            if len(content) != end - start:
                raise ValueError("selected-text block coverage is incomplete")
            return content
        except (OSError, ValueError, KeyError, TypeError):
            from zf.runtime.artifact_source_versions import read_source_version

            raw, _ = read_source_version(
                self.state_dir, self.source, path=sidecar_path(self.state_dir, self.source["ref"]),
            )
            self.content = selected_text(raw, self.json_path)
            if (hashlib.sha256(self.content.encode()).hexdigest() != self.digest
                    or len(self.content) != self.chars or len(self.content.encode()) != self.byte_count):
                raise ValueError("selected-text recovery differs from the pinned selection")
            self.receipt["recovery_mode"] = "pinned_selection_rebuilt"
            return self.content[start:end]


def open_selection(
    state_dir: Path, *, manifest: Mapping[str, Any], row: Mapping[str, Any], identity: Mapping[str, str],
) -> SelectedText:
    """Authorize every occurrence before using even snapshot metadata."""
    from zf.runtime.artifact_read_ledger import ARTIFACT_READ_SCHEMA, _find_source, read_attempt_artifact

    identity = {**identity, "purpose": str(identity.get("purpose") or manifest.get("read_purpose") or "").strip()}
    path = str(row.get("json_path") or "$")
    expected = str(row.get("artifact_sha256") or row.get("sha256") or "")
    source = _find_source(manifest, source_id=row["source_id"], artifact_id=row["artifact_id"],
                          artifact_sha256=expected)
    allowed = source.get("allowed_paths") or ["$"]
    permitted = artifact_access_allowed(source.get("access_scope"), actor=identity.get("actor", ""),
                                       role=identity.get("role", ""), purpose=identity.get("purpose", ""))
    if "$" not in allowed and path not in allowed:
        raise ValueError("json_path is not allowed for this source")
    snapshots = [s for s in source.get("read_selections", []) if s.get("json_path") == path]
    if not permitted or not snapshots:
        # Retain the existing denied-read audit and legacy complete-source path.
        value = read_attempt_artifact(
            state_dir, manifest=manifest, source_id=row["source_id"], artifact_id=row["artifact_id"],
            artifact_sha256=expected, json_path=path, record=False,
            actor=identity.get("actor", ""), role=identity.get("role", ""),
            provider=identity.get("provider", ""), purpose=identity.get("purpose", ""),
        )
        content = value["content"]
        return SelectedText(state_dir, source, path, len(content), len(content.encode()),
                            hashlib.sha256(content.encode()).hexdigest(), value["read"], content=content)
    if len(snapshots) != 1:
        raise ValueError("ambiguous selected-text snapshot")
    snapshot = snapshots[0]
    if (snapshot.get("schema_version") != SNAPSHOT_SCHEMA or snapshot.get("source_sha256") != source["sha256"]
            or type(snapshot.get("selected_chars")) is not int or snapshot["selected_chars"] < 0
            or type(snapshot.get("selected_bytes")) is not int or snapshot["selected_bytes"] < snapshot["selected_chars"]
            or len(str(snapshot.get("selected_sha256", ""))) != 64):
        raise ValueError("invalid selected-text snapshot binding")
    position = 0
    for chunk in snapshot.get("chunks", []):
        if (chunk.get("start") != position or type(chunk.get("end")) is not int
                or not position < chunk["end"] <= snapshot["selected_chars"]):
            raise ValueError("invalid selected-text snapshot range")
        position = chunk["end"]
    if position != snapshot["selected_chars"]:
        raise ValueError("incomplete selected-text snapshot")
    receipt = {
        "schema_version": ARTIFACT_READ_SCHEMA, "status": "read", "production_status": "generated",
        "provider_delivery_status": "unknown", "json_path": path,
        **{key: str(manifest.get(key) or "") for key in ("workflow_run_id", "task_id", "attempt_id", "dispatch_id")},
        "source_id": source["source_id"], "artifact_id": source["artifact_id"],
        "artifact_ref": source["ref"], "artifact_sha256": source["sha256"],
        "occurrence_id": source.get("occurrence_id", ""), "bytes": snapshot["source_bytes"],
        "returned_bytes": snapshot["selected_bytes"], "truncated": False, "max_items": 0, "max_chars": 0,
        "consumer_actor": identity.get("actor", ""), "consumer_role": identity.get("role", ""),
        "consumer_provider": identity.get("provider", ""), "consumer_purpose": identity.get("purpose", ""),
        "read_at": datetime.now(timezone.utc).isoformat(), "recovery_mode": "pinned_selection_snapshot",
    }
    return SelectedText(state_dir, source, path, snapshot["selected_chars"], snapshot["selected_bytes"],
                        snapshot["selected_sha256"], receipt, chunks=snapshot["chunks"])
