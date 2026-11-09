"""Canonical workflow artifact references for worker briefings."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def canonical_workflow_artifact_refs(
    refs: Sequence[Any],
    *,
    scalar_refs: Mapping[str, str],
) -> list[Any]:
    """Drop superseded plan handoff refs while preserving other evidence."""

    current = {
        key: str(value or "").strip()
        for key, value in scalar_refs.items()
        if str(value or "").strip()
    }
    package_ref = current.get("plan_artifact_package_ref", "")
    claim_ref = current.get("goal_claim_set_ref", "")
    task_map_ref = current.get("task_map_ref", "")
    source_index_ref = current.get("source_index_ref", "")

    def ref_of(item: Any) -> str:
        if isinstance(item, Mapping):
            return str(item.get("ref") or item.get("path") or "").strip()
        return str(item or "").strip()

    def keep(item: Any) -> bool:
        ref = ref_of(item)
        if not ref:
            return False
        if package_ref and "artifacts/plan-packages/" in ref:
            return ref == package_ref
        if claim_ref and "artifacts/goal-closure/claim-sets/" in ref:
            return ref == claim_ref
        if task_map_ref and ref.endswith("artifacts/plan/task_map.json"):
            return ref == task_map_ref
        if source_index_ref and ref.endswith("artifacts/plan/source_index.json"):
            return ref == source_index_ref
        return True

    out: list[Any] = []
    seen: set[str] = set()
    for item in [*refs, package_ref, claim_ref, task_map_ref, source_index_ref]:
        if not keep(item):
            continue
        key = ref_of(item)
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def build_workflow_ref_bundle(
    sources: Sequence[Mapping[str, Any]],
    *,
    workflow_ref_keys: Sequence[str],
) -> dict[str, Any]:
    """Build a worker input bundle from current scalar and list refs."""

    bundle: dict[str, Any] = {}
    source_refs: dict[str, str] = {}
    artifact_refs: list[Any] = []
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key in workflow_ref_keys:
            value = str(source.get(key) or "").strip()
            if value and key not in bundle:
                bundle[key] = value
        raw_source_refs = source.get("source_refs")
        if isinstance(raw_source_refs, Mapping):
            source_refs.update({
                str(k): str(v)
                for k, v in raw_source_refs.items()
                if str(v or "").strip()
            })
        raw_artifact_refs = source.get("artifact_refs")
        if isinstance(raw_artifact_refs, list):
            artifact_refs.extend(raw_artifact_refs)
    scalar_refs = {
        key: str(bundle.get(key) or "").strip()
        for key in (
            "plan_artifact_package_ref",
            "goal_claim_set_ref",
            "task_map_ref",
            "source_index_ref",
        )
    }
    artifact_refs = canonical_workflow_artifact_refs(
        artifact_refs,
        scalar_refs=scalar_refs,
    )
    if source_refs:
        bundle["source_refs"] = source_refs
    if artifact_refs:
        bundle["artifact_refs"] = artifact_refs
    return bundle


__all__ = ["build_workflow_ref_bundle", "canonical_workflow_artifact_refs"]
