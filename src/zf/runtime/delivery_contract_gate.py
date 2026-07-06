"""Generic delivery contract gates for one-run workflows.

The gate validates artifact relationships, not project-specific semantics.
Project adapter skills decide what capabilities and tests mean; runtime only
checks that the declared chain is complete and evidenced.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class DeliveryContractFinding:
    code: str
    message: str
    severity: str = "high"
    path: str = ""
    row_id: str = ""


@dataclass(frozen=True)
class DeliveryContractGateResult:
    passed: bool
    findings: list[DeliveryContractFinding] = field(default_factory=list)
    checked_artifacts: list[str] = field(default_factory=list)
    checked_rows: int = 0
    blocking_capabilities: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": [asdict(item) for item in self.findings],
            "checked_artifacts": list(self.checked_artifacts),
            "checked_rows": self.checked_rows,
            "blocking_capabilities": self.blocking_capabilities,
        }


def evaluate_delivery_contract(
    root: Path,
    config: Mapping[str, Any] | None = None,
) -> DeliveryContractGateResult:
    root = root.expanduser().resolve(strict=False)
    cfg = dict(config or {})
    findings: list[DeliveryContractFinding] = []
    checked: list[str] = []
    capabilities = _load_rows(
        root,
        _refs(cfg, "capability_matrix_paths", "capability_matrix_refs", "capability_paths"),
        label="capability matrix",
        code_prefix="capability_matrix",
        checked=checked,
        findings=findings,
    )
    acceptances = _load_rows(
        root,
        _refs(cfg, "acceptance_matrix_paths", "acceptance_matrix_refs", "acceptance_paths"),
        label="acceptance matrix",
        code_prefix="acceptance_matrix",
        checked=checked,
        findings=findings,
    )
    tests = _load_rows(
        root,
        _refs(cfg, "test_matrix_paths", "test_matrix_refs", "test_paths"),
        label="test matrix",
        code_prefix="test_matrix",
        checked=checked,
        findings=findings,
    )
    tasks = _load_rows(
        root,
        _refs(cfg, "task_map_paths", "task_map_refs", "task_paths"),
        label="task map",
        code_prefix="task_map",
        checked=checked,
        findings=findings,
    )
    blocking_priorities = _priority_set(
        cfg.get("blocking_priorities")
        or cfg.get("blocking_priority")
        or ["p0", "p1"]
    )
    priority_field = str(cfg.get("priority_field") or "priority")
    capability_rows = [
        row for row in capabilities
        if _is_blocking(row, priority_field=priority_field, blocking_priorities=blocking_priorities)
    ]
    task_by_cap = _index_rows_by_refs(tasks, (
        "capability_id",
        "capability_ids",
        "capability",
        "capabilities",
        "source_capability_id",
        "source_capability_ids",
    ))
    acceptance_by_cap = _index_rows_by_refs(acceptances, (
        "capability_id",
        "capability_ids",
        "capability",
        "capabilities",
    ))
    test_by_cap = _index_rows_by_refs(tests, (
        "capability_id",
        "capability_ids",
        "capability",
        "capabilities",
    ))
    test_by_acceptance = _index_rows_by_refs(tests, (
        "acceptance_id",
        "acceptance_ids",
        "acceptance",
        "acceptances",
    ))
    checked_rows = len(capabilities) + len(acceptances) + len(tests) + len(tasks)
    for cap in capability_rows:
        cap_id = _row_id(cap)
        if not cap_id:
            findings.append(DeliveryContractFinding(
                code="delivery_capability_id_missing",
                message="blocking capability row is missing id/capability",
            ))
            continue
        mapped_tasks = task_by_cap.get(cap_id, [])
        if not mapped_tasks:
            findings.append(DeliveryContractFinding(
                code="delivery_capability_missing_task",
                row_id=cap_id,
                message=f"capability {cap_id!r} is not mapped to any task",
            ))
        elif not any(_task_has_lane(row) for row in mapped_tasks):
            findings.append(DeliveryContractFinding(
                code="delivery_task_missing_lane",
                row_id=cap_id,
                message=f"capability {cap_id!r} task mapping lacks lane/role ownership",
            ))
        mapped_acceptances = acceptance_by_cap.get(cap_id, [])
        if not mapped_acceptances:
            findings.append(DeliveryContractFinding(
                code="delivery_capability_missing_acceptance",
                row_id=cap_id,
                message=f"capability {cap_id!r} is not mapped to any acceptance row",
            ))
        has_direct_test = bool(test_by_cap.get(cap_id))
        has_acceptance_test = any(
            bool(test_by_acceptance.get(_row_id(acceptance)))
            for acceptance in mapped_acceptances
        )
        if not has_direct_test and not has_acceptance_test:
            findings.append(DeliveryContractFinding(
                code="delivery_capability_missing_test",
                row_id=cap_id,
                message=f"capability {cap_id!r} is not mapped to any test row",
            ))
    if capabilities and blocking_priorities and not capability_rows:
        findings.append(DeliveryContractFinding(
            code="delivery_no_blocking_capabilities",
            message=f"no capabilities matched blocking priorities {sorted(blocking_priorities)}",
        ))
    return DeliveryContractGateResult(
        passed=not findings,
        findings=findings,
        checked_artifacts=checked,
        checked_rows=checked_rows,
        blocking_capabilities=len(capability_rows),
    )


def evaluate_real_e2e_matrix(
    root: Path,
    config: Mapping[str, Any] | None = None,
) -> DeliveryContractGateResult:
    root = root.expanduser().resolve(strict=False)
    cfg = dict(config or {})
    if _truthy(cfg.get("execute_real_e2e") or cfg.get("run_real_e2e")):
        try:
            from zf.runtime.real_e2e_runner import run_real_e2e_matrix

            runner_result = run_real_e2e_matrix(root, cfg)
            cfg = {
                **cfg,
                "real_e2e_matrix_paths": [runner_result.result_matrix_ref],
            }
        except Exception as exc:
            return DeliveryContractGateResult(
                passed=False,
                findings=[DeliveryContractFinding(
                    code="real_e2e_runner_failed",
                    message=f"real E2E runner failed: {exc}",
                )],
            )
    findings: list[DeliveryContractFinding] = []
    checked: list[str] = []
    rows = _load_rows(
        root,
        _refs(cfg, "real_e2e_matrix_paths", "real_e2e_matrix_refs", "e2e_matrix_paths"),
        label="real E2E matrix",
        code_prefix="real_e2e_matrix",
        checked=checked,
        findings=findings,
    )
    required_surfaces = _string_list(
        cfg.get("required_surfaces")
        or cfg.get("required_real_e2e_surfaces")
        or []
    )
    allowed_statuses = {
        item.lower()
        for item in (_string_list(cfg.get("allowed_statuses")) or ["passed", "pass", "ok"])
    }
    by_surface: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        surface = str(row.get("surface") or row.get("kind") or row.get("id") or "").strip()
        if surface:
            by_surface.setdefault(surface, []).append(row)
        status = str(row.get("status") or "").strip().lower()
        if status not in allowed_statuses:
            findings.append(DeliveryContractFinding(
                code="real_e2e_status_not_allowed",
                row_id=_row_id(row) or surface,
                message=(
                    f"real E2E row {_row_id(row) or surface!r} has status "
                    f"{status or '<missing>'!r}; allowed: {sorted(allowed_statuses)}"
                ),
            ))
        if _field_missing(row.get("evidence_refs")) and _field_missing(row.get("evidence_ref")):
            findings.append(DeliveryContractFinding(
                code="real_e2e_evidence_missing",
                row_id=_row_id(row) or surface,
                message=f"real E2E row {_row_id(row) or surface!r} lacks evidence refs",
            ))
    for surface in required_surfaces:
        if surface not in by_surface:
            findings.append(DeliveryContractFinding(
                code="real_e2e_required_surface_missing",
                row_id=surface,
                message=f"required real E2E surface {surface!r} is missing",
            ))
    return DeliveryContractGateResult(
        passed=not findings,
        findings=findings,
        checked_artifacts=checked,
        checked_rows=len(rows),
        blocking_capabilities=0,
    )


def _load_rows(
    root: Path,
    refs: list[str],
    *,
    label: str,
    code_prefix: str,
    checked: list[str],
    findings: list[DeliveryContractFinding],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rel in refs:
        checked.append(rel)
        path = _safe_join(root, rel)
        if path is None or not path.exists():
            findings.append(DeliveryContractFinding(
                code=f"{code_prefix}_missing",
                path=rel,
                message=f"{label} {rel!r} is missing",
            ))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            findings.append(DeliveryContractFinding(
                code=f"{code_prefix}_invalid_json",
                path=rel,
                message=f"{label} {rel!r} is invalid JSON: {exc.msg}",
            ))
            continue
        rows.extend(_iter_rows(data))
    return rows


def _iter_rows(data: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            out.extend(_iter_rows(item))
        return out
    if not isinstance(data, dict):
        return out
    for key in ("rows", "items", "capabilities", "acceptance", "acceptances", "tests", "tasks", "matrix"):
        value = data.get(key)
        if isinstance(value, list | dict):
            out.extend(_iter_rows(value))
    if out:
        return out
    if _looks_like_row(data):
        out.append(data)
    return out


def _index_rows_by_refs(
    rows: list[dict[str, Any]],
    fields: tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for ref in _refs_from_row(row, fields):
            out.setdefault(ref, []).append(row)
    return out


def _refs_from_row(row: Mapping[str, Any], fields: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for field in fields:
        raw = row.get(field)
        if isinstance(raw, list | tuple | set):
            values.extend(str(item).strip() for item in raw if str(item).strip())
        elif raw not in (None, ""):
            values.append(str(raw).strip())
    return list(dict.fromkeys(values))


def _refs(config: Mapping[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        values.extend(_string_list(config.get(key)))
    return list(dict.fromkeys(values))


def _task_has_lane(row: Mapping[str, Any]) -> bool:
    return any(
        not _field_missing(row.get(field))
        for field in ("affinity_tag", "lane_id", "lane", "owner_role", "role", "role_instance")
    )


def _is_blocking(
    row: Mapping[str, Any],
    *,
    priority_field: str,
    blocking_priorities: set[str],
) -> bool:
    if not blocking_priorities:
        return True
    return str(row.get(priority_field) or "").strip().lower() in blocking_priorities


def _priority_set(value: Any) -> set[str]:
    return {item.strip().lower() for item in _string_list(value) if item.strip()}


def _row_id(row: Mapping[str, Any]) -> str:
    for key in ("id", "capability_id", "capability", "test_id", "acceptance_id", "name"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _looks_like_row(data: Mapping[str, Any]) -> bool:
    return any(
        key in data
        for key in (
            "id",
            "capability_id",
            "capability",
            "priority",
            "status",
            "surface",
            "task_id",
            "acceptance_id",
            "test_id",
        )
    )


def _field_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | tuple | set | dict):
        return not bool(value)
    return False


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _safe_join(root: Path, path: str) -> Path | None:
    if not str(path or "").strip():
        return None
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root.resolve(strict=False))
    except ValueError:
        return None
    return resolved


__all__ = [
    "DeliveryContractFinding",
    "DeliveryContractGateResult",
    "evaluate_delivery_contract",
    "evaluate_real_e2e_matrix",
]
