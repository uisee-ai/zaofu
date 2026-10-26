"""Bound, read-only artifact access for Plan review; no directory discovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_refs import resolve_runtime_artifact_ref
from zf.runtime.plan_artifact_ports import coerce_plan_port_descriptors
from zf.runtime.call_result_envelope import write_immutable_json_sidecar

PLAN_PREVIEW_BUNDLE_SCHEMA = "plan-preview-bundle.v1"
PLAN_REVIEW_REF_FIELDS = (
    "plan_artifact_ref", "plan_artifact_digest", "plan_ref",
    "plan_artifact_package_ref", "plan_artifact_package_digest",
    "plan_preview_bundle_ref", "plan_preview_bundle_digest",
    "requirement_spec_ref",
    "source_index_ref", "workflow_run_id",
)


def preview_artifact_path(ref: str, *, state_dir: Path, project_root: Path | None) -> Path:
    path = resolve_runtime_artifact_ref(
        ref, state_dir=state_dir, project_root=project_root or state_dir.parent,
        search_workdirs=False,
    ).resolve()
    roots = [state_dir.resolve()]
    if project_root is not None:
        roots.append(project_root.resolve())
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError("artifact is outside the current project")
    return path


def read_preview_artifact(
    ref: str, *, state_dir: Path, project_root: Path | None, digest: str = "",
) -> str:
    path = preview_artifact_path(ref, state_dir=state_dir, project_root=project_root)
    # Fail explicitly instead of showing a truncated body as a complete Plan.
    with path.open("rb") as stream:
        body = stream.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError("artifact exceeds the 8 MiB preview limit")
    if digest and hashlib.sha256(body).hexdigest() != digest:
        raise ValueError("artifact digest mismatch")
    return body.decode("utf-8")


def approval_artifact_refs(
    payload: Mapping[str, Any], *, state_dir: Path, project_root: Path,
) -> dict[str, Any]:
    refs = {key: payload[key] for key in PLAN_REVIEW_REF_FIELDS if payload.get(key)}
    ref = str(refs.get("plan_artifact_ref") or refs.get("plan_ref") or "")
    if ref and not refs.get("plan_artifact_digest"):
        try:
            body = read_preview_artifact(ref, state_dir=state_dir, project_root=project_root)
            refs["plan_artifact_digest"] = hashlib.sha256(body.encode()).hexdigest()
        except (OSError, ValueError):
            # Keep the declared ref: the projection must report it as unavailable.
            pass
    return refs


def preview_package_artifacts(
    payload: Mapping[str, Any], *, state_dir: Path, project_root: Path | None,
) -> tuple[list[dict[str, str]], str]:
    ref = str(
        payload.get("plan_artifact_package_ref")
        or payload.get("plan_preview_bundle_ref")
        or ""
    )
    if not ref:
        return [], ""
    digest = str(
        payload.get("plan_artifact_package_digest")
        or payload.get("plan_preview_bundle_digest")
        or ""
    )
    try:
        if not digest:
            raise ValueError("plan package digest missing")
        package = json.loads(read_preview_artifact(
            ref, state_dir=state_dir, project_root=project_root, digest=digest,
        ))
        if package.get("schema_version") == PLAN_PREVIEW_BUNDLE_SCHEMA:
            artifacts = package.get("artifacts")
            if not isinstance(artifacts, list):
                raise ValueError("preview bundle artifacts must be a list")
            if payload.get("workflow_run_id") and package.get("workflow_run_id") != payload["workflow_run_id"]:
                raise ValueError("preview bundle workflow mismatch")
            bound = {
                str(item.get("name") or ""): item
                for item in artifacts
                if isinstance(item, Mapping)
            }
            for logical_name, payload_key in (("accepted_plan", "plan_artifact_ref"), ("task_map", "task_map_ref")):
                declared = str(payload.get(payload_key) or "").strip()
                item = bound.get(logical_name)
                if declared and item and str(item.get("ref") or ""):
                    expected = preview_artifact_path(declared, state_dir=state_dir, project_root=project_root)
                    actual = preview_artifact_path(str(item["ref"]), state_dir=state_dir, project_root=project_root)
                    if expected != actual:
                        raise ValueError(f"preview bundle {logical_name} mismatch")
            return [
                {
                    "name": str(item.get("name") or ""),
                    "ref": str(item.get("ref") or ""),
                    "sha256": str(item.get("sha256") or ""),
                }
                for item in artifacts
                if isinstance(item, Mapping)
                and item.get("name") and item.get("ref") and item.get("sha256")
            ], ""
        from zf.runtime.plan_artifact_package import validate_plan_artifact_package_shape

        validate_plan_artifact_package_shape(package)
        if payload.get("workflow_run_id") and package["workflow_run_id"] != payload["workflow_run_id"]:
            raise ValueError("plan package workflow mismatch")
        ports = [*package["produced"], *package["inherited"]]
        task_map = next((p for p in ports if p["logical_name"] == "task_map"), None)
        if task_map and payload.get("task_map_ref"):
            expected = preview_artifact_path(str(payload["task_map_ref"]), state_dir=state_dir, project_root=project_root)
            actual = preview_artifact_path(task_map["ref"], state_dir=state_dir, project_root=project_root)
            if expected != actual:
                raise ValueError("plan package task map mismatch")
        return [{"name": p["logical_name"], "ref": p["ref"], "sha256": p["sha256"]} for p in ports], ""
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [], str(exc)


def read_plan_preview_bundle(
    payload: Mapping[str, Any],
    *,
    state_dir: Path,
    project_root: Path | None,
) -> tuple[dict[str, Any], str]:
    """Read the owner-gate candidate bundle and verify its digest/binding."""

    ref = str(payload.get("plan_preview_bundle_ref") or "").strip()
    if not ref:
        return {}, ""
    digest = str(payload.get("plan_preview_bundle_digest") or "").strip()
    try:
        if not digest:
            raise ValueError("preview bundle digest missing")
        body = json.loads(read_preview_artifact(
            ref,
            state_dir=state_dir,
            project_root=project_root,
            digest=digest,
        ))
        if not isinstance(body, dict) or body.get("schema_version") != PLAN_PREVIEW_BUNDLE_SCHEMA:
            raise ValueError("unsupported preview bundle schema")
        workflow_run_id = str(payload.get("workflow_run_id") or "").strip()
        if workflow_run_id and body.get("workflow_run_id") != workflow_run_id:
            raise ValueError("preview bundle workflow mismatch")
        return body, ""
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {}, str(exc)


def bind_plan_approval_preview(
    state_dir: Path,
    trigger_payload: Mapping[str, Any],
    project_root: Path | None,
    approval_payload: dict[str, Any],
) -> dict[str, Any]:
    """Bind immutable review evidence when requesting approval, before minting."""
    payload = {
        **trigger_payload,
        **approval_payload,
        "workflow_run_id": approval_payload["trace_id"],
        "fanout_id": str(trigger_payload.get("fanout_id") or ""),
    }
    refs = {
        **approval_payload,
        "source_index_ref": str(trigger_payload.get("source_index_ref") or ""),
        "requirement_spec_ref": str(trigger_payload.get("requirement_spec_ref") or ""),
        "plan_artifact_ref": str(
            trigger_payload.get("plan_artifact_ref") or trigger_payload.get("plan_ref") or ""
        ),
        "plan_artifact_digest": str(trigger_payload.get("plan_artifact_digest") or ""),
    }
    bundle = write_plan_preview_bundle(
        state_dir, payload=payload, refs=refs, project_root=project_root,
        source_event_id=approval_payload["plan_id"],
    )
    return {
        **approval_payload,
        "plan_preview_bundle_ref": str(bundle.get("ref") or ""),
        "plan_preview_bundle_digest": str(bundle.get("sha256") or ""),
        "plan_content_profile": str(trigger_payload.get("plan_content_profile") or "derived_plan_full"),
        "authority_refs": list(trigger_payload.get("authority_refs") or []),
    }


def write_plan_preview_bundle(
    state_dir: Path,
    *,
    payload: Mapping[str, Any],
    refs: Mapping[str, Any],
    project_root: Path | None = None,
    source_event_id: str = "",
) -> dict[str, Any]:
    """Persist the candidate Plan and explicit source/port refs for human review.

    This is a read-only review bundle. It does not admit a plan package or
    mint canonical tasks, so it is safe to create before the owner gate.
    Every body comes from an explicit Planner ref or inline plan port; there is
    no directory discovery.
    """

    report = payload.get("report")
    report = report if isinstance(report, Mapping) else {}
    artifacts: list[dict[str, str]] = []
    seen: set[str] = set()
    seen_refs: set[str] = set()

    def add_artifact(name: str, ref: str, digest: str = "") -> None:
        name = str(name or "").strip()
        ref = str(ref or "").strip()
        if not name or not ref or name in seen:
            return
        if ref in seen_refs:
            return
        try:
            body = read_preview_artifact(
                ref,
                state_dir=state_dir,
                project_root=project_root or state_dir.parent,
            )
            actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
        except (OSError, ValueError, UnicodeError):
            return
        if digest and digest != actual:
            return
        artifacts.append({"name": name, "ref": ref, "sha256": actual})
        seen.add(name)
        seen_refs.add(ref)

    plan_ref = str(refs.get("plan_artifact_ref") or refs.get("plan_ref") or "")
    add_artifact("accepted_plan", plan_ref, str(refs.get("plan_artifact_digest") or ""))
    add_artifact("task_map", str(refs.get("task_map_ref") or ""), str(refs.get("task_map_digest") or ""))
    add_artifact("source_index", str(refs.get("source_index_ref") or ""))
    add_artifact("requirement_spec", str(refs.get("requirement_spec_ref") or ""), str(refs.get("requirement_spec_digest") or ""))

    raw_refs = payload.get("artifact_refs") or report.get("artifact_refs") or []
    if isinstance(raw_refs, list):
        for index, raw_ref in enumerate(raw_refs, start=1):
            if isinstance(raw_ref, Mapping):
                ref = str(
                    raw_ref.get("ref")
                    or raw_ref.get("path")
                    or raw_ref.get("artifact_ref")
                    or ""
                ).strip()
                digest = str(raw_ref.get("sha256") or raw_ref.get("digest") or "").strip()
            else:
                ref = str(raw_ref or "").strip()
                digest = ""
            if not ref or ref == plan_ref:
                continue
            name = Path(ref).name or f"source_{index}"
            if name in {"task_map.json", "source_index.json"}:
                continue
            if name.endswith((".md", ".markdown")):
                add_artifact(f"requirement_{index}_{name}", ref, digest)

    # Authority documents are part of the human approval contract even when
    # the provider report omitted them from its generic artifact list. Bind
    # them explicitly so the review bundle remains complete and replayable.
    authority_refs = refs.get("authority_refs") or payload.get("authority_refs") or []
    if isinstance(authority_refs, list):
        for index, raw_ref in enumerate(authority_refs, start=1):
            ref = str(raw_ref or "").strip()
            if not ref:
                continue
            name = Path(ref).name or f"authority_{index}"
            add_artifact(f"authority_{index}_{name}", ref)

    raw_ports = payload.get("plan_ports") or report.get("plan_ports") or []
    for port in coerce_plan_port_descriptors(raw_ports):
        logical_name = str(port.get("logical_name") or "").strip()
        if not logical_name or logical_name in seen:
            continue
        body = port.get("body")
        if isinstance(body, Mapping):
            descriptor = write_immutable_json_sidecar(
                state_dir,
                dict(body),
                root="plan-preview/ports",
                kind=logical_name,
                schema_version=str(port.get("schema_version") or body.get("schema_version") or ""),
                created_by="plan-preview-bundle",
                source_event_id=source_event_id,
            )
            add_artifact(logical_name, str(descriptor.get("ref") or ""), str(descriptor.get("sha256") or ""))
        else:
            add_artifact(
                logical_name,
                str(port.get("ref") or ""),
                str(port.get("sha256") or port.get("digest") or ""),
            )

    bundle = {
        "schema_version": PLAN_PREVIEW_BUNDLE_SCHEMA,
        "workflow_run_id": str(payload.get("workflow_run_id") or ""),
        "fanout_id": str(payload.get("fanout_id") or ""),
        "source_event_id": source_event_id,
        "content_profile": str(payload.get("plan_content_profile") or "derived_plan"),
        "authority_refs": [
            str(item)
            for item in (payload.get("authority_refs") or report.get("authority_refs") or [])
            if str(item).strip()
        ],
        "artifacts": artifacts,
    }
    return write_immutable_json_sidecar(
        state_dir,
        bundle,
        root="plan-preview/bundles",
        kind="plan_preview_bundle",
        schema_version=PLAN_PREVIEW_BUNDLE_SCHEMA,
        created_by="plan-preview-bundle",
        source_event_id=source_event_id,
    )
