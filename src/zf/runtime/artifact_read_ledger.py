"""Attempt-local source manifests and controlled artifact-read evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.artifact_access import ArtifactAccessError, require_artifact_access
from zf.runtime.artifact_read_liveness import live_attempt_ids
from zf.runtime.artifact_read_requirements import (
    ArtifactReadRequirementError,
    build_canonical_required_reads,
)
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import (
    SidecarRefError,
    build_sidecar_ref_descriptor,
    safe_sidecar_ref,
    sidecar_path,
)
from zf.runtime import semantic_replan_handoff
from zf.runtime.artifact_read_ledger_validation import (
    ARTIFACT_READ_SCHEMA, ArtifactReadError, _parse_ledger, _read_matches,
    required_read_satisfied, validate_required_reads,
)


SOURCE_MANIFEST_SCHEMA = "attempt-source-manifest.v1"
READ_POLICY_SCHEMA = "input-consumption-policy.v1"
READ_LEDGER_SCHEMA = "artifact-read-ledger.v1"
_ATTEMPT_REF_RE = re.compile(
    r"^artifacts/attempts/(?P<attempt>[^/]+)/read-ledger\.active\.jsonl$"
)


def build_attempt_source_manifest(
    *,
    workflow_run_id: str,
    task_id: str,
    attempt_id: str,
    dispatch_id: str,
    sources: Iterable[Mapping[str, Any]],
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(metadata or {})
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
        access_scope = (
            dict(raw.get("access_scope") or {})
            if isinstance(raw.get("access_scope"), Mapping)
            else {}
        )
        retention = (
            dict(raw.get("retention") or {})
            if isinstance(raw.get("retention"), Mapping)
            else {}
        )
        occurrence_id = str(raw.get("occurrence_id") or "").strip()
        if not occurrence_id:
            occurrence_body = {
                "dispatch_id": dispatch_id,
                "source_event_id": str(
                    raw.get("source_event_id")
                    or metadata.get("source_event_id")
                    or ""
                ),
                "source_id": source_id,
                "artifact_id": artifact_id,
                "ref": ref,
                "sha256": digest,
                "access_scope": access_scope,
                "retention": retention,
            }
            occurrence_id = "attempt-occurrence:" + hashlib.sha256(
                json.dumps(
                    occurrence_body,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        row = {
            "source_id": source_id,
            "artifact_id": artifact_id,
            "kind": str(raw.get("kind") or "artifact"),
            "ref": ref,
            "sha256": digest,
            "allowed_paths": _strings(raw.get("allowed_paths")) or ["$"],
            "occurrence_id": occurrence_id,
            "source_event_id": str(
                raw.get("source_event_id")
                or metadata.get("source_event_id")
                or ""
            ),
            "schema_version": str(raw.get("schema_version") or ""),
            "access_scope": access_scope,
            "retention": retention,
        }
        if isinstance(raw.get("recovery_ref"), Mapping):
            row["recovery_ref"] = dict(raw["recovery_ref"])
        normalized.append(row)
    manifest = {
        "schema_version": SOURCE_MANIFEST_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "dispatch_id": dispatch_id,
        "sources": normalized,
    }
    if metadata:
        for key in (
            "contract_revision",
            "task_map_generation",
            "feedback_revision",
            "base_commit",
            "target_commit",
            "task_ref",
            "plan_artifact_package_id",
            "plan_artifact_package_ref",
            "plan_artifact_package_digest",
            "plan_revision",
            "source_snapshot",
            "resolver",
            "source_event_id",
            "read_purpose",
            "handoff_authority_profile",
            "handoff_authority_contract",
            "candidate_snapshot",
            "candidate_event_id",
            "candidate_head_commit",
            "candidate_snapshot_digest",
            "orchestrator_context_route_source_ids",
            "context_policy",
            "context_sections",
        ):
            value = metadata.get(key)
            if value not in (None, "", {}, []):
                manifest[key] = value
    return manifest


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
    manifest_metadata: Mapping[str, Any] | None = None,
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
    from zf.runtime.result_handoff_sources import result_handoff_source_entries

    sources.extend(result_handoff_source_entries(payload))
    sources.extend(semantic_replan_handoff.replan_sources(
        state_dir, payload, attempt_id=attempt_id, source_event_id=source_event_id,
    ))
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
    from zf.runtime.artifact_source_versions import pin_source_versions

    pin_source_versions(state_dir, sources)
    manifest = build_attempt_source_manifest(
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        dispatch_id=dispatch_id,
        sources=sources,
        metadata=manifest_metadata,
    )
    if payload.get("_context_delivery_enabled", True):
        from zf.runtime.context_delivery import attach_context_sections

        required_reads = canonical_required_reads(
            manifest,
            output_profile_id=str(payload.get("output_profile_id") or ""),
            explicit=(
                payload.get("required_reads")
                if isinstance(payload.get("required_reads"), list)
                else ()
            ),
        )
        manifest = attach_context_sections(
            manifest,
            output_profile_id=str(payload.get("output_profile_id") or ""),
            explicit_required_reads=required_reads,
            context_inheritance=payload.get("context_inheritance"),
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
    try:
        return build_canonical_required_reads(
            manifest,
            output_profile_id=output_profile_id,
            explicit=explicit,
        )
    except ArtifactReadRequirementError as exc:
        raise ArtifactReadError(str(exc)) from exc


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
    artifact_sha256: str = "",
    json_path: str = "$",
    max_items: int = 0,
    max_chars: int = 0,
    page_start: int | None = None,
    page_size: int = 0,
    record: bool = True,
    actor: str = "",
    role: str = "",
    provider: str = "",
    purpose: str = "",
) -> dict[str, Any]:
    source = _find_source(manifest, source_id=source_id, artifact_id=artifact_id,
                          artifact_sha256=artifact_sha256)
    read_purpose = str(purpose or manifest.get("read_purpose") or "").strip()
    try:
        require_artifact_access(
            source.get("access_scope"),
            actor=actor,
            role=role,
            purpose=read_purpose,
        )
    except ArtifactAccessError as exc:
        append_artifact_read(
            state_dir,
            {
                "schema_version": ARTIFACT_READ_SCHEMA,
                "status": "denied",
                "workflow_run_id": str(
                    manifest.get("workflow_run_id") or ""
                )[:160],
                "task_id": str(manifest.get("task_id") or "")[:160],
                "attempt_id": str(manifest.get("attempt_id") or "")[:160],
                "dispatch_id": str(manifest.get("dispatch_id") or "")[:160],
                "source_id": str(source_id or "")[:160],
                "artifact_id": str(artifact_id or "")[:160],
                "json_path": str(json_path or "$")[:256],
                "returned_bytes": 0,
                "truncated": False,
                "consumer_actor": str(actor or "")[:160],
                "consumer_role": str(role or "")[:160],
                "consumer_provider": str(provider or "")[:160],
                "consumer_purpose": read_purpose[:160],
                "occurrence_id": str(
                    source.get("occurrence_id") or ""
                )[:256],
                "denial_code": "artifact_access_denied",
                "read_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise ArtifactReadError(
            "artifact read is not authorized"
        ) from exc
    allowed_paths = _strings(source.get("allowed_paths")) or ["$"]
    if "$" not in allowed_paths and json_path not in allowed_paths:
        raise ArtifactReadError(
            f"json_path {json_path!r} is not allowed for {source_id}/{artifact_id}"
        )
    ref = str(source.get("ref") or "")
    path = _manifest_ref_path(state_dir, ref)
    from zf.runtime.artifact_source_versions import read_source_version

    try:
        raw, recovery_mode = read_source_version(state_dir, dict(source), path=path)
    except (OSError, ValueError, KeyError) as exc:
        raise ArtifactReadError(str(exc)) from exc
    digest = hashlib.sha256(raw).hexdigest()
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
    if page_start is not None and (page_start < 0 or not 1 <= page_size <= 8192 or max_items or max_chars):
        raise ArtifactReadError("paged reads require a non-negative start, size 1..8192, and no limits")
    limited_items = bool(
        max_items > 0 and isinstance(selected, (list, Mapping)) and len(selected) > max_items
    )
    selected = _limit_items(selected, max_items=max_items)
    encoded = _encode_selected(selected)
    truncated = limited_items
    page_meta: dict[str, Any] = {}
    if page_start is not None:
        total = len(encoded)
        if page_start >= total:
            raise ArtifactReadError("page start is outside the selected content")
        page_end = min(total, page_start + page_size)
        page_meta = {
            "page_start": page_start, "page_end": page_end, "page_total": total,
            "selected_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "selected_bytes": len(encoded.encode("utf-8")),
        }
        encoded = encoded[page_start:page_end]
        truncated = page_start > 0 or page_end < total
    if max_chars > 0 and len(encoded) > max_chars:
        encoded = encoded[:max_chars]
        truncated = True
    row = {
        "schema_version": ARTIFACT_READ_SCHEMA,
        "status": "read",
        "production_status": "generated",
        "provider_delivery_status": "unknown",
        "workflow_run_id": str(manifest.get("workflow_run_id") or ""),
        "task_id": str(manifest.get("task_id") or ""),
        "attempt_id": str(manifest.get("attempt_id") or ""),
        "dispatch_id": str(manifest.get("dispatch_id") or ""),
        "source_id": source_id,
        "artifact_id": artifact_id,
        "artifact_ref": ref,
        "artifact_sha256": digest,
        "recovery_mode": recovery_mode,
        "json_path": json_path,
        "bytes": len(raw),
        "returned_bytes": len(encoded.encode("utf-8")),
        "truncated": truncated,
        "max_items": max_items,
        "max_chars": max_chars,
        "consumer_actor": str(actor or ""),
        "consumer_role": str(role or ""),
        "consumer_provider": str(provider or ""),
        "consumer_purpose": read_purpose,
        "occurrence_id": str(source.get("occurrence_id") or ""),
        "read_at": datetime.now(timezone.utc).isoformat(),
        **page_meta,
    }
    if record:
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


def seal_read_ledger(state_dir: Path, attempt_id: str, *, allow_empty: bool = False) -> dict[str, Any]:
    active = active_read_ledger_path(state_dir, attempt_id)
    with locked_path(active):
        attempt_dir = active.parent
        sealed_paths = sorted(attempt_dir.glob("read-ledger-*.jsonl"))
        if not active.exists() and not sealed_paths and not allow_empty:
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
        from zf.runtime.artifact_output_delivery import observe_artifact_delivery

        enriched = observe_artifact_delivery(state_dir, list(rows_by_body.values()))
        rows_by_body = {json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")): row
                        for row in enriched}
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


def active_read_ledger_path(state_dir: Path, attempt_id: str) -> Path:
    rel = f"artifacts/attempts/{_safe_component(attempt_id)}/read-ledger.active.jsonl"
    return sidecar_path(state_dir, rel)


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
        f"- List: `{cli_command} artifact list --attempt <attempt-id>`.",
        "- Read required inputs with the exact commands below.",
        (
            "- Run one literal command per tool call; no variables, aliases, "
            "loops, pipes, redirections, substitutions, or compound commands "
            "(Claude allowlist matching is literal)."
        ),
    ]
    if required:
        lines.extend([
            f"- Preferred deduplicated read: `{cli_command} artifact read --attempt {attempt_id} --required`.",
            "  It delivers every required slice, sharing identical bodies while recording each source. "
            "All responses use required-artifact-page.v2: consume every items[].content fragment. "
            "If the response has `next_cursor`, continue the same command with `--cursor <next_cursor>` "
            "until it is null. Consume every page; do not repeat individual reads after a complete bundle.",
            "- Required pages default to 8192 UTF-8 bytes including metadata, not tokens. "
            "If the tool output budget is smaller, use --page-bytes 2048; consume pages "
            "separately and leave room for tool wrappers. Return each raw page unchanged; do not filter fields or batch pages into a summary. CLI receipts prove production; "
            "provider delivery may remain unknown and never proves understanding.",
            "- If an output is too large or truncated, read that exact source and JSON path "
            "with `--page-start 0 --page-size 8192`. Read and consume every page, using "
            "the returned `next_page_start` until it is null. The ledger accepts pages "
            "only when they cover the entire selected input without gaps.",
        ])
        lines.extend(["- Required reads for this attempt:"])
        for item in required:
            source_id = str(item.get("source_id") or "")
            artifact_id = str(item.get("artifact_id") or "")
            json_path = str(item.get("json_path") or "$")
            digest = str(
                item.get("artifact_sha256")
                or item.get("sha256")
                or ""
            )
            lines.append(
                f"  - `{cli_command} artifact read --attempt {attempt_id} "
                f"--source {source_id} --artifact {artifact_id} "
                f"--json-path {shlex.quote(json_path)}` (sha256 `{digest[:16]}`)"
            )
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
            "occurrence_id": str(item.get("occurrence_id") or ""),
            "source_event_id": str(item.get("source_event_id") or ""),
            "schema_version": str(item.get("schema_version") or ""),
            "access_scope": (
                dict(item.get("access_scope") or {})
                if isinstance(item.get("access_scope"), Mapping)
                else {}
            ),
            "retention": (
                dict(item.get("retention") or {})
                if isinstance(item.get("retention"), Mapping)
                else {}
            ),
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
    artifact_sha256: str = "",
) -> dict[str, Any]:
    matches = []
    for item in manifest.get("sources") if isinstance(manifest.get("sources"), list) else []:
        if not isinstance(item, Mapping):
            continue
        if (str(item.get("source_id") or "") == source_id
                and str(item.get("artifact_id") or "") == artifact_id
                and (not artifact_sha256 or item.get("sha256") == artifact_sha256)):
            matches.append(dict(item))
    if len(matches) > 1:
        raise ArtifactReadError(f"ambiguous source: {source_id}/{artifact_id}; use the pinned required read")
    if matches:
        return matches[0]
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
