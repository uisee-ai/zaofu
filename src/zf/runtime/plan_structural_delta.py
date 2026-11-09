"""Lossless JSON differences for evidence-bound plan review experiments.

This module does not authorize narrower reads or approve any plan. Callers must
also establish source identity, prior review, and semantic scope independently.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def _bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def make_verified_delta(old_raw: bytes, new_raw: bytes, *, old_sha256: str, new_sha256: str) -> dict[str, Any]:
    """Bind semantic JSON replay to the exact two supplied source files."""
    if hashlib.sha256(old_raw).hexdigest() != old_sha256 or hashlib.sha256(new_raw).hexdigest() != new_sha256:
        raise ValueError("source file digest differs")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    old = json.loads(old_raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    new = json.loads(new_raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    delta = make_delta(old, new)
    if _bytes(replay_delta(old, delta)) != _bytes(new):
        raise ValueError("candidate could not be replayed")
    return {**delta, "old_file_sha256": old_sha256, "new_file_sha256": new_sha256}


def make_delta(old: Any, new: Any) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []

    def visit(before: Any, after: Any, path: list[str | int]) -> None:
        if _bytes(before) == _bytes(after):
            return
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(before.keys() | after.keys()):
                child = [*path, key]
                if key not in before:
                    changes.append({"op": "add", "path": child, "new": copy.deepcopy(after[key])})
                elif key not in after:
                    changes.append({"op": "remove", "path": child, "old": copy.deepcopy(before[key])})
                else:
                    visit(before[key], after[key], child)
        elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
            for index, (left, right) in enumerate(zip(before, after, strict=True)):
                visit(left, right, [*path, index])
        else:
            changes.append({"op": "replace", "path": path,
                            "old": copy.deepcopy(before), "new": copy.deepcopy(after)})

    visit(old, new, [])
    return {"schema_version": "plan-structural-delta.v1", "old_sha256": _digest(old),
            "new_sha256": _digest(new), "changes": changes}


def replay_delta(old: Any, delta: dict[str, Any]) -> Any:
    if delta.get("schema_version") != "plan-structural-delta.v1" or _digest(old) != delta.get("old_sha256"):
        raise ValueError("delta baseline differs")
    changes = delta.get("changes")
    if not isinstance(changes, list):
        raise ValueError("delta changes must be a list")
    result = copy.deepcopy(old)
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("path"), list):
            raise ValueError("invalid delta change")
        path, op = change["path"], change.get("op")
        if not path:
            if op != "replace" or "old" not in change or "new" not in change or _bytes(result) != _bytes(change["old"]):
                raise ValueError("invalid root replacement")
            result = copy.deepcopy(change["new"])
            continue
        parent = result
        try:
            for part in path[:-1]:
                if not ((isinstance(parent, dict) and type(part) is str) or
                        (isinstance(parent, list) and type(part) is int and 0 <= part < len(parent))):
                    raise ValueError("invalid delta path")
                parent = parent[part]
            key = path[-1]
            if isinstance(parent, dict) and type(key) is str:
                if op == "add" and key not in parent and "new" in change:
                    parent[key] = copy.deepcopy(change["new"])
                elif op in {"replace", "remove"} and key in parent and "old" in change and _bytes(parent[key]) == _bytes(change["old"]):
                    if op == "remove":
                        del parent[key]
                    elif "new" in change:
                        parent[key] = copy.deepcopy(change["new"])
                    else:
                        raise ValueError("replacement missing value")
                else:
                    raise ValueError("delta precondition differs")
            elif isinstance(parent, list) and type(key) is int and 0 <= key < len(parent) and op == "replace" and "old" in change and "new" in change and _bytes(parent[key]) == _bytes(change["old"]):
                parent[key] = copy.deepcopy(change["new"])
            else:
                raise ValueError("invalid delta path or operation")
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("invalid delta path") from exc
    if _digest(result) != delta.get("new_sha256"):
        raise ValueError("delta candidate differs")
    return result
