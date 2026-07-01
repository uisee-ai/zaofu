"""Generic artifact and matrix gate evaluation.

The evaluator is intentionally project-agnostic. Project-specific semantics
such as "which P0 capabilities matter" live in skills, prompts, or zf.yaml
criterion config.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class ArtifactMatrixFinding:
    code: str
    message: str
    severity: str = "high"
    path: str = ""
    row_id: str = ""


@dataclass(frozen=True)
class ArtifactMatrixGateResult:
    passed: bool
    findings: list[ArtifactMatrixFinding] = field(default_factory=list)
    checked_artifacts: list[str] = field(default_factory=list)
    checked_matrices: list[str] = field(default_factory=list)
    checked_rows: int = 0
    blocking_rows: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": [asdict(item) for item in self.findings],
            "checked_artifacts": list(self.checked_artifacts),
            "checked_matrices": list(self.checked_matrices),
            "checked_rows": self.checked_rows,
            "blocking_rows": self.blocking_rows,
        }


def evaluate_artifact_matrix_gate(
    root: Path,
    config: Mapping[str, Any] | None = None,
) -> ArtifactMatrixGateResult:
    """Evaluate required artifacts, matrix rows, and forbidden text.

    Supported config keys are deliberately generic:

    - ``config_ref``: optional JSON file under ``root``; merged before inline
      config, with inline values taking precedence.
    - ``required_artifacts``: paths that must exist under ``root``.
    - ``matrix_paths``: JSON files to flatten into row dictionaries.
    - ``blocking_priority`` or ``blocking_priorities``: priority values to
      validate. Empty means validate every row.
    - ``status_field`` / ``priority_field``: row field names.
    - ``allowed_statuses``: terminal statuses for blocking rows.
    - ``required_row_fields``: fields required on blocking rows.
    - ``forbidden_text``: list of ``{"path": "...", "contains": "..."}``.
    - ``module_parity_report_paths``: JSON module parity reports to validate.
    - ``gap_task_map_paths``: module-gap-plan JSON files to validate.
    - ``goal_gap_report_paths``: generic goal gap reports to validate.
    - ``goal_gap_task_map_paths``: goal-gap-plan JSON files to validate.
    - ``inventory_coverage``: generic source-inventory to matrix coverage
      rules. Project skills define inventory content; this gate only checks
      that required inventory item ids are represented by blocking matrix rows.
    """

    root = root.resolve()
    findings: list[ArtifactMatrixFinding] = []
    cfg = _load_config(root, dict(config or {}), findings)
    checked_artifacts: list[str] = []
    checked_matrices: list[str] = []

    for rel in _string_list(cfg.get("required_artifacts")):
        checked_artifacts.append(rel)
        path = _safe_join(root, rel)
        if path is None or not path.exists():
            findings.append(ArtifactMatrixFinding(
                code="required_artifact_missing",
                path=rel,
                message=f"required artifact {rel!r} is missing",
            ))

    priority_field = str(cfg.get("priority_field") or "priority")
    status_field = str(cfg.get("status_field") or "status")
    required_fields = _string_list(cfg.get("required_row_fields"))
    allowed_statuses = {
        str(item).strip().lower()
        for item in _string_list(cfg.get("allowed_statuses"))
        if str(item).strip()
    }
    blocking_priorities = _blocking_priorities(cfg)

    checked_rows = 0
    blocking_rows = 0
    for rel in _string_list(cfg.get("matrix_paths")):
        checked_matrices.append(rel)
        path = _safe_join(root, rel)
        if path is None or not path.exists():
            findings.append(ArtifactMatrixFinding(
                code="matrix_missing",
                path=rel,
                message=f"matrix {rel!r} is missing",
            ))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            findings.append(ArtifactMatrixFinding(
                code="matrix_invalid_json",
                path=rel,
                message=f"matrix {rel!r} is invalid JSON: {exc.msg}",
            ))
            continue
        rows = list(_iter_matrix_rows(data))
        if not rows:
            findings.append(ArtifactMatrixFinding(
                code="matrix_empty",
                path=rel,
                message=f"matrix {rel!r} contains no rows",
            ))
            continue
        checked_rows += len(rows)
        for row in rows:
            if not _is_blocking_row(row, priority_field, blocking_priorities):
                continue
            blocking_rows += 1
            row_id = _row_id(row)
            for field_name in required_fields:
                if _field_missing(row.get(field_name)):
                    findings.append(ArtifactMatrixFinding(
                        code="matrix_row_required_field_missing",
                        path=rel,
                        row_id=row_id,
                        message=f"blocking row {row_id!r} is missing field {field_name!r}",
                    ))
            if allowed_statuses:
                status = str(row.get(status_field) or "").strip().lower()
                if status not in allowed_statuses:
                    findings.append(ArtifactMatrixFinding(
                        code="matrix_row_status_not_allowed",
                        path=rel,
                        row_id=row_id,
                        message=(
                            f"blocking row {row_id!r} has status "
                            f"{status or '<missing>'!r}; allowed: {sorted(allowed_statuses)}"
                        ),
                    ))

    if checked_matrices and blocking_priorities and blocking_rows == 0:
        findings.append(ArtifactMatrixFinding(
            code="matrix_no_blocking_rows",
            message=f"no rows matched blocking priorities {sorted(blocking_priorities)}",
        ))

    _evaluate_inventory_coverage(
        root=root,
        config=cfg,
        checked_artifacts=checked_artifacts,
        checked_matrices=checked_matrices,
        findings=findings,
    )

    for item in _forbidden_text_items(cfg.get("forbidden_text")):
        rel = item.get("path", "")
        needle = item.get("contains", "")
        path = _safe_join(root, rel)
        if not rel or not needle or path is None or not path.exists():
            continue
        if needle in path.read_text(encoding="utf-8", errors="ignore"):
            reason = item.get("reason") or f"forbidden text remains in {rel!r}"
            findings.append(ArtifactMatrixFinding(
                code="forbidden_text_present",
                path=rel,
                message=str(reason),
            ))

    for rel in _string_list(
        cfg.get("module_parity_report_paths") or cfg.get("module_parity_reports")
    ):
        checked_artifacts.append(rel)
        path = _safe_join(root, rel)
        if path is None or not path.exists():
            findings.append(ArtifactMatrixFinding(
                code="module_parity_report_missing",
                path=rel,
                message=f"module parity report {rel!r} is missing",
            ))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            findings.append(ArtifactMatrixFinding(
                code="module_parity_report_invalid_json",
                path=rel,
                message=f"module parity report {rel!r} is invalid JSON: {exc.msg}",
            ))
            continue
        if not isinstance(data, dict):
            findings.append(ArtifactMatrixFinding(
                code="module_parity_report_not_object",
                path=rel,
                message=f"module parity report {rel!r} must be a JSON object",
            ))
            continue
        _evaluate_module_parity_report(
            data,
            path=rel,
            config=cfg,
            findings=findings,
        )

    _evaluate_gap_task_map_paths(
        root=root,
        refs=_string_list(cfg.get("gap_task_map_paths") or cfg.get("gap_task_maps")),
        checked_artifacts=checked_artifacts,
        findings=findings,
        code_prefix="gap_task_map",
        label="gap task map",
    )
    _evaluate_gap_task_map_paths(
        root=root,
        refs=_string_list(cfg.get("goal_gap_task_map_paths") or cfg.get("goal_gap_task_maps")),
        checked_artifacts=checked_artifacts,
        findings=findings,
        code_prefix="goal_gap_task_map",
        label="goal gap task map",
    )
    for rel in _string_list(cfg.get("goal_gap_report_paths") or cfg.get("goal_gap_reports")):
        checked_artifacts.append(rel)
        path = _safe_join(root, rel)
        if path is None or not path.exists():
            findings.append(ArtifactMatrixFinding(
                code="goal_gap_report_missing",
                path=rel,
                message=f"goal gap report {rel!r} is missing",
            ))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            findings.append(ArtifactMatrixFinding(
                code="goal_gap_report_invalid_json",
                path=rel,
                message=f"goal gap report {rel!r} is invalid JSON: {exc.msg}",
            ))
            continue
        if not isinstance(data, dict):
            findings.append(ArtifactMatrixFinding(
                code="goal_gap_report_not_object",
                path=rel,
                message=f"goal gap report {rel!r} must be a JSON object",
            ))
            continue
        _evaluate_goal_gap_report(data, path=rel, config=cfg, findings=findings)

    return ArtifactMatrixGateResult(
        passed=not findings,
        findings=findings,
        checked_artifacts=checked_artifacts,
        checked_matrices=checked_matrices,
        checked_rows=checked_rows,
        blocking_rows=blocking_rows,
    )


def _load_config(
    root: Path,
    inline: dict[str, Any],
    findings: list[ArtifactMatrixFinding],
) -> dict[str, Any]:
    ref = str(inline.get("config_ref") or inline.get("gate_config_ref") or "").strip()
    if not ref:
        return inline
    path = _safe_join(root, ref)
    if path is None or not path.exists():
        findings.append(ArtifactMatrixFinding(
            code="gate_config_missing",
            path=ref,
            message=f"gate config {ref!r} is missing",
        ))
        return inline
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        findings.append(ArtifactMatrixFinding(
            code="gate_config_invalid_json",
            path=ref,
            message=f"gate config {ref!r} is invalid JSON: {exc.msg}",
        ))
        return inline
    if not isinstance(data, dict):
        findings.append(ArtifactMatrixFinding(
            code="gate_config_not_object",
            path=ref,
            message=f"gate config {ref!r} must be a JSON object",
        ))
        return inline
    merged = dict(data)
    merged.update({key: value for key, value in inline.items() if key not in {"path"}})
    return merged


_MODULE_PARITY_REQUIRED_FIELDS = (
    "module_id",
    "parent_task_id",
    "affinity_tag",
    "lane_id",
    "hermes_original_paths",
    "cangjie_target_paths",
    "capability_rows",
    "test_rows",
    "runtime_evidence_refs",
    "gap_tasks",
    "open_p0_p1_gap_count",
)


def _evaluate_module_parity_report(
    report: Mapping[str, Any],
    *,
    path: str,
    config: Mapping[str, Any],
    findings: list[ArtifactMatrixFinding],
) -> None:
    required_fields = (
        _string_list(config.get("module_parity_required_fields"))
        or list(_MODULE_PARITY_REQUIRED_FIELDS)
    )
    module_id = str(report.get("module_id") or "").strip()
    row_id = module_id or _row_id(report)
    for field_name in required_fields:
        if _field_missing(report.get(field_name)):
            findings.append(ArtifactMatrixFinding(
                code="module_parity_required_field_missing",
                path=path,
                row_id=row_id,
                message=f"module parity report {row_id!r} is missing field {field_name!r}",
            ))
    if _truthy(config.get("require_no_open_p0_p1_gaps"), default=True):
        count = _int_value(report.get("open_p0_p1_gap_count"))
        if count is not None and count > 0:
            findings.append(ArtifactMatrixFinding(
                code="module_parity_open_gaps",
                path=path,
                row_id=row_id,
                message=f"module parity report {row_id!r} has {count} open P0/P1 gaps",
            ))
    runtime_modules = {
        item.strip().lower()
        for item in _string_list(config.get("runtime_path_evidence_modules"))
        if item.strip()
    }
    if runtime_modules and module_id.lower() in runtime_modules:
        if _field_missing(report.get("runtime_evidence_refs")):
            findings.append(ArtifactMatrixFinding(
                code="module_parity_runtime_evidence_missing",
                path=path,
                row_id=row_id,
                message=f"module parity report {row_id!r} lacks runtime path evidence",
            ))


def _evaluate_goal_gap_report(
    report: Mapping[str, Any],
    *,
    path: str,
    config: Mapping[str, Any],
    findings: list[ArtifactMatrixFinding],
) -> None:
    row_id = _row_id(report)
    required_fields = _string_list(config.get("goal_gap_required_fields"))
    for field_name in required_fields:
        if _field_missing(report.get(field_name)):
            findings.append(ArtifactMatrixFinding(
                code="goal_gap_required_field_missing",
                path=path,
                row_id=row_id,
                message=f"goal gap report {row_id!r} is missing field {field_name!r}",
            ))
    if _truthy(config.get("require_no_open_p0_p1_gaps"), default=True):
        count = _int_value(report.get("open_p0_p1_gap_count"))
        if count is not None and count > 0:
            findings.append(ArtifactMatrixFinding(
                code="goal_gap_open_gaps",
                path=path,
                row_id=row_id,
                message=f"goal gap report {row_id!r} has {count} open P0/P1 gaps",
            ))


def _evaluate_gap_task_map_paths(
    *,
    root: Path,
    refs: list[str],
    checked_artifacts: list[str],
    findings: list[ArtifactMatrixFinding],
    code_prefix: str,
    label: str,
) -> None:
    for rel in refs:
        checked_artifacts.append(rel)
        path = _safe_join(root, rel)
        if path is None or not path.exists():
            findings.append(ArtifactMatrixFinding(
                code=f"{code_prefix}_missing",
                path=rel,
                message=f"{label} {rel!r} is missing",
            ))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            findings.append(ArtifactMatrixFinding(
                code=f"{code_prefix}_invalid_json",
                path=rel,
                message=f"{label} {rel!r} is invalid JSON: {exc.msg}",
            ))
            continue
        if not isinstance(data, dict):
            findings.append(ArtifactMatrixFinding(
                code=f"{code_prefix}_not_object",
                path=rel,
                message=f"{label} {rel!r} must be a JSON object",
            ))
            continue
        from zf.runtime.goal_gap_plan import validate_goal_gap_plan_payload

        validation = validate_goal_gap_plan_payload(data)
        if not validation.passed:
            findings.append(ArtifactMatrixFinding(
                code=f"{code_prefix}_invalid",
                path=rel,
                message="; ".join(validation.errors),
            ))


def _evaluate_inventory_coverage(
    *,
    root: Path,
    config: Mapping[str, Any],
    checked_artifacts: list[str],
    checked_matrices: list[str],
    findings: list[ArtifactMatrixFinding],
) -> None:
    raw_rules = config.get("inventory_coverage") or config.get("inventory_coverage_rules")
    if raw_rules in (None, "", []):
        return
    if isinstance(raw_rules, Mapping):
        rules: list[Any] = [raw_rules]
    elif isinstance(raw_rules, list):
        rules = raw_rules
    else:
        findings.append(ArtifactMatrixFinding(
            code="inventory_coverage_config_invalid",
            message="inventory_coverage must be an object or list of objects",
        ))
        return

    for index, raw_rule in enumerate(rules):
        if not isinstance(raw_rule, Mapping):
            findings.append(ArtifactMatrixFinding(
                code="inventory_coverage_rule_invalid",
                row_id=str(index),
                message="inventory coverage rule must be an object",
            ))
            continue
        rule = dict(raw_rule)
        inventory_refs = _string_list(
            rule.get("inventory_refs")
            or rule.get("inventory_paths")
            or rule.get("source_inventory_refs")
        )
        if not inventory_refs:
            findings.append(ArtifactMatrixFinding(
                code="inventory_coverage_inventory_refs_missing",
                row_id=str(index),
                message="inventory coverage rule is missing inventory_refs",
            ))
            continue
        matrix_refs = _string_list(
            rule.get("matrix_paths")
            or rule.get("coverage_matrix_paths")
            or config.get("matrix_paths")
        )
        if not matrix_refs:
            findings.append(ArtifactMatrixFinding(
                code="inventory_coverage_matrix_paths_missing",
                row_id=str(index),
                message="inventory coverage rule is missing matrix_paths",
            ))
            continue

        priority_field = str(
            rule.get("priority_field")
            or config.get("priority_field")
            or "priority"
        )
        inventory_id_field = str(rule.get("inventory_id_field") or "id")
        matrix_ref_fields = _string_list(
            rule.get("matrix_inventory_ref_fields")
            or rule.get("matrix_inventory_ref_field")
        ) or [
            "inventory_id",
            "inventory_ref",
            "source_inventory_id",
            "source_inventory_ref",
            "source_id",
            "id",
        ]
        required_priorities = (
            _priority_set(rule.get("required_priorities"))
            or _blocking_priorities(rule)
            or _blocking_priorities(config)
        )
        require_all = _truthy(
            rule.get("require_all_inventory_items_mapped"),
            default=True,
        )
        require_blocking_matrix = _truthy(
            rule.get("require_blocking_matrix_for_blocking_inventory"),
            default=True,
        )

        matrix_rows: list[dict[str, Any]] = []
        for rel in matrix_refs:
            if rel not in checked_matrices:
                checked_matrices.append(rel)
            matrix_rows.extend(_load_rows_from_ref(
                root=root,
                rel=rel,
                label="inventory coverage matrix",
                code_prefix="inventory_coverage_matrix",
                findings=findings,
            ))
        matrix_rows_by_inventory_id: dict[str, list[dict[str, Any]]] = {}
        for row in matrix_rows:
            for inventory_id in _matrix_inventory_refs(row, matrix_ref_fields):
                matrix_rows_by_inventory_id.setdefault(inventory_id, []).append(row)

        for rel in inventory_refs:
            checked_artifacts.append(rel)
            inventory_rows = _load_rows_from_ref(
                root=root,
                rel=rel,
                label="inventory",
                code_prefix="inventory",
                findings=findings,
            )
            if not inventory_rows:
                findings.append(ArtifactMatrixFinding(
                    code="inventory_empty",
                    path=rel,
                    message=f"inventory {rel!r} contains no rows",
                ))
                continue
            for item in inventory_rows:
                inventory_id = str(item.get(inventory_id_field) or "").strip()
                if not inventory_id:
                    findings.append(ArtifactMatrixFinding(
                        code="inventory_item_id_missing",
                        path=rel,
                        message=(
                            f"inventory item is missing id field "
                            f"{inventory_id_field!r}"
                        ),
                    ))
                    continue
                if required_priorities:
                    required = (
                        str(item.get(priority_field) or "").strip().lower()
                        in required_priorities
                    )
                else:
                    required = require_all
                if not required:
                    continue
                mapped_rows = matrix_rows_by_inventory_id.get(inventory_id) or []
                if not mapped_rows:
                    findings.append(ArtifactMatrixFinding(
                        code="inventory_item_unmapped",
                        path=rel,
                        row_id=inventory_id,
                        message=(
                            f"inventory item {inventory_id!r} is not mapped by "
                            "any coverage matrix row"
                        ),
                    ))
                    continue
                if (
                    require_blocking_matrix
                    and required_priorities
                    and required
                    and not any(
                        str(row.get(priority_field) or "").strip().lower()
                        in required_priorities
                        for row in mapped_rows
                    )
                ):
                    findings.append(ArtifactMatrixFinding(
                        code="inventory_item_not_blocking",
                        path=rel,
                        row_id=inventory_id,
                        message=(
                            f"inventory item {inventory_id!r} is mapped only by "
                            "non-blocking matrix rows"
                        ),
                    ))


def _load_rows_from_ref(
    *,
    root: Path,
    rel: str,
    label: str,
    code_prefix: str,
    findings: list[ArtifactMatrixFinding],
) -> list[dict[str, Any]]:
    path = _safe_join(root, rel)
    if path is None or not path.exists():
        findings.append(ArtifactMatrixFinding(
            code=f"{code_prefix}_missing",
            path=rel,
            message=f"{label} {rel!r} is missing",
        ))
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        findings.append(ArtifactMatrixFinding(
            code=f"{code_prefix}_invalid_json",
            path=rel,
            message=f"{label} {rel!r} is invalid JSON: {exc.msg}",
        ))
        return []
    rows = list(_iter_matrix_rows(data))
    return rows


def _iter_matrix_rows(data: Any) -> Iterable[dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            yield from _iter_matrix_rows(item)
        return
    if not isinstance(data, dict):
        return
    if _looks_like_row(data):
        yield data
    for key in ("rows", "items", "capabilities", "modules", "tests", "matrix"):
        value = data.get(key)
        if isinstance(value, list | dict):
            yield from _iter_matrix_rows(value)


def _looks_like_row(data: Mapping[str, Any]) -> bool:
    return any(key in data for key in ("priority", "status", "id", "capability", "test", "name"))


def _is_blocking_row(
    row: Mapping[str, Any],
    priority_field: str,
    blocking_priorities: set[str],
) -> bool:
    if not blocking_priorities:
        return True
    return str(row.get(priority_field) or "").strip().lower() in blocking_priorities


def _blocking_priorities(config: Mapping[str, Any]) -> set[str]:
    raw = config.get("blocking_priorities")
    if raw is None:
        raw = config.get("blocking_priority")
    return {item.strip().lower() for item in _string_list(raw) if item.strip()}


def _priority_set(value: Any) -> set[str]:
    return {item.strip().lower() for item in _string_list(value) if item.strip()}


def _matrix_inventory_refs(
    row: Mapping[str, Any],
    fields: list[str],
) -> list[str]:
    refs: list[str] = []
    for field_name in fields:
        raw = row.get(field_name)
        if isinstance(raw, list | tuple | set):
            refs.extend(str(item).strip() for item in raw if str(item).strip())
        elif raw not in (None, ""):
            refs.append(str(raw).strip())
    return list(dict.fromkeys(item for item in refs if item))


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _forbidden_text_items(value: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not isinstance(value, list):
        return items
    for item in value:
        if isinstance(item, dict):
            items.append({
                "path": str(item.get("path") or ""),
                "contains": str(item.get("contains") or item.get("text") or ""),
                "reason": str(item.get("reason") or ""),
            })
    return items


def _row_id(row: Mapping[str, Any]) -> str:
    for key in ("id", "capability", "test", "name"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return "<unknown>"


def _field_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | tuple | set | dict):
        return not bool(value)
    return False


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_value(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_join(root: Path, path: str) -> Path | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return candidate
