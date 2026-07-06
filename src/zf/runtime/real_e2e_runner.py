"""Generic real-environment E2E matrix runner.

Project adapter skills decide which commands prove a surface. The runtime runner
only executes declared commands, captures evidence, and writes a result matrix.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class RealE2ERowResult:
    row_id: str
    surface: str
    status: str
    command: str
    exit_code: int | None = None
    evidence_ref: str = ""
    reason: str = ""


@dataclass(frozen=True)
class RealE2ERunnerResult:
    passed: bool
    result_matrix_ref: str
    evidence_refs: list[str] = field(default_factory=list)
    rows: list[RealE2ERowResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "real-e2e-runner.result.v1",
            "passed": self.passed,
            "result_matrix_ref": self.result_matrix_ref,
            "evidence_refs": list(self.evidence_refs),
            "rows": [asdict(row) for row in self.rows],
        }


def run_real_e2e_matrix(
    root: Path,
    config: Mapping[str, Any] | None = None,
) -> RealE2ERunnerResult:
    root = root.expanduser().resolve(strict=False)
    cfg = dict(config or {})
    evidence_dir = _safe_join(
        root,
        str(cfg.get("evidence_dir") or "artifacts/real-e2e"),
    )
    if evidence_dir is None:
        evidence_dir = root / "artifacts" / "real-e2e"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    timeout_seconds = _positive_int(cfg.get("timeout_seconds"), default=120)
    result_rows: list[dict[str, Any]] = []
    row_results: list[RealE2ERowResult] = []
    evidence_refs: list[str] = []
    refs = _refs(cfg, "real_e2e_matrix_paths", "real_e2e_matrix_refs", "e2e_matrix_paths")
    for rel in refs:
        path = _safe_join(root, rel)
        if path is None or not path.exists():
            row_results.append(RealE2ERowResult(
                row_id=rel,
                surface="",
                status="failed",
                command="",
                reason="matrix missing",
            ))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            row_results.append(RealE2ERowResult(
                row_id=rel,
                surface="",
                status="failed",
                command="",
                reason=f"invalid matrix json: {exc.msg}",
            ))
            continue
        for row in _iter_rows(data):
            updated, row_result = _run_row(
                root=root,
                evidence_dir=evidence_dir,
                row=row,
                timeout_seconds=timeout_seconds,
            )
            result_rows.append(updated)
            row_results.append(row_result)
            if row_result.evidence_ref:
                evidence_refs.append(row_result.evidence_ref)
    result_matrix = {
        "schema_version": "real-e2e-matrix.v1",
        "status": "passed" if row_results and all(row.status == "passed" for row in row_results) else "failed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "real-e2e-runner",
        "rows": result_rows,
    }
    result_matrix_ref = evidence_dir / "real-e2e-results.json"
    result_matrix_ref.write_text(
        json.dumps(result_matrix, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return RealE2ERunnerResult(
        passed=bool(row_results) and all(row.status == "passed" for row in row_results),
        result_matrix_ref=str(result_matrix_ref),
        evidence_refs=list(dict.fromkeys(evidence_refs)),
        rows=row_results,
    )


def _run_row(
    *,
    root: Path,
    evidence_dir: Path,
    row: Mapping[str, Any],
    timeout_seconds: int,
) -> tuple[dict[str, Any], RealE2ERowResult]:
    row_id = _row_id(row)
    surface = str(row.get("surface") or row.get("kind") or "").strip()
    command_value = row.get("command")
    command = _command_text(command_value)
    updated = dict(row)
    if not command:
        existing_status = str(row.get("status") or "").strip().lower()
        existing_evidence = _string_list(row.get("evidence_refs") or row.get("evidence_ref"))
        if existing_status in {"passed", "pass", "ok"} and existing_evidence:
            return updated, RealE2ERowResult(
                row_id=row_id,
                surface=surface,
                status="passed",
                command="",
                evidence_ref=existing_evidence[0],
            )
        updated["status"] = "failed"
        updated.setdefault("evidence_refs", [])
        return updated, RealE2ERowResult(
            row_id=row_id,
            surface=surface,
            status="failed",
            command="",
            reason="missing command or passing evidence",
        )
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        completed = subprocess.run(
            command_value if isinstance(command_value, list) else command,
            cwd=str(root),
            shell=not isinstance(command_value, list),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        exit_code = int(completed.returncode)
        stdout = completed.stdout[-8000:]
        stderr = completed.stderr[-8000:]
        reason = "" if exit_code == 0 else f"command exited {exit_code}"
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = str(exc.stdout or "")[-8000:]
        stderr = str(exc.stderr or "")[-8000:]
        reason = f"command timed out after {timeout_seconds}s"
    evidence_ref = evidence_dir / f"{_safe_filename(row_id)}.json"
    evidence = {
        "schema_version": "real-e2e-evidence.v1",
        "row_id": row_id,
        "surface": surface,
        "command": command,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "exit_code": exit_code,
        "stdout_tail": stdout,
        "stderr_tail": stderr,
        "reason": reason,
    }
    evidence_ref.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    status = "passed" if exit_code == 0 else "failed"
    updated["status"] = status
    updated["evidence_refs"] = [str(evidence_ref)]
    return updated, RealE2ERowResult(
        row_id=row_id,
        surface=surface,
        status=status,
        command=command,
        exit_code=exit_code,
        evidence_ref=str(evidence_ref),
        reason=reason,
    )


def _iter_rows(data: Any) -> Iterable[dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            yield from _iter_rows(item)
        return
    if not isinstance(data, dict):
        return
    if _looks_like_row(data):
        yield dict(data)
    for key in ("rows", "items", "checks", "tests", "matrix"):
        value = data.get(key)
        if isinstance(value, list | dict):
            yield from _iter_rows(value)


def _refs(config: Mapping[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        values.extend(_string_list(config.get(key)))
    return list(dict.fromkeys(values))


def _row_id(row: Mapping[str, Any]) -> str:
    for key in ("id", "test_id", "capability_id", "surface", "name"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return "real-e2e-row"


def _looks_like_row(row: Mapping[str, Any]) -> bool:
    return any(key in row for key in ("id", "surface", "command", "evidence_refs", "status"))


def _command_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(shlex.quote(str(item)) for item in value)
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        out: list[str] = []
        for item in value:
            out.extend(_string_list(item))
        return out
    if isinstance(value, dict):
        text = str(value.get("path") or value.get("ref") or "")
        return [text] if text.strip() else []
    text = str(value).strip()
    return [text] if text else []


def _safe_join(root: Path, value: str) -> Path | None:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _safe_filename(value: str) -> str:
    return "-".join(
        chunk for chunk in "".join(
            ch.lower() if ch.isalnum() else "-"
            for ch in value
        ).split("-")
        if chunk
    ) or "real-e2e-row"


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


__all__ = [
    "RealE2ERowResult",
    "RealE2ERunnerResult",
    "run_real_e2e_matrix",
]
