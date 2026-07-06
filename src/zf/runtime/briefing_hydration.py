"""Run-contract hydration helpers for worker instructions.

The run contract is the stable launch envelope. This module renders that
envelope into role instructions and writes a read-only projection proving the
refs were included. It does not mutate task truth.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def render_run_contract_context(
    state_dir: Path | str,
    *,
    project_root: Path | str | None = None,
) -> str:
    contract = _load_run_contract(Path(state_dir))
    if not contract:
        return ""
    lines = [
        "## Run Contract Context",
        "",
        "This run has a deterministic launch contract. Treat these refs as "
        "handoff context, not as editable control-plane truth.",
        "",
        f"- `run_contract_ref`: `{Path(state_dir) / 'config' / 'run-contract.json'}`",
    ]
    digest = str(contract.get("contract_digest") or "").strip()
    if digest:
        lines.append(f"- `contract_digest`: `{digest}`")
    workflow = contract.get("workflow")
    if isinstance(workflow, Mapping):
        summary = {
            key: str(workflow.get(key) or "")
            for key in (
                "kind",
                "schema_profile",
                "quality_floor",
                "strictness",
                "gap_loop",
                "post_verify_discovery",
                "completion_threshold",
            )
            if str(workflow.get(key) or "").strip()
        }
        if summary:
            lines.append("- `workflow`: `" + json.dumps(summary, ensure_ascii=False, sort_keys=True) + "`")
    refs = contract.get("refs")
    refs = refs if isinstance(refs, Mapping) else {}
    if refs:
        lines.append("")
        lines.append("### Delivery Artifact Refs")
        for group in sorted(refs):
            values = _string_list(refs.get(group))
            if not values:
                continue
            lines.append(f"- `{group}`:")
            for value in values:
                lines.append(f"  - `{_display_ref(value, project_root=project_root)}`")
    required = contract.get("required_delivery_artifacts")
    if isinstance(required, list) and required:
        lines.append("")
        lines.append("### Required Delivery Artifacts")
        for item in required:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            requirement = str(item.get("required_for") or "").strip()
            if name:
                suffix = f" ({requirement})" if requirement else ""
                lines.append(f"- `{name}`{suffix}")
    lines.append("")
    return "\n".join(lines)


def build_briefing_hydration_report(
    state_dir: Path | str,
    *,
    instructions_dir: Path | str | None = None,
) -> dict[str, Any]:
    state_path = Path(state_dir)
    contract = _load_run_contract(state_path)
    instructions_path = Path(instructions_dir) if instructions_dir is not None else state_path / "instructions"
    report: dict[str, Any] = {
        "schema_version": "briefing-hydration-report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS",
        "run_contract_ref": str(state_path / "config" / "run-contract.json"),
        "instructions_dir": str(instructions_path),
        "diagnostics": [],
        "roles": [],
    }
    if not contract:
        report["status"] = "WARN"
        report["diagnostics"].append({
            "severity": "WARN",
            "kind": "run_contract_missing",
            "message": "run-contract.json is not available for briefing hydration",
        })
        return report
    refs = contract.get("refs")
    refs = refs if isinstance(refs, Mapping) else {}
    missing_required_groups = _missing_required_ref_groups(contract, refs)
    if missing_required_groups:
        report["status"] = "WARN"
        report["diagnostics"].append({
            "severity": "WARN",
            "kind": "run_contract_required_refs_missing",
            "missing_groups": missing_required_groups,
            "message": "run contract required delivery artifact groups have no refs",
        })
    expected_refs = {
        ref
        for values in refs.values()
        for ref in _string_list(values)
    }
    if not instructions_path.exists():
        report["status"] = "WARN"
        report["diagnostics"].append({
            "severity": "WARN",
            "kind": "instructions_dir_missing",
            "message": "instructions directory is not available yet",
        })
        return report
    for path in sorted(instructions_path.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        missing = [ref for ref in sorted(expected_refs) if ref and ref not in text]
        role_status = "PASS" if "## Run Contract Context" in text and not missing else "WARN"
        if role_status != "PASS":
            report["status"] = "WARN"
            report["diagnostics"].append({
                "severity": "WARN",
                "kind": "briefing_hydration_incomplete",
                "role_ref": str(path),
                "missing_refs": missing[:20],
            })
        report["roles"].append({
            "role_ref": str(path),
            "status": role_status,
            "missing_ref_count": len(missing),
            "has_run_contract_context": "## Run Contract Context" in text,
        })
    return report


def evaluate_instruction_hydration(
    state_dir: Path | str,
    instructions: str,
) -> dict[str, Any]:
    state_path = Path(state_dir)
    contract = _load_run_contract(state_path)
    report: dict[str, Any] = {
        "schema_version": "briefing-hydration-instruction.v1",
        "status": "PASS",
        "strict": False,
        "missing_refs": [],
        "has_run_contract_context": "## Run Contract Context" in instructions,
    }
    if not contract:
        report["status"] = "WARN"
        report["missing_refs"] = ["config/run-contract.json"]
        return report
    try:
        from zf.runtime.run_contract import is_strict_run_contract

        strict = is_strict_run_contract(contract)
    except Exception:
        strict = False
    refs = contract.get("refs")
    refs = refs if isinstance(refs, Mapping) else {}
    missing_required_groups = _missing_required_ref_groups(contract, refs)
    expected_refs = [
        ref
        for values in refs.values()
        for ref in _string_list(values)
        if ref
    ]
    missing = [ref for ref in sorted(set(expected_refs)) if ref not in instructions]
    if missing or missing_required_groups or "## Run Contract Context" not in instructions:
        report["status"] = "STOP" if strict else "WARN"
    report["strict"] = strict
    report["missing_refs"] = missing
    report["missing_required_groups"] = missing_required_groups
    return report


def write_briefing_hydration_report(
    state_dir: Path | str,
    report: Mapping[str, Any],
) -> Path:
    path = Path(state_dir) / "projections" / "briefing-hydration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _load_run_contract(state_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((state_dir / "config" / "run-contract.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _missing_required_ref_groups(
    contract: Mapping[str, Any],
    refs: Mapping[str, Any],
) -> list[str]:
    required = contract.get("required_delivery_artifacts")
    if not isinstance(required, list):
        return []
    known_groups = set(refs.keys())
    missing: list[str] = []
    for item in required:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name not in known_groups:
            continue
        if not _string_list(refs.get(name)):
            missing.append(name)
    return missing


def _display_ref(value: str, *, project_root: Path | str | None) -> str:
    if project_root is None:
        return value
    root = Path(project_root).expanduser().resolve(strict=False)
    path = Path(value).expanduser()
    if path.is_absolute():
        try:
            return str(path.resolve(strict=False).relative_to(root))
        except ValueError:
            return value
    return value
