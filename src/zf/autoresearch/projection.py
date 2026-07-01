"""Read-only Web/API projection for autoresearch state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.autoresearch.experiment_graph import project_experiment_graph
from zf.autoresearch.holdout import holdout_projection
from zf.autoresearch.loop_requests import project_loop_requests
from zf.autoresearch.review_gate_context import FATAL_EVENT_TYPES, HIGH_SIGNAL_EVENT_TYPES
from zf.autoresearch.triggers import read_trigger_decisions
from zf.core.events.log import EventLog


def _read_jsonl(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows[-limit:]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _source(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "mtime": path.stat().st_mtime,
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if item not in (None, "")
    }


def _run_dirs(state_dir: Path) -> list[Path]:
    root = state_dir / "autoresearch" / "runs"
    if not root.exists():
        return []
    return [path for path in sorted(root.iterdir()) if path.is_dir()]


def _review_gate_record(run_dir: Path, *, source_kind: str) -> dict[str, Any]:
    gate_dir = run_dir / "review-gate"
    summary_path = gate_dir / "summary.json"
    closeout_path = gate_dir / "closeout.json"
    summary = _read_json(summary_path)
    closeout = _read_json(closeout_path)
    if not summary and not closeout:
        return {}
    refs = _string_map(summary.get("artifact_refs"))
    if summary_path.exists() and "summary" not in refs:
        refs["summary"] = str(summary_path)
    failure_pack_ref = refs.get("failure_evidence_pack", "")
    failure_pack = _read_json(Path(failure_pack_ref)) if failure_pack_ref else {}
    closeout_result = closeout.get("result") if isinstance(closeout.get("result"), dict) else {}
    return {
        "source_kind": source_kind,
        "run_id": run_dir.name,
        "source": _source(gate_dir),
        "summary_source": _source(summary_path),
        "closeout_source": _source(closeout_path),
        "mode": str(summary.get("mode") or ""),
        "status": str(summary.get("status") or ""),
        "triggered": bool(summary.get("triggered", False)),
        "route": str(summary.get("route") or ""),
        "severity": str(summary.get("severity") or ""),
        "reason": str(summary.get("reason") or ""),
        "failure_fingerprint": str(summary.get("failure_fingerprint") or ""),
        "run_terminal_status": str(
            summary.get("run_terminal_status")
            or failure_pack.get("run_terminal_status")
            or ""
        ),
        "primary_failure_class": str(
            summary.get("primary_failure_class")
            or failure_pack.get("primary_failure_class")
            or ""
        ),
        "review_gate_summary_fresh": _review_gate_summary_fresh(summary, failure_pack),
        "attempt": _safe_int(summary.get("attempt")),
        "attempt_cap": _safe_int(summary.get("attempt_cap")),
        "budget_cap": summary.get("budget_cap") if isinstance(summary.get("budget_cap"), dict) else {},
        "required_roles": [
            str(role) for role in (summary.get("required_roles") or [])
            if str(role)
        ],
        "artifact_refs": refs,
        "policy": summary.get("policy") if isinstance(summary.get("policy"), dict) else {},
        "closeout": closeout_result,
        "decision": str(closeout_result.get("decision") or ""),
        "accepted": bool(closeout_result.get("accepted", False)),
    }


def _review_gate_summary_fresh(
    summary: dict[str, Any],
    failure_pack: dict[str, Any],
) -> bool:
    explicit = summary.get("review_gate_summary_fresh")
    if explicit is False:
        return False
    generated_at = _parse_iso(str(summary.get("generated_at") or ""))
    state_dir = str(failure_pack.get("state_dir") or "").strip()
    if generated_at is None or not state_dir:
        return bool(explicit) if explicit is not None else True
    try:
        events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    except Exception:
        return bool(explicit) if explicit is not None else True
    high_types = set(FATAL_EVENT_TYPES) | set(HIGH_SIGNAL_EVENT_TYPES)
    for event in events:
        if event.type not in high_types:
            continue
        ts = _parse_iso(str(event.ts or ""))
        if ts is not None and _utc(ts) > _utc(generated_at):
            return False
    return True


def _parse_iso(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _review_gate_projection(state_dir: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    roots = [
        ("run", state_dir / "autoresearch" / "runs"),
        ("resident", state_dir / "autoresearch" / "resident"),
    ]
    for source_kind, root in roots:
        if not root.exists():
            continue
        for run_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            row = _review_gate_record(run_dir, source_kind=source_kind)
            if row:
                rows.append(row)
    rows.sort(key=lambda item: (
        float((item.get("summary_source") or {}).get("mtime") or 0.0),
        str(item.get("run_id") or ""),
    ))
    by_route: dict[str, int] = {}
    for row in rows:
        route = str(row.get("route") or "unknown")
        by_route[route] = by_route.get(route, 0) + 1
    return {
        "summary": {
            "total": len(rows),
            "triggered": sum(1 for row in rows if row.get("triggered")),
            "errors": sum(1 for row in rows if row.get("status") == "error"),
            "accepted": sum(1 for row in rows if row.get("accepted")),
            "by_route": dict(sorted(by_route.items())),
        },
        "latest": rows[-1] if rows else None,
        "runs": rows[-100:],
        "sources": {
            "runs": _source(state_dir / "autoresearch" / "runs"),
            "resident": _source(state_dir / "autoresearch" / "resident"),
        },
    }


def _eval_results(state_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    roots = [
        state_dir / "autoresearch" / "loop" / "eval-results",
        state_dir / "autoresearch" / "resident",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            data = _read_json(path)
            if data.get("schema_version") != "eval-result.v1":
                continue
            score = data.get("score") if isinstance(data.get("score"), dict) else {}
            gate = data.get("gate") if isinstance(data.get("gate"), dict) else {}
            rows.append({
                "path": str(path),
                "result_id": data.get("result_id", ""),
                "scenario_id": data.get("scenario_id", ""),
                "mode": data.get("mode", ""),
                "gate": gate.get("final", ""),
                "score_total": score.get("total"),
            })
    return rows[-100:]


def _resident_projection(state_dir: Path) -> dict[str, Any]:
    root = state_dir / "autoresearch" / "resident"
    return {
        "source": _source(root),
        "run_dirs": [
            {
                "request_id": path.name,
                "source": _source(path),
                "report": _source(path / "report.md"),
                "journal": _source(path / "journal.jsonl"),
            }
            for path in sorted(root.iterdir()) if path.is_dir()
        ] if root.exists() else [],
    }


def _bug_candidate_files(project_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for base in (project_root / "backlogs", project_root / "tasks"):
        if not base.exists():
            continue
        for path in sorted(base.glob("*autoresearch*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "FailureSignal" not in text and "autoresearch" not in text.lower():
                continue
            status = ""
            dedupe = ""
            title = path.stem
            for line in text.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                elif line.startswith("> 状态:"):
                    status = line.split(":", 1)[1].strip()
                elif line.startswith("> Dedupe:"):
                    dedupe = line.split(":", 1)[1].strip()
            rows.append({
                "path": str(path),
                "title": title,
                "status": status,
                "dedupe_key": dedupe,
            })
    return rows


def project_autoresearch_state(
    state_dir: Path,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    state_dir = Path(state_dir)
    root = Path(project_root) if project_root is not None else state_dir.parent
    loop_dir = state_dir / "autoresearch" / "loop"
    journal_path = loop_dir / "journal.jsonl"
    report_path = loop_dir / "report.md"
    iterations = _read_jsonl(journal_path)
    runs = []
    for run_dir in _run_dirs(state_dir):
        review_gate = _review_gate_record(run_dir, source_kind="run")
        runs.append({
            "run_id": run_dir.name,
            "source": _source(run_dir),
            "events_summary": _read_json(run_dir / "events-summary.json"),
            "report": _source(run_dir / "report.md"),
            "review_gate": review_gate,
        })
    decisions = [decision.to_dict() for decision in read_trigger_decisions(state_dir)]
    maintenance_current = state_dir / "autoresearch" / "maintenance" / "current.yaml"
    return {
        "state_dir": str(state_dir),
        "sources": {
            "journal": _source(journal_path),
            "report": _source(report_path),
            "trigger_decisions": _source(state_dir / "autoresearch" / "triggers" / "decisions.jsonl"),
            "maintenance_current": _source(maintenance_current),
        },
        "iterations": iterations,
        "latest_iteration": iterations[-1] if iterations else None,
        "runs": runs,
        "triggers": decisions,
        "loop_requests": project_loop_requests(state_dir),
        "review_gate": _review_gate_projection(state_dir),
        "eval_results": _eval_results(state_dir),
        "resident": _resident_projection(state_dir),
        "maintenance": _read_json(maintenance_current),
        "bug_candidates": _bug_candidate_files(root),
        "holdout": holdout_projection(root),
        "experiments": project_experiment_graph(state_dir),
    }


__all__ = ["project_autoresearch_state"]
