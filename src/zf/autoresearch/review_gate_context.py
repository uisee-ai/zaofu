"""Autoresearch review-gate context and evidence pack builders.

The packs are run artifacts only. They summarize codebase context and failure
evidence for read-only review fanout children without mutating runtime truth.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.autoresearch.failure_signals import (
    FailureSignal,
    collect_failure_signals,
    severity_rank,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent

CODEBASE_CONTEXT_SCHEMA = "codebase_context_pack.v1"
FAILURE_EVIDENCE_SCHEMA = "failure_evidence_pack.v1"
FATAL_EVENT_TYPES = frozenset({
    "run.failed",
    "orchestrator.dispatch_failed",
    "worker.respawn.failed",
    "worker_stuck.recovery_failed",
    "worker.stuck.recovery_failed",
    "runtime.safe_halted",
    "autoresearch.run.failed",
})
HIGH_SIGNAL_EVENT_TYPES = frozenset({
    "worker.respawn.failed",
    "runtime.safe_halted",
    "dispatch.paused",
    "zaofu.bug.detected",
})
_TASK_TERMINAL_EVENTS = frozenset({
    "task.done",
    "task.done.accepted",
    "task.archived",
})
OWNER_PATHS = (
    "AGENTS.md",
    "zf.yaml",
    "docs/design/97-autoresearch-review-fanout-gate-design.md",
    "src/zf/autoresearch",
    "src/zf/cli/autoresearch.py",
    "src/zf/runtime/autoresearch_invocation.py",
    "src/zf/runtime/repair_authorization.py",
    "src/zf/runtime/self_repair_runner.py",
    "src/zf/runtime/orchestrator_reactor.py",
    "src/zf/runtime/control_actions_product.py",
    "src/zf/runtime/control_actions_helpers.py",
)


@dataclass(frozen=True)
class ReviewGatePrepareResult:
    run_dir: str
    state_dir: str
    source_root: str
    codebase_context_pack: str
    failure_evidence_pack: str
    events_summary: str
    codebase_pack_reused: bool
    route: str
    severity: str
    failure_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prepare_review_gate_context(
    *,
    run_dir: Path,
    state_dir: Path,
    source_root: Path,
) -> ReviewGatePrepareResult:
    run_dir = Path(run_dir)
    state_dir = Path(state_dir)
    source_root = Path(source_root).resolve()
    context_dir = run_dir / "review-gate" / "context"
    context_dir.mkdir(parents=True, exist_ok=True)

    source_commit = _git_head(source_root)
    cache_key = _cache_key(source_root, source_commit=source_commit)
    codebase_path = context_dir / "codebase_context_pack.json"
    codebase_md = context_dir / "codebase_context_pack.md"
    codebase_pack, reused = _load_reusable_codebase_pack(codebase_path, cache_key)
    if codebase_pack is None:
        codebase_pack = _build_codebase_context_pack(
            source_root=source_root,
            source_commit=source_commit,
            cache_key=cache_key,
        )
        _write_json(codebase_path, codebase_pack)
        _write_text(codebase_md, _codebase_pack_markdown(codebase_pack))
    elif not codebase_md.exists():
        _write_text(codebase_md, _codebase_pack_markdown(codebase_pack))

    evidence_pack, events_summary = _build_failure_evidence_pack(
        run_dir=run_dir,
        state_dir=state_dir,
        source_commit=source_commit,
    )
    evidence_path = context_dir / "failure_evidence_pack.json"
    evidence_md = context_dir / "failure_evidence_pack.md"
    summary_path = context_dir / "events_summary.json"
    _write_json(evidence_path, evidence_pack)
    _write_text(evidence_md, _failure_pack_markdown(evidence_pack))
    _write_json(summary_path, events_summary)

    route, severity = _classify_route(evidence_pack)
    return ReviewGatePrepareResult(
        run_dir=str(run_dir),
        state_dir=str(state_dir),
        source_root=str(source_root),
        codebase_context_pack=str(codebase_path),
        failure_evidence_pack=str(evidence_path),
        events_summary=str(summary_path),
        codebase_pack_reused=reused,
        route=route,
        severity=severity,
        failure_fingerprint=str(evidence_pack.get("failure_fingerprint") or ""),
    )


def _build_codebase_context_pack(
    *,
    source_root: Path,
    source_commit: str,
    cache_key: dict[str, Any],
) -> dict[str, Any]:
    relevant_files = _owner_files(source_root)
    payload: dict[str, Any] = {
        "schema_version": CODEBASE_CONTEXT_SCHEMA,
        "source_commit": source_commit,
        "generated_at": _now_iso(),
        "scope": "autoresearch-review-fanout-gate",
        "cache_key": cache_key,
        "relevant_files": relevant_files,
        "current_flows": [
            "autoresearch failure signal collection",
            "workflow.invoke.requested -> task.fanout.requested",
            "proposal-only autoresearch invocation",
            "authorized self-repair dispatch",
        ],
        "invariants": [
            "zf.yaml is the only control-plane config",
            "events.jsonl and kernel stores remain runtime truth",
            "fanout review children are read-only by default",
            "repair remains proposal-only unless explicitly authorized",
        ],
        "known_gaps": [
            "P0 sidecar does not alter autoresearch run/loop defaults",
            "canonical review task creation remains an explicit operator step",
        ],
        "required_regressions": [
            "pytest tests/test_autoresearch_review_gate.py",
            "pytest tests/test_cli_autoresearch.py",
        ],
        "do_not_touch": [
            "runtime state truth files outside append-only event helpers",
            "Channel as workflow executor",
            "autoresearch run/loop default semantics",
        ],
        "pack_hash": "",
    }
    payload["pack_hash"] = _payload_hash(payload)
    return payload


def _build_failure_evidence_pack(
    *,
    run_dir: Path,
    state_dir: Path,
    source_commit: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    signals = collect_failure_signals(Path(state_dir), run_dir=run_dir)
    fatal_event = _latest_fatal_event(events)
    high_signal_events = _high_signal_events(events)
    handoff_invariants = _handoff_invariants(events)
    severity = _max_severity(
        signals,
        fatal_event=fatal_event,
        high_signal_events=high_signal_events,
        handoff_invariants=handoff_invariants,
    )
    fingerprint = _failure_fingerprint(
        run_dir=run_dir,
        state_dir=state_dir,
        source_commit=source_commit,
        signals=signals,
        fatal_event=fatal_event,
        handoff_invariants=handoff_invariants,
        high_signal_events=high_signal_events,
    )
    event_ids = _event_refs(
        signals,
        fatal_event=fatal_event,
        high_signal_events=high_signal_events,
        handoff_invariants=handoff_invariants,
    )
    task_refs = _task_refs(events, event_ids)
    run_terminal_status = _run_terminal_status(
        fatal_event=fatal_event,
        handoff_invariants=handoff_invariants,
        signals=signals,
    )
    primary_failure_class = _primary_failure_class(
        fatal_event=fatal_event,
        handoff_invariants=handoff_invariants,
        high_signal_events=high_signal_events,
        signals=signals,
    )
    trace_refs = _existing_refs(
        run_dir,
        ("report.md", "eval-result.json", "eval_result.json", "journal.jsonl"),
    )
    screenshot_refs = _glob_refs(run_dir, ("*.png", "*.jpg", "*.jpeg", "*.webp"))
    log_refs = _glob_refs(run_dir, ("*.log", "*.txt"))
    events_summary = {
        "schema_version": "review_gate.events_summary.v1",
        "state_dir": str(state_dir),
        "event_count": len(events),
        "event_type_counts": _event_type_counts(events),
        "fatal_event_ids": [event.id for event in events if event.type in FATAL_EVENT_TYPES],
        "high_signal_event_ids": [event["id"] for event in high_signal_events],
        "handoff_invariants": handoff_invariants,
        "failure_signals": [signal.to_dict() for signal in signals],
    }
    evidence_pack = {
        "schema_version": FAILURE_EVIDENCE_SCHEMA,
        "run_dir": str(run_dir),
        "state_dir": str(state_dir),
        "source_commit": source_commit,
        "failure_fingerprint": fingerprint,
        "severity": severity,
        "run_terminal_status": run_terminal_status,
        "primary_failure_class": primary_failure_class,
        "fatal_event": _event_to_dict(fatal_event) if fatal_event else {},
        "high_signal_events": high_signal_events,
        "handoff_invariants": handoff_invariants,
        "event_refs": event_ids,
        "task_refs": task_refs,
        "trace_refs": trace_refs,
        "screenshot_refs": screenshot_refs,
        "log_refs": log_refs,
        "events_summary_ref": str(run_dir / "review-gate" / "context" / "events_summary.json"),
        "initial_hypotheses": [
            {
                "category": signal.category,
                "severity": signal.severity,
                "summary": signal.summary,
                "fingerprint": signal.fingerprint,
            }
            for signal in signals[:8]
        ] + _invariant_hypotheses(handoff_invariants),
    }
    return evidence_pack, events_summary


def _classify_route(evidence_pack: dict[str, Any]) -> tuple[str, str]:
    severity = str(evidence_pack.get("severity") or "medium")
    fingerprint = str(evidence_pack.get("failure_fingerprint") or "")
    fatal_event = evidence_pack.get("fatal_event")
    hypotheses = evidence_pack.get("initial_hypotheses") or []
    high = severity_rank(severity) >= severity_rank("high")
    runtime_keywords = (
        "runtime",
        "fanout",
        "workflow",
        "dispatch",
        "stuck",
        "task_ref",
        "handoff",
        "repair",
    )
    runtime_signal = any(keyword in fingerprint for keyword in runtime_keywords)
    for item in hypotheses:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get(key) or "") for key in ("category", "summary", "fingerprint"))
        runtime_signal = runtime_signal or any(keyword in text for keyword in runtime_keywords)
    if evidence_pack.get("handoff_invariants") or evidence_pack.get("high_signal_events"):
        runtime_signal = True
    if high and (runtime_signal or bool(fatal_event)):
        return "fanout_gate", severity
    if high:
        return "lightweight_review", severity
    return "direct_repair", severity


def _cache_key(source_root: Path, *, source_commit: str) -> dict[str, Any]:
    return {
        "source_commit": source_commit,
        "zf_yaml_hash": _file_hash(source_root / "zf.yaml"),
        "agents_md_hash": _file_hash(source_root / "AGENTS.md"),
        "owner_files_hash": _owner_files_hash(source_root),
        "schema_version": CODEBASE_CONTEXT_SCHEMA,
    }


def _load_reusable_codebase_pack(
    path: Path,
    cache_key: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None, False
    if payload.get("schema_version") != CODEBASE_CONTEXT_SCHEMA:
        return None, False
    if payload.get("cache_key") != cache_key:
        return None, False
    if str(payload.get("status") or "").lower() in {"stale", "incomplete"}:
        return None, False
    return payload, True


def _owner_files(source_root: Path) -> list[str]:
    files: list[str] = []
    for rel in OWNER_PATHS:
        path = source_root / rel
        if path.is_file():
            files.append(rel)
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix in {".py", ".md", ".yaml", ".yml"}:
                    files.append(child.relative_to(source_root).as_posix())
    return sorted(set(files))


def _owner_files_hash(source_root: Path) -> str:
    digest = hashlib.sha256()
    for rel in _owner_files(source_root):
        path = source_root / rel
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_file_hash(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _file_hash(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()


def _payload_hash(payload: dict[str, Any]) -> str:
    body = dict(payload)
    body["pack_hash"] = ""
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _git_head(source_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _high_signal_events(events: list[ZfEvent]) -> list[dict[str, Any]]:
    rows = [
        _event_to_dict(event)
        for event in events
        if event.type in HIGH_SIGNAL_EVENT_TYPES
    ]
    return rows[-25:]


def _handoff_invariants(events: list[ZfEvent]) -> list[dict[str, Any]]:
    latest_contract: dict[str, tuple[int, ZfEvent, str]] = {}
    for idx, event in enumerate(events):
        if event.type != "task.contract.update":
            continue
        task_id = _event_task_id(event)
        if not task_id:
            continue
        owner = _contract_owner(event)
        if not owner:
            continue
        latest_contract[task_id] = (idx, event, owner)

    invariants: list[dict[str, Any]] = []
    for task_id, (contract_idx, contract_event, expected) in sorted(latest_contract.items()):
        if _task_reached_terminal(events, task_id=task_id, after_idx=contract_idx):
            continue
        assignment_after = _first_assignee_event_after(
            events,
            task_id=task_id,
            event_type="task.assigned",
            assignee=expected,
            after_idx=contract_idx,
        )
        latest_assignment = _latest_assignee_event(events, task_id=task_id)
        latest_assignee = _event_assignee(latest_assignment[1]) if latest_assignment else ""
        if assignment_after is None and latest_assignee != expected:
            refs = [contract_event.id]
            if latest_assignment is not None:
                refs.append(latest_assignment[1].id)
            invariants.append({
                "type": "layer2_handoff_incomplete",
                "severity": "high",
                "task_id": task_id,
                "expected_assignee": expected,
                "assigned_to": latest_assignee,
                "contract_event_id": contract_event.id,
                "event_refs": sorted(set(refs)),
                "summary": (
                    f"contract owner is {expected!r}, but no matching "
                    f"task.assigned event exists after the contract update"
                ),
            })
            continue

        assignment_idx = assignment_after[0] if assignment_after is not None else contract_idx
        dispatch_after = _first_assignee_event_after(
            events,
            task_id=task_id,
            event_type="task.dispatched",
            assignee=expected,
            after_idx=assignment_idx,
        )
        if assignment_after is not None and dispatch_after is None:
            invariants.append({
                "type": "layer2_dispatch_missing",
                "severity": "high",
                "task_id": task_id,
                "expected_assignee": expected,
                "assigned_to": expected,
                "contract_event_id": contract_event.id,
                "assignment_event_id": assignment_after[1].id,
                "event_refs": sorted({contract_event.id, assignment_after[1].id}),
                "summary": (
                    f"task was assigned to {expected!r}, but no matching "
                    "task.dispatched event exists after the assignment"
                ),
            })
    return invariants[-25:]


def _event_payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _event_task_id(event: ZfEvent) -> str:
    payload = _event_payload(event)
    return str(event.task_id or payload.get("task_id") or "").strip()


def _contract_owner(event: ZfEvent) -> str:
    payload = _event_payload(event)
    contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else payload
    if not isinstance(contract, dict):
        return ""
    return str(contract.get("owner_instance") or contract.get("owner_role") or "").strip()


def _event_assignee(event: ZfEvent) -> str:
    payload = _event_payload(event)
    handoff = payload.get("handoff") if isinstance(payload.get("handoff"), dict) else {}
    return str(
        payload.get("assignee")
        or payload.get("role")
        or payload.get("worker")
        or payload.get("instance_id")
        or handoff.get("assignee")
        or ""
    ).strip()


def _latest_assignee_event(
    events: list[ZfEvent],
    *,
    task_id: str,
) -> tuple[int, ZfEvent] | None:
    for idx in range(len(events) - 1, -1, -1):
        event = events[idx]
        if event.type != "task.assigned":
            continue
        if _event_task_id(event) == task_id and _event_assignee(event):
            return idx, event
    return None


def _first_assignee_event_after(
    events: list[ZfEvent],
    *,
    task_id: str,
    event_type: str,
    assignee: str,
    after_idx: int,
) -> tuple[int, ZfEvent] | None:
    for idx, event in enumerate(events[after_idx + 1:], start=after_idx + 1):
        if event.type != event_type:
            continue
        if _event_task_id(event) != task_id:
            continue
        if _event_assignee(event) == assignee:
            return idx, event
    return None


def _task_reached_terminal(
    events: list[ZfEvent],
    *,
    task_id: str,
    after_idx: int,
) -> bool:
    for event in events[after_idx + 1:]:
        if _event_task_id(event) != task_id:
            continue
        if event.type in _TASK_TERMINAL_EVENTS:
            return True
        if event.type == "task.status_changed":
            to_status = str(_event_payload(event).get("to") or "").strip()
            if to_status in {"done", "cancelled"}:
                return True
    return False


def _run_terminal_status(
    *,
    fatal_event: ZfEvent | None,
    handoff_invariants: list[dict[str, Any]],
    signals: list[FailureSignal],
) -> str:
    if fatal_event is not None:
        return "fatal"
    if handoff_invariants:
        return "incomplete"
    if signals:
        return "failed"
    return "unknown"


def _primary_failure_class(
    *,
    fatal_event: ZfEvent | None,
    handoff_invariants: list[dict[str, Any]],
    high_signal_events: list[dict[str, Any]],
    signals: list[FailureSignal],
) -> str:
    high_signal_text = " ".join(
        json.dumps(event, ensure_ascii=False, sort_keys=True).lower()
        for event in high_signal_events
    )
    if "respawn_failure_cascade" in high_signal_text or (
        "worker.respawn.failed" in high_signal_text
        and (
            "can't find window" in high_signal_text
            or "can't find session" in high_signal_text
            or "can't find pane" in high_signal_text
        )
    ):
        return "pane_grid_respawn_failure"
    if handoff_invariants:
        return str(handoff_invariants[0].get("type") or "layer2_handoff_incomplete")
    if fatal_event is not None:
        if fatal_event.type == "worker.respawn.failed":
            return "worker_respawn_failure"
        if fatal_event.type == "runtime.safe_halted":
            return "runtime_safe_halt"
        return fatal_event.type.replace(".", "_")
    for signal in signals:
        if signal.category:
            return signal.category
    return "unknown_failure"


def _invariant_hypotheses(invariants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "category": str(item.get("type") or ""),
            "severity": str(item.get("severity") or "high"),
            "summary": str(item.get("summary") or ""),
            "fingerprint": (
                "handoff:"
                f"{item.get('type')}:"
                f"{item.get('task_id')}:"
                f"{item.get('expected_assignee')}"
            ),
        }
        for item in invariants[:8]
    ]


def _latest_fatal_event(events: list[ZfEvent]) -> ZfEvent | None:
    for event in reversed(events):
        if event.type in FATAL_EVENT_TYPES:
            return event
    return None


def _max_severity(
    signals: list[FailureSignal],
    *,
    fatal_event: ZfEvent | None,
    high_signal_events: list[dict[str, Any]],
    handoff_invariants: list[dict[str, Any]],
) -> str:
    severity = "medium"
    if signals:
        severity = max((signal.severity for signal in signals), key=severity_rank)
    if fatal_event is not None or high_signal_events or handoff_invariants:
        severity = max((severity, "high"), key=severity_rank)
    return severity


def _failure_fingerprint(
    *,
    run_dir: Path,
    state_dir: Path,
    source_commit: str,
    signals: list[FailureSignal],
    fatal_event: ZfEvent | None,
    handoff_invariants: list[dict[str, Any]],
    high_signal_events: list[dict[str, Any]],
) -> str:
    for signal in signals:
        if signal.fingerprint:
            return signal.fingerprint
    if fatal_event is not None:
        payload = fatal_event.payload if isinstance(fatal_event.payload, dict) else {}
        reason = str(payload.get("reason") or payload.get("error") or fatal_event.type)
        return f"fatal:{fatal_event.type}:{reason[:80]}"
    if handoff_invariants:
        first = handoff_invariants[0]
        return (
            "handoff:"
            f"{first.get('type')}:"
            f"{first.get('task_id')}:"
            f"{first.get('expected_assignee')}"
        )
    if high_signal_events:
        first = high_signal_events[-1]
        return f"runtime:{first.get('type')}:{first.get('id')}"
    seed = "|".join([str(run_dir), str(state_dir), source_commit])
    return "failure:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _event_refs(
    signals: list[FailureSignal],
    *,
    fatal_event: ZfEvent | None,
    high_signal_events: list[dict[str, Any]],
    handoff_invariants: list[dict[str, Any]],
) -> list[str]:
    refs: list[str] = []
    if fatal_event is not None:
        refs.append(fatal_event.id)
    refs.extend(str(event.get("id") or "") for event in high_signal_events)
    for invariant in handoff_invariants:
        refs.extend(str(ref) for ref in invariant.get("event_refs") or [])
    for signal in signals:
        refs.extend(signal.event_ids)
    return sorted(set(ref for ref in refs if ref))


def _task_refs(events: list[ZfEvent], event_ids: list[str]) -> list[str]:
    ids = set(event_ids)
    refs: set[str] = set()
    for event in events:
        if event.id not in ids:
            continue
        payload_task = ""
        if isinstance(event.payload, dict):
            payload_task = str(event.payload.get("task_id") or "").strip()
        task_id = str(event.task_id or payload_task).strip()
        if task_id:
            refs.add(task_id)
    return sorted(refs)


def _event_type_counts(events: list[ZfEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.type] = counts.get(event.type, 0) + 1
    return dict(sorted(counts.items()))


def _event_to_dict(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return {
        "id": event.id,
        "type": event.type,
        "ts": event.ts,
        "actor": event.actor or "",
        "task_id": event.task_id or "",
        "payload": payload,
    }


def _existing_refs(root: Path, names: tuple[str, ...]) -> list[str]:
    refs: list[str] = []
    for name in names:
        path = root / name
        if path.exists():
            refs.append(str(path))
    return refs


def _glob_refs(root: Path, patterns: tuple[str, ...]) -> list[str]:
    refs: list[str] = []
    for pattern in patterns:
        refs.extend(str(path) for path in sorted(root.rglob(pattern)) if path.is_file())
    return refs


def _codebase_pack_markdown(payload: dict[str, Any]) -> str:
    files = "\n".join(f"- `{path}`" for path in payload.get("relevant_files", [])[:80])
    invariants = "\n".join(f"- {item}" for item in payload.get("invariants", []))
    return (
        "# Codebase Context Pack\n\n"
        f"- schema: `{payload.get('schema_version')}`\n"
        f"- source_commit: `{payload.get('source_commit')}`\n"
        f"- pack_hash: `{payload.get('pack_hash')}`\n\n"
        "## Invariants\n\n"
        f"{invariants}\n\n"
        "## Relevant Files\n\n"
        f"{files}\n"
    )


def _failure_pack_markdown(payload: dict[str, Any]) -> str:
    hypotheses = "\n".join(
        f"- {item.get('severity')}: {item.get('category')} — {item.get('summary')}"
        for item in payload.get("initial_hypotheses", [])
        if isinstance(item, dict)
    )
    return (
        "# Failure Evidence Pack\n\n"
        f"- schema: `{payload.get('schema_version')}`\n"
        f"- severity: `{payload.get('severity')}`\n"
        f"- fingerprint: `{payload.get('failure_fingerprint')}`\n\n"
        "## Initial Hypotheses\n\n"
        f"{hypotheses or '- none'}\n"
    )


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "CODEBASE_CONTEXT_SCHEMA",
    "FAILURE_EVIDENCE_SCHEMA",
    "FATAL_EVENT_TYPES",
    "HIGH_SIGNAL_EVENT_TYPES",
    "ReviewGatePrepareResult",
    "prepare_review_gate_context",
]
