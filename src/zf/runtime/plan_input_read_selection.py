"""Lossless report partitions and policy-bound first-review navigation.

No risk classifier lives here. Targeted scope is an approved Agent decision;
all original sources stay available through the existing controlled reader.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_source_versions import read_source_version
from zf.runtime.artifact_text_codec import decode_artifact_text, encode_selected
from zf.runtime.sidecar_refs import sidecar_path

# These are supplementary ports of the existing Plan contract, not inferred
# business relevance. The Critic selects their contents to answer its focus.
SUPPLEMENTARY_PORTS = frozenset({
    "capability_matrix", "acceptance_matrix", "test_matrix", "real_e2e_matrix",
    "coverage_ledger", "source_inventory", "product_acceptance_spec",
})


def _identity(source: Mapping[str, Any]) -> tuple:
    return source.get("source_id"), source.get("artifact_id"), source.get("sha256")


def _read(state_dir: Path, source: Mapping[str, Any]) -> Any:
    raw, _ = read_source_version(state_dir, dict(source), path=sidecar_path(state_dir, source["ref"]))
    if hashlib.sha256(raw).hexdigest() != source.get("sha256"):
        raise ValueError("source digest differs")
    return decode_artifact_text(raw, "$")


def _children(value: Any, path: str) -> list[tuple[str, Any]]:
    if isinstance(value, dict) and value:
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", key) for key in value):
            return []
        return [(f"{path}.{key}", child) for key, child in value.items()]
    if isinstance(value, list) and value:
        return [(f"{path}[{index}]", child) for index, child in enumerate(value)]
    return []


def _partition(value: Any, peers: set[str], path: str = "$", depth: int = 0) -> list[tuple[str, Any]]:
    """Split only ancestors of exact standalone bodies, retaining every sibling."""
    if path != "$" and encode_selected(value) in peers:
        return [(path, value)]
    if depth >= 8:
        return [(path, value)]
    children = _children(value, path)
    if not children or len(children) > 128:
        return [(path, value)]
    parts = [part for key, child in children for part in _partition(child, peers, key, depth + 1)]
    if len(parts) > 256 or not any(encode_selected(child) in peers for _, child in parts):
        return [(path, value)]
    return parts


def select_plan_input_reads(
    state_dir: Path, manifest: Mapping[str, Any], required: list[dict[str, Any]], *,
    policy: Mapping[str, Any], explicit: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Keep explicit policies intact; unknown or unbound sources remain required."""
    if explicit:
        return required, ""
    sources = [source for source in manifest.get("sources", []) if isinstance(source, Mapping)]
    source_map = {_identity(source): source for source in sources}
    if len(source_map) != len(sources):
        return required, ""
    bodies: dict[tuple, Any] = {}
    for key, source in source_map.items():
        if "$" not in source.get("allowed_paths", ["$"]):
            continue
        try:
            bodies[key] = _read(state_dir, source)
        except (ValueError, OSError, KeyError, TypeError):
            continue  # The normal reader still reports the original failure.
    full_rows = {(row.get("source_id"), row.get("artifact_id"), row.get("artifact_sha256"))
                 for row in required if row.get("json_path", "$") == "$"}
    peers = {encode_selected(body) for key, body in bodies.items() if key in full_rows}
    targeted = (policy.get("mode") == "targeted" and bool(policy.get("focus"))
                and not any(source.get("source_id") in {"plan-rework-package", "plan-rework-context"}
                            for source in sources))
    optional: set[tuple] = set()
    if targeted:
        # Use exact port ref + digest, never a filename match. Preserve unknown
        # or multiply declared ports and any source explicitly carrying authority.
        declarations: dict[tuple, set[str]] = {}
        core_refs: set[str] = set()
        index_owners: dict[str, set[tuple]] = {}
        for key, body in bodies.items():
            if not str(key[0]).startswith("child-result-") or not isinstance(body, dict):
                continue
            core_refs.update(body[field] for field in (
                "plan_artifact_ref", "plan_ref", "task_map_ref", "requirement_spec_ref",
            ) if isinstance(body.get(field), str) and body[field])
            if isinstance(body.get("source_index_ref"), str) and body["source_index_ref"]:
                index_owners.setdefault(body["source_index_ref"], set()).add(key)
            for port in body.get("plan_ports", []):
                if isinstance(port, dict) and port.get("ref") and port.get("sha256"):
                    declarations.setdefault((port["ref"], port["sha256"]), set()).add(port.get("logical_name", ""))
        for key, source in source_map.items():
            names = declarations.get((source.get("ref"), source.get("sha256")), set())
            index = bodies.get(key)
            index_ports = [(digest, port_names) for (ref, digest), port_names in declarations.items()
                           if ref == source.get("ref")]
            known_index = (len(index_owners.get(source.get("ref"), set())) == 1
                           and isinstance(index, dict) and index.get("schema_version") == "source-index.v1"
                           and all(digest == source.get("sha256") and port_names == {"source_index"}
                                   for digest, port_names in index_ports))
            if (key in bodies and str(key[0]).startswith("child-artifact-")
                    and source.get("ref") not in core_refs
                    and (known_index or len(names) == 1 and names <= SUPPLEMENTARY_PORTS)):
                optional.add(key)
    optional_text = {encode_selected(bodies[key]) for key in optional}
    result: list[dict[str, Any]] = []
    for row in required:
        key = row.get("source_id"), row.get("artifact_id"), row.get("artifact_sha256")
        if key in optional:
            continue
        source = source_map.get(key, {})
        body = bodies.get(key)
        # Only complete report/candidate wrappers are partitioned. Authority,
        # core Plan and explicit partial reads retain their original selection.
        wrapper = str(key[0]).startswith(("child-result-", "previous-plan-candidate-"))
        if (not wrapper or not isinstance(body, dict) or row.get("json_path", "$") != "$"
                or any(row.get(k) for k in ("max_chars", "max_items", "allow_truncated"))):
            result.append(row)
            continue
        other_peers = peers - {encode_selected(body)}
        parts = _partition(body, other_peers)
        for path, value in parts:
            if (targeted and (path == "$.source_index" or re.fullmatch(r"\$\.plan_ports\[\d+\]\.body", path))
                    and encode_selected(value) in optional_text):
                continue
            result.append({**row, "json_path": path,
                           "min_returned_bytes": 0 if value == "" else row.get("min_returned_bytes", 1)})
    if result == required:
        return required, ""
    return result, "targeted_navigation" if optional else "lossless_report_partition"


def complete_partition_read(
    state_dir: Path, source: Mapping[str, Any], required: list[dict[str, Any]],
    observed: list[dict[str, Any]],
) -> bool:
    """Prove complete required and delivered JSON coverage for a legacy root read.

    Does not equate a partial targeted review with full historical coverage.
    """
    from zf.runtime.artifact_read_ledger_validation import required_read_satisfied

    paths = {row.get("json_path", "$") for row in required
             if (row.get("source_id"), row.get("artifact_id"), row.get("artifact_sha256")) == _identity(source)
             and not any(row.get(k) for k in ("max_chars", "max_items", "allow_truncated"))
             and required_read_satisfied(observed, row)}
    if not paths:
        return False
    try:
        body = _read(state_dir, source)
    except (ValueError, OSError, KeyError, TypeError):
        return False

    def covered(value: Any, path: str, depth: int = 0) -> bool:
        if path in paths:
            return True
        children = _children(value, path)
        return bool(children) and depth < 8 and all(covered(child, key, depth + 1) for key, child in children)

    return covered(body, "$")
