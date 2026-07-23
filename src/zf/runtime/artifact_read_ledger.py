"""Attempt-local source manifests and controlled artifact-read evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import (
    SidecarRefError,
    build_sidecar_ref_descriptor,
    hydrate_sidecar_ref,
    safe_sidecar_ref,
    sidecar_path,
)


SOURCE_MANIFEST_SCHEMA = "attempt-source-manifest.v1"
ARTIFACT_READ_SCHEMA = "artifact-read.v1"
READ_POLICY_SCHEMA = "input-consumption-policy.v1"
READ_LEDGER_SCHEMA = "artifact-read-ledger.v1"

_ATTEMPT_REF_RE = re.compile(
    r"^artifacts/attempts/(?P<attempt>[^/]+)/read-ledger\.active\.jsonl$"
)


class ArtifactReadError(ValueError):
    """A controlled artifact read or its evidence contract failed."""


def build_attempt_source_manifest(
    *,
    workflow_run_id: str,
    task_id: str,
    attempt_id: str,
    dispatch_id: str,
    sources: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(sources):
        ref = str(raw.get("ref") or raw.get("path") or "").strip()
        digest = str(raw.get("sha256") or "").strip()
        if not ref or not digest:
            continue
        source_id = str(raw.get("source_id") or raw.get("kind") or f"source-{index + 1}").strip()
        artifact_id = str(raw.get("artifact_id") or raw.get("name") or Path(ref).name).strip()
        key = (source_id, artifact_id, digest)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({
            "source_id": source_id,
            "artifact_id": artifact_id,
            "kind": str(raw.get("kind") or "artifact"),
            "ref": ref,
            "sha256": digest,
            "allowed_paths": _strings(raw.get("allowed_paths")) or ["$"],
        })
    return {
        "schema_version": SOURCE_MANIFEST_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "dispatch_id": dispatch_id,
        "sources": normalized,
    }


def write_attempt_source_manifest(
    state_dir: Path,
    manifest: Mapping[str, Any],
    *,
    source_event_id: str = "",
) -> dict[str, Any]:
    attempt_id = str(manifest.get("attempt_id") or "")
    if not attempt_id:
        raise ArtifactReadError("source manifest requires attempt_id")
    return write_immutable_json_sidecar(
        state_dir,
        manifest,
        root=f"attempts/{_safe_component(attempt_id)}/source-manifests",
        kind="attempt_source_manifest",
        schema_version=SOURCE_MANIFEST_SCHEMA,
        created_by="artifact-read-ledger",
        source_event_id=source_event_id,
    )


def source_manifest_from_payload(
    *,
    state_dir: Path,
    project_root: Path,
    payload: Mapping[str, Any],
    workflow_run_id: str,
    task_id: str,
    attempt_id: str,
    dispatch_id: str,
    source_event_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    descriptor_fields = (
        ("contract", "contract_snapshot_ref", "contract_snapshot_digest"),
        ("target", "target_snapshot_ref", "target_snapshot_digest"),
        ("impl-self-check", "impl_self_check_ref", "impl_self_check_digest"),
        ("rework-feedback", "rework_feedback_ref", "rework_feedback_digest"),
        ("parent-call-result", "parent_call_result_ref", "parent_call_result_digest"),
    )
    for source_id, ref_key, digest_key in descriptor_fields:
        ref = str(payload.get(ref_key) or "").strip()
        digest = str(payload.get(digest_key) or "").strip()
        if ref and digest:
            sources.append({
                "source_id": source_id,
                "artifact_id": Path(ref).name,
                "kind": source_id,
                "ref": ref,
                "sha256": digest,
                "allowed_paths": ["$"],
            })
    manifest_ref = str(payload.get("workflow_input_manifest_ref") or "").strip()
    if manifest_ref:
        source = _source_from_ref(
            state_dir=state_dir,
            project_root=project_root,
            ref=manifest_ref,
            source_id="workflow-input",
            kind="workflow_input_manifest",
        )
        if source:
            sources.append(source)
    for field in ("artifact_refs", "input_refs"):
        raw_refs = payload.get(field)
        if not isinstance(raw_refs, list):
            continue
        for index, item in enumerate(raw_refs):
            source = _source_from_item(
                state_dir=state_dir,
                project_root=project_root,
                item=item,
                source_id=f"{field}-{index + 1}",
            )
            if source:
                sources.append(source)
    manifest = build_attempt_source_manifest(
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        dispatch_id=dispatch_id,
        sources=sources,
    )
    descriptor = write_attempt_source_manifest(
        state_dir,
        manifest,
        source_event_id=source_event_id,
    )
    return manifest, descriptor


def canonical_required_reads(
    manifest: Mapping[str, Any],
    *,
    output_profile_id: str,
    explicit: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Declare the canonical artifact slices one stage must actually consume."""

    rows = [dict(item) for item in explicit if isinstance(item, Mapping)]
    profile = str(output_profile_id or "").strip()
    required_paths: dict[str, tuple[str, ...]] = {}
    if profile == "implementation":
        required_paths = {
            "contract": ("$.acceptance_criteria", "$.verification_commands"),
        }
    elif profile in {"task-verify", "candidate-verify"}:
        required_paths = {
            "contract": ("$.acceptance_criteria", "$.verification_commands"),
            "target": ("$",),
            "impl-self-check": ("$",),
        }

    if profile == "plan-synth":
        for source in (
            manifest.get("sources")
            if isinstance(manifest.get("sources"), list)
            else []
        ):
            if not isinstance(source, Mapping):
                continue
            source_id = str(source.get("source_id") or "")
            if source_id == "plan-synth-contract" or source_id.startswith("child-result-"):
                required_paths[source_id] = ("$",)
            elif source_id in {
                "goal-objective",
                "requirement",
                "review-artifact",
                "workflow-input",
                "workflow-prompt",
            }:
                required_paths[source_id] = ("$",)

    sources = manifest.get("sources")
    source_rows = sources if isinstance(sources, list) else []
    for source in source_rows:
        if not isinstance(source, Mapping):
            continue
        source_id = str(source.get("source_id") or "")
        for json_path in required_paths.get(source_id, ()):
            rows.append({
                "source_id": source_id,
                "artifact_id": str(source.get("artifact_id") or ""),
                "artifact_sha256": str(source.get("sha256") or ""),
                "json_path": json_path,
                "min_returned_bytes": 1,
                "max_items": 0,
                "max_chars": 0,
                "allow_truncated": False,
            })

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("source_id") or ""),
            str(row.get("artifact_id") or ""),
            str(row.get("artifact_sha256") or row.get("sha256") or ""),
            str(row.get("json_path") or "$"),
        )
        if not all(key[:3]) or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def materialize_attempt_source_ref(
    *,
    state_dir: Path,
    project_root: Path,
    ref: str,
    source_id: str,
    kind: str = "artifact",
) -> dict[str, Any]:
    """Resolve one state/project ref into an immutable manifest descriptor."""

    return _source_from_ref(
        state_dir=state_dir,
        project_root=project_root,
        ref=ref,
        source_id=source_id,
        kind=kind,
    )


