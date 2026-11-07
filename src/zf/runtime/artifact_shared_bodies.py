"""Lossless invocation-local presentation; never an authorization/read cache."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from zf.runtime.artifact_read_ledger import _encode_selected


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def shared_body_records(results: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return postordered definitions and individually attributable source receipts.

    Replacing a value with null is only a transport placeholder: external refs
    contain exact list/dict paths, so literal nulls and reference-like keys survive.
    Consumers must read every definition, not merely source hashes/references.
    """
    results = list(results)
    values: list[Any] = []
    counts: Counter[str] = Counter()

    def count(value: Any) -> None:
        key = _compact(value)
        if len(key) > 200:
            counts[key] += 1
        if isinstance(value, dict):
            for child in value.values():
                count(child)
        elif isinstance(value, list):
            for child in value:
                count(child)

    for result in results:
        read = result["read"]
        if (read.get("status") != "read" or read.get("truncated")
                or read.get("max_items") or read.get("max_chars")
                or not re.fullmatch(r"[0-9a-f]{64}", str(read.get("artifact_sha256", "")))):
            raise ValueError("shared bodies require complete, authenticated reads")
        content = result["content"]
        try:
            value = json.loads(content)
            if _encode_selected(value) != content:
                value = content
        except json.JSONDecodeError:
            value = content
        values.append(value)
        count(value)

    records: list[dict[str, Any]] = []
    ids: dict[str, str] = {}

    def define(value: Any) -> str:
        key = _compact(value)
        if key in ids:
            return ids[key]
        refs: list[dict[str, Any]] = []

        def visit(child: Any, path: list[str | int]) -> Any:
            child_key = _compact(child)
            if path and len(child_key) > 200 and counts[child_key] > 1:
                refs.append({"path": path, "ref": define(child)})
                return None
            if isinstance(child, dict):
                return {k: visit(v, path + [k]) for k, v in child.items()}
            if isinstance(child, list):
                return [visit(v, path + [i]) for i, v in enumerate(child)]
            return child

        body = visit(value, [])
        identifier = f"body-{len(ids) + 1}"
        ids[key] = identifier
        records.append({"kind": "body", "id": identifier, "body": body, "refs": refs})
        return identifier

    sources = []
    for result, value in zip(results, values):
        read = result["read"]
        sources.append({
            "kind": "source", "schema_version": "artifact-shared-bodies.v1",
            **{k: read[k] for k in ("source_id", "artifact_id", "json_path",
                                   "artifact_ref", "artifact_sha256")},
            "body_ref": define(value),
            "content_sha256": hashlib.sha256(result["content"].encode("utf-8")).hexdigest(),
            "read_receipt": {
                "attempt_id": str(read.get("attempt_id") or ""),
                "occurrence_id": str(read.get("occurrence_id") or ""),
                "row_sha256": hashlib.sha256(json.dumps(
                    read, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")).hexdigest(),
            },
        })
    return records + sources
