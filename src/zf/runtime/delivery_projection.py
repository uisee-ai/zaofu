"""Read-only delivery contract projections.

These projections expose the run contract, failure-to-eval candidates, and
real E2E matrix without making them a second control plane.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def project_run_contract(state_dir: Path | str) -> dict[str, Any]:
    state_path = Path(state_dir)
    ref = state_path / "config" / "run-contract.json"
    contract = _read_json(ref)
    return {
        "schema_version": "run-contract.projection.v1",
        "status": "present" if isinstance(contract, dict) else "missing",
        "run_contract_ref": str(ref),
        "contract": contract if isinstance(contract, dict) else {},
    }


def project_failure_candidates(state_dir: Path | str) -> dict[str, Any]:
    root = Path(state_dir) / "failure-candidates"
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        row = dict(data)
        row["candidate_ref"] = str(path)
        items.append(row)
    return {
        "schema_version": "failure-candidates.projection.v1",
        "status": "present" if items else "empty",
        "count": len(items),
        "items": items,
    }


def project_real_e2e_matrix(
    state_dir: Path | str,
    *,
    project_root: Path | str | None = None,
) -> dict[str, Any]:
    state_path = Path(state_dir)
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else state_path.parent
    contract = _read_json(state_path / "config" / "run-contract.json")
    refs = _matrix_refs(contract)
    if not refs:
        refs = [
            str(path)
            for path in sorted((state_path / "artifacts").glob("**/*real*e2e*matrix*.json"))
        ]
    matrices: list[dict[str, Any]] = []
    missing: list[str] = []
    for ref in refs:
        path = _resolve_ref(ref, project_root=root)
        data = _read_json(path)
        if not isinstance(data, dict):
            missing.append(ref)
            continue
        matrices.append({
            "matrix_ref": ref,
            "resolved_path": str(path),
            "status": str(data.get("status") or ""),
            "summary": _matrix_summary(data),
            "matrix": data,
        })
    status = "present" if matrices else "missing" if refs else "not_declared"
    return {
        "schema_version": "real-e2e-matrix.projection.v1",
        "status": status,
        "refs": refs,
        "missing_refs": missing,
        "matrices": matrices,
        "summary": {
            "declared": len(refs),
            "loaded": len(matrices),
            "missing": len(missing),
        },
    }


def _matrix_refs(contract: Any) -> list[str]:
    if not isinstance(contract, Mapping):
        return []
    refs = contract.get("refs")
    refs = refs if isinstance(refs, Mapping) else {}
    raw = refs.get("real_e2e_matrix")
    if isinstance(raw, list | tuple | set):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _matrix_summary(data: Mapping[str, Any]) -> dict[str, Any]:
    cases = data.get("cases")
    checks = data.get("checks")
    items = data.get("items")
    rows = data.get("rows")
    count = 0
    if isinstance(cases, list):
        count = len(cases)
    elif isinstance(checks, list):
        count = len(checks)
    elif isinstance(items, list):
        count = len(items)
    elif isinstance(rows, list):
        count = len(rows)
    return {
        "case_count": count,
        "status": str(data.get("status") or ""),
        "schema_version": str(data.get("schema_version") or ""),
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _resolve_ref(ref: str, *, project_root: Path) -> Path:
    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve(strict=False)