def build_input_consumption_policy(
    *,
    workflow_run_id: str,
    attempt_id: str,
    required_reads: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = []
    for item in required_reads:
        rows.append({
            "source_id": str(item.get("source_id") or ""),
            "artifact_id": str(item.get("artifact_id") or ""),
            "artifact_sha256": str(item.get("artifact_sha256") or item.get("sha256") or ""),
            "json_path": str(item.get("json_path") or "$"),
            "min_returned_bytes": int(item.get("min_returned_bytes") or 1),
            "max_items": int(item.get("max_items") or 0),
            "max_chars": int(item.get("max_chars") or 0),
            "allow_truncated": bool(item.get("allow_truncated", False)),
        })
    return {
        "schema_version": READ_POLICY_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "attempt_id": attempt_id,
        "required_reads": rows,
    }


def write_input_consumption_policy(
    state_dir: Path,
    policy: Mapping[str, Any],
    *,
    source_event_id: str = "",
) -> dict[str, Any]:
    return write_immutable_json_sidecar(
        state_dir,
        policy,
        root="attempts/read-policies",
        kind="input_consumption_policy",
        schema_version=READ_POLICY_SCHEMA,
        created_by="artifact-read-ledger",
        source_event_id=source_event_id,
    )


def read_attempt_artifact(
    state_dir: Path,
    *,
    manifest: Mapping[str, Any],
    source_id: str,
    artifact_id: str,
    json_path: str = "$",
    max_items: int = 0,
    max_chars: int = 0,
) -> dict[str, Any]:
    source = _find_source(manifest, source_id=source_id, artifact_id=artifact_id)
    allowed_paths = _strings(source.get("allowed_paths")) or ["$"]
    if "$" not in allowed_paths and json_path not in allowed_paths:
        raise ArtifactReadError(
            f"json_path {json_path!r} is not allowed for {source_id}/{artifact_id}"
        )
    ref = str(source.get("ref") or "")
    path = _manifest_ref_path(state_dir, ref)
    if not path.exists() or not path.is_file():
        raise ArtifactReadError(f"artifact ref missing: {ref}")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    expected = str(source.get("sha256") or "")
    if expected and digest != expected:
        raise ArtifactReadError(
            f"artifact hash mismatch for {ref}: expected {expected}, got {digest}"
        )
    selected: Any
    if json_path != "$":
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactReadError("json_path requires a JSON artifact") from exc
        selected = _select_json_path(decoded, json_path)
    else:
        try:
            selected = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            selected = raw.decode("utf-8", errors="replace")
    selected = _limit_items(selected, max_items=max_items)
    encoded = _encode_selected(selected)
    truncated = False
    if max_chars > 0 and len(encoded) > max_chars:
        encoded = encoded[:max_chars]
        truncated = True
    row = {
        "schema_version": ARTIFACT_READ_SCHEMA,
        "workflow_run_id": str(manifest.get("workflow_run_id") or ""),
        "task_id": str(manifest.get("task_id") or ""),
        "attempt_id": str(manifest.get("attempt_id") or ""),
        "dispatch_id": str(manifest.get("dispatch_id") or ""),
        "source_id": source_id,
        "artifact_id": artifact_id,
        "artifact_ref": ref,
        "artifact_sha256": digest,
        "json_path": json_path,
        "bytes": len(raw),
        "returned_bytes": len(encoded.encode("utf-8")),
        "truncated": truncated,
        "max_items": max_items,
        "max_chars": max_chars,
        "read_at": datetime.now(timezone.utc).isoformat(),
    }
    append_artifact_read(state_dir, row)
    return {"content": encoded, "read": row}


def append_artifact_read(state_dir: Path, row: Mapping[str, Any]) -> Path:
    attempt_id = str(row.get("attempt_id") or "").strip()
    if not attempt_id:
        raise ArtifactReadError("artifact read row requires attempt_id")
    path = active_read_ledger_path(state_dir, attempt_id)
    line = json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with locked_path(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    return path


def seal_read_ledger(state_dir: Path, attempt_id: str) -> dict[str, Any]:
    active = active_read_ledger_path(state_dir, attempt_id)
    with locked_path(active):
        attempt_dir = active.parent
        sealed_paths = sorted(attempt_dir.glob("read-ledger-*.jsonl"))
        if not active.exists() and not sealed_paths:
            raise ArtifactReadError(f"active read ledger missing for {attempt_id}")

        # Output correction keeps the same attempt identity. The runtime may
        # eagerly seal the active ledger between individual correction reads,
        # so a later seal must carry forward every prior fragment instead of
        # validating only the most recent read.
        rows_by_body: dict[str, dict[str, Any]] = {}
        for path in [*sealed_paths, *([active] if active.exists() else [])]:
            for row in _parse_ledger(
                path.read_bytes(),
                expected_attempt_id=attempt_id,
            ):
                body = json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                rows_by_body.setdefault(body, row)
        ordered = sorted(
            rows_by_body.items(),
            key=lambda item: (str(item[1].get("read_at") or ""), item[0]),
        )
        raw = "".join(f"{body}\n" for body, _ in ordered).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        sealed_ref = (
            f"artifacts/attempts/{_safe_component(attempt_id)}/"
            f"read-ledger-{digest[:16]}.jsonl"
        )
        sealed = sidecar_path(state_dir, sealed_ref)
        sealed.parent.mkdir(parents=True, exist_ok=True)
        if sealed.exists():
            if sealed.read_bytes() != raw:
                raise ArtifactReadError("sealed read ledger collision")
        else:
            atomic_write_text(sealed, raw.decode("utf-8"))
        active.unlink(missing_ok=True)
        return build_sidecar_ref_descriptor(
            kind="artifact_read_ledger",
            ref=sealed_ref,
            sha256=digest,
            byte_count=len(raw),
            content_type="application/x-ndjson",
            schema_version=READ_LEDGER_SCHEMA,
            created_by="artifact-read-ledger",
            required=True,
        )


def validate_required_reads(
    state_dir: Path,
    *,
    policy: Mapping[str, Any],
    ledger_descriptor: Mapping[str, Any],
) -> list[dict[str, str]]:
    try:
        hydrated = hydrate_sidecar_ref(state_dir, dict(ledger_descriptor))
    except Exception as exc:  # descriptor errors become admission issues
        return [{"field": "input_consumption.read_ledger_ref", "code": "invalid_ref", "message": str(exc)}]
    raw = hydrated.payload
    if not isinstance(raw, str):
        return [{"field": "input_consumption.read_ledger_ref", "code": "invalid_ledger"}]
    attempt_id = str(policy.get("attempt_id") or "")
    try:
        rows = _parse_ledger(raw.encode("utf-8"), expected_attempt_id=attempt_id)
    except ArtifactReadError as exc:
        return [{"field": "input_consumption.read_ledger_ref", "code": "invalid_ledger", "message": str(exc)}]
    issues: list[dict[str, str]] = []
    required = policy.get("required_reads")
    for index, item in enumerate(required if isinstance(required, list) else []):
        if not isinstance(item, Mapping):
            issues.append({"field": f"required_reads[{index}]", "code": "invalid_policy"})
            continue
        match = next((row for row in rows if _read_matches(row, item)), None)
        if match is None:
            issues.append({
                "field": f"required_reads[{index}]",
                "code": "required_read_missing",
                "message": f"{item.get('source_id')}/{item.get('artifact_id')} {item.get('json_path') or '$'}",
            })
    return issues


def active_read_ledger_path(state_dir: Path, attempt_id: str) -> Path:
    rel = f"artifacts/attempts/{_safe_component(attempt_id)}/read-ledger.active.jsonl"
    return sidecar_path(state_dir, rel)


def live_attempt_ids(events: Iterable[ZfEvent]) -> set[str]:
    live: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        attempt_id = str(
            payload.get("attempt_id")
            or payload.get("run_id")
            or payload.get("dispatch_id")
            or ""
        ).strip()
        if not attempt_id:
            continue
        if event.type in {"task.dispatched", "fanout.child.dispatched", "workflow.operation.started"}:
            live.add(_safe_component(attempt_id))
        elif event.type in {
            "dev.build.done",
            "dev.failed",
            "dev.blocked",
            "fanout.child.completed",
            "fanout.child.failed",
            "workflow.operation.settled",
            "workflow.operation.failed",
            "workflow.operation.blocked",
        }:
            live.discard(_safe_component(attempt_id))
    return live


def active_ledger_attempt_id(ref: str) -> str:
    match = _ATTEMPT_REF_RE.match(str(ref or ""))
    return str(match.group("attempt") if match else "")


def render_attempt_source_briefing(
    payload: Mapping[str, Any],
    *,
    state_dir: Path | None = None,
) -> str:
    ref = str(payload.get("attempt_source_manifest_ref") or "").strip()
    if not ref:
        return ""
    attempt_id = str(payload.get("attempt_id") or payload.get("run_id") or "")
    required = payload.get("required_reads") if isinstance(payload.get("required_reads"), list) else []
    from zf.runtime.cli_command import zf_cli_cmd

    cli_command = zf_cli_cmd()
    lines = [
        "## Controlled Artifact Inputs",
        "",
        f"- source_manifest_ref: `{ref}`",
        f"- attempt_id: `{attempt_id}`",
        (
            f"- List inputs with `{cli_command} artifact list --attempt "
            "<attempt-id>`."
        ),
        (
            f"- Read handoff-critical inputs with `{cli_command} artifact read "
            "--attempt <attempt-id> --source <source-id> --artifact <artifact-id>`."
        ),
        (
            "- Execute one literal CLI command per tool call. Do not use shell "
            "variables, aliases, loops, pipes, redirections, command "
            "substitution, or compound commands; Claude allowlist matching is "
            "performed before shell expansion."
        ),
    ]
    sources: list[dict[str, Any]] = []
    descriptor = payload.get("attempt_source_manifest")
    if state_dir is not None and isinstance(descriptor, Mapping):
        try:
            manifest = hydrate_sidecar_ref(Path(state_dir), dict(descriptor)).payload
        except Exception:
            manifest = {}
        raw_sources = manifest.get("sources") if isinstance(manifest, Mapping) else None
        if isinstance(raw_sources, list):
            sources = [
                {
                    "source_id": str(item.get("source_id") or ""),
                    "artifact_id": str(item.get("artifact_id") or ""),
                    "sha256": str(item.get("sha256") or ""),
                    "ref": str(item.get("ref") or ""),
                    "allowed_paths": list(item.get("allowed_paths") or ["$"]),
                }
                for item in raw_sources
                if isinstance(item, Mapping)
            ]
    if sources:
        lines.extend([
            "- Source Manifest index (allowed does not mean required):",
            "```json",
            json.dumps(sources, ensure_ascii=False, indent=2),
            "```",
        ])
    if required:
        lines.extend(["- Required reads for this attempt:", "```json", json.dumps(required, ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines) + "\n"


def _source_from_item(
    *,
    state_dir: Path,
    project_root: Path,
    item: Any,
    source_id: str,
) -> dict[str, Any]:
    if isinstance(item, Mapping):
        ref = str(item.get("ref") or item.get("path") or "").strip()
        digest = str(item.get("sha256") or "").strip()
        kind = str(item.get("kind") or "artifact")
        source = _source_from_ref(
            state_dir=state_dir,
            project_root=project_root,
            ref=ref,
            source_id=source_id,
            kind=kind,
        )
        if not source:
            return {}
        if digest and digest != str(source.get("sha256") or ""):
            return {}
        source.update({
            "source_id": str(item.get("source_id") or source_id),
            "artifact_id": str(item.get("artifact_id") or item.get("name") or Path(ref).name),
            "kind": kind,
            "allowed_paths": _strings(item.get("allowed_paths")) or ["$"],
        })
        return source
    return _source_from_ref(
        state_dir=state_dir,
        project_root=project_root,
        ref=str(item or ""),
        source_id=source_id,
        kind="artifact",
    )


def _source_from_ref(
    *,
    state_dir: Path,
    project_root: Path,
    ref: str,
    source_id: str,
    kind: str,
) -> dict[str, Any]:
    ref = str(ref or "").strip()
    if not ref or ":" in ref and not ref.startswith(("artifacts/", ".zf/")):
        return {}
    path = _resolve_ref_path(state_dir=state_dir, project_root=project_root, ref=ref)
    if not path.exists() or not path.is_file():
        return {}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    normalized_ref = ref
    try:
        normalized_ref = path.resolve().relative_to(state_dir.resolve()).as_posix()
        try:
            safe_sidecar_ref(normalized_ref)
        except SidecarRefError:
            normalized_ref = _copy_attempt_input(
                state_dir=state_dir,
                path=path,
                digest=digest,
            )
    except ValueError:
        try:
            path.resolve().relative_to(project_root.resolve())
        except ValueError:
            return {}
        # Controlled reads operate exclusively on immutable state sidecars.
        # Project inputs are copied by digest so restart/replay cannot observe
        # a later edit through the same manifest ref.
        normalized_ref = _copy_attempt_input(
            state_dir=state_dir,
            path=path,
            digest=digest,
        )
    return {
        "source_id": source_id,
        "artifact_id": path.name,
        "kind": kind,
        "ref": normalized_ref,
        "sha256": digest,
        "allowed_paths": ["$"],
    }


def _copy_attempt_input(*, state_dir: Path, path: Path, digest: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", path.name).strip("-.") or "input"
    normalized_ref = f"artifacts/attempt-inputs/{digest[:16]}-{safe_name}"
    materialized = sidecar_path(state_dir, normalized_ref)
    with locked_path(materialized):
        materialized.parent.mkdir(parents=True, exist_ok=True)
        if materialized.exists():
            if hashlib.sha256(materialized.read_bytes()).hexdigest() != digest:
                raise ArtifactReadError("attempt input sidecar collision")
        else:
            materialized.write_bytes(path.read_bytes())
            with materialized.open("rb") as handle:
                os.fsync(handle.fileno())
    return normalized_ref


def _resolve_ref_path(*, state_dir: Path, project_root: Path, ref: str) -> Path:
    raw = str(ref or "").strip()
    if raw.startswith(".zf/"):
        return state_dir / raw[len(".zf/"):]
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    state_candidate = state_dir / path
    return state_candidate if state_candidate.exists() else project_root / path


def _manifest_ref_path(state_dir: Path, ref: str) -> Path:
    raw = str(ref or "").strip()
    if raw.startswith(".zf/"):
        raw = raw[len(".zf/"):]
    path = Path(raw)
    if path.is_absolute():
        try:
            path.resolve().relative_to(state_dir.resolve())
        except ValueError as exc:
            raise ArtifactReadError("manifest artifact escapes state_dir") from exc
        return path
    return sidecar_path(state_dir, safe_sidecar_ref(raw))


def _find_source(
    manifest: Mapping[str, Any],
    *,
    source_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    for item in manifest.get("sources") if isinstance(manifest.get("sources"), list) else []:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("source_id") or "") == source_id and str(item.get("artifact_id") or "") == artifact_id:
            return dict(item)
    raise ArtifactReadError(f"source not found: {source_id}/{artifact_id}")


def _select_json_path(value: Any, path: str) -> Any:
    if path == "$":
        return value
    if not path.startswith("$."):
        raise ArtifactReadError("json_path must use the supported $.field[index] form")
    current = value
    token_re = re.compile(r"([A-Za-z0-9_-]+)|\[(\d+)\]")
    consumed = "$"
    for match in token_re.finditer(path[2:]):
        key, index = match.groups()
        if key is not None:
            if not isinstance(current, Mapping) or key not in current:
                raise ArtifactReadError(f"json_path missing at {consumed}.{key}")
            current = current[key]
            consumed += f".{key}"
        else:
            position = int(index)
            if not isinstance(current, list) or position >= len(current):
                raise ArtifactReadError(f"json_path index missing at {consumed}[{position}]")
            current = current[position]
            consumed += f"[{position}]"
    normalized = re.sub(r"\.([A-Za-z0-9_-]+)", r"\1", path[2:])
    matched = "".join(match.group(0) for match in token_re.finditer(path[2:]))
    if normalized != matched:
        raise ArtifactReadError("json_path contains unsupported syntax")
    return current


def _limit_items(value: Any, *, max_items: int) -> Any:
    if max_items <= 0:
        return value
    if isinstance(value, list):
        return value[:max_items]
    if isinstance(value, Mapping):
        return dict(list(value.items())[:max_items])
    return value


def _encode_selected(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _parse_ledger(raw: bytes, *, expected_attempt_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ArtifactReadError(f"invalid read ledger line {index}") from exc
        if not isinstance(row, dict) or row.get("schema_version") != ARTIFACT_READ_SCHEMA:
            raise ArtifactReadError(f"invalid read ledger row {index}")
        if expected_attempt_id and str(row.get("attempt_id") or "") != expected_attempt_id:
            raise ArtifactReadError(f"attempt identity mismatch on ledger line {index}")
        rows.append(row)
    return rows


def _read_matches(row: Mapping[str, Any], requirement: Mapping[str, Any]) -> bool:
    for field in ("source_id", "artifact_id"):
        if str(requirement.get(field) or "") != str(row.get(field) or ""):
            return False
    digest = str(requirement.get("artifact_sha256") or requirement.get("sha256") or "")
    if digest and digest != str(row.get("artifact_sha256") or ""):
        return False
    if str(requirement.get("json_path") or "$") != str(row.get("json_path") or "$"):
        return False
    if int(row.get("returned_bytes") or 0) < int(requirement.get("min_returned_bytes") or 1):
        return False
    if bool(row.get("truncated")) and not bool(requirement.get("allow_truncated", False)):
        return False
    for field in ("max_items", "max_chars"):
        required_value = int(requirement.get(field) or 0)
        if required_value and int(row.get(field) or 0) != required_value:
            return False
    return True


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-._") or "attempt"


def _strings(value: Any) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else [value] if value else []
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


__all__ = [
    "ARTIFACT_READ_SCHEMA",
    "READ_LEDGER_SCHEMA",
    "READ_POLICY_SCHEMA",
    "SOURCE_MANIFEST_SCHEMA",
    "ArtifactReadError",
    "active_ledger_attempt_id",
    "active_read_ledger_path",
    "append_artifact_read",
    "build_attempt_source_manifest",
    "build_input_consumption_policy",
    "canonical_required_reads",
    "live_attempt_ids",
    "materialize_attempt_source_ref",
    "read_attempt_artifact",
    "render_attempt_source_briefing",
    "seal_read_ledger",
    "source_manifest_from_payload",
    "validate_required_reads",
    "write_attempt_source_manifest",
    "write_input_consumption_policy",
]
