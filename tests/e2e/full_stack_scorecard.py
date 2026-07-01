"""Read-only full-stack validation scorecard for real ZaoFu runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from zf.core.events.log import EventLog
from zf.core.events.segments import build_event_manifest


SCHEMA_VERSION = "zaofu.full_stack_validation.v1"
SYNTHETIC_ACTORS = {"e2e", "test", "pytest", "script", "driver", "scorecard"}
FANOUT_TERMINAL_EVENTS = {
    "fanout.child.completed",
    "fanout.child.failed",
    "fanout.child.blocked",
}
FANOUT_RUNTIME_EVENTS = {
    "fanout.started",
    "fanout.child.dispatched",
    "fanout.aggregate.completed",
}


@dataclass(frozen=True)
class EventRequirement:
    event_type: str
    predicate: Callable[[dict[str, Any]], bool] | None = None
    description: str = ""

    def matches(self, event: dict[str, Any]) -> bool:
        if str(event.get("type") or "") != self.event_type:
            return False
        return True if self.predicate is None else bool(self.predicate(event))


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    label: str
    requirements: tuple[EventRequirement, ...]
    next_command: str


@dataclass
class CaseResult:
    case_id: str
    label: str
    passed: bool
    evidence_event_ids: list[str] = field(default_factory=list)
    missing_events: list[str] = field(default_factory=list)
    reason_class: str = ""
    next_command: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "label": self.label,
            "passed": self.passed,
            "evidence_event_ids": self.evidence_event_ids,
            "missing_events": self.missing_events,
            "reason_class": self.reason_class,
            "next_command": self.next_command,
        }


def payload(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("payload") or {}
    return value if isinstance(value, dict) else {}


def event_id(event: dict[str, Any]) -> str:
    return str(event.get("id") or "")


def event_actor(event: dict[str, Any]) -> str:
    return str(event.get("actor") or "")


def is_synthetic_event(event: dict[str, Any]) -> bool:
    actor = event_actor(event).strip().lower()
    data = payload(event)
    return (
        actor in SYNTHETIC_ACTORS
        or bool(data.get("validation_synthetic"))
        or str(data.get("source") or "").strip().lower() in SYNTHETIC_ACTORS
    )


def _payload_eq(key: str, *values: str) -> Callable[[dict[str, Any]], bool]:
    allowed = {str(value) for value in values}

    def inner(event: dict[str, Any]) -> bool:
        return str(payload(event).get(key) or "") in allowed

    return inner


CASE_SPECS: tuple[CaseSpec, ...] = (
    CaseSpec(
        case_id="issue",
        label="Issue ingest / bug-fix entry",
        requirements=(
            EventRequirement(
                "task.created",
                lambda event: (
                    str(payload(event).get("source_kind") or "") == "issue"
                    or str(payload(event).get("via") or "") == "zf issue ingest"
                ),
                "task.created from zf issue ingest",
            ),
        ),
        next_command="uv run zf issue ingest <issue-candidate.md>",
    ),
    CaseSpec(
        case_id="prd",
        label="PRD / product task-map entry",
        requirements=(
            EventRequirement("product.plan.ready"),
            EventRequirement("task_map.ready"),
            EventRequirement("product_delivery.task_map.accepted"),
            EventRequirement("product_delivery.wave.ready"),
        ),
        next_command="run product delivery task-map ingestion and wave readiness",
    ),
    CaseSpec(
        case_id="refactor",
        label="Refactor review -> plan entry",
        requirements=(
            EventRequirement("zaofu.refactor.review.ready"),
            EventRequirement("zaofu.refactor.plan.ready"),
        ),
        next_command="run the refactor review/plan workflow yaml",
    ),
    CaseSpec(
        case_id="new_task",
        label="Web New Task entry",
        requirements=(
            EventRequirement("web.action.requested"),
            EventRequirement("task.created"),
        ),
        next_command="use Web New Task and confirm task.created appears",
    ),
    CaseSpec(
        case_id="kanban_agent",
        label="Kanban Agent headless turn",
        requirements=(
            EventRequirement("kanban.agent.turn.started"),
            EventRequirement("kanban.agent.turn.completed"),
        ),
        next_command="send a Kanban Agent message through the Web dashboard",
    ),
    CaseSpec(
        case_id="channel",
        label="Channel group message / reply",
        requirements=(
            EventRequirement("channel.message.posted"),
            EventRequirement("channel.agent.reply.completed"),
        ),
        next_command="post a Channel message and wait for agent reply completed",
    ),
    CaseSpec(
        case_id="fanout_kanban_agent",
        label="Kanban Agent triggered fanout",
        requirements=(
            EventRequirement(
                "workflow.invoke.requested",
                _payload_eq("entrypoint", "kanban-agent", "kanban_agent"),
            ),
            EventRequirement("fanout.aggregate.completed"),
        ),
        next_command="trigger workflow fanout from Kanban Agent",
    ),
    CaseSpec(
        case_id="fanout_channel",
        label="Channel triggered fanout",
        requirements=(
            EventRequirement("workflow.invoke.requested", _payload_eq("entrypoint", "channel")),
            EventRequirement("fanout.aggregate.completed"),
        ),
        next_command="trigger workflow fanout from Channel",
    ),
)


def read_events(state_dir: Path) -> list[dict[str, Any]]:
    return [
        json.loads(event.to_json())
        for event in EventLog(state_dir / "events.jsonl").read_all()
    ]


def event_source_summary(state_dir: Path) -> dict[str, Any]:
    manifest = build_event_manifest(state_dir)
    active_count = 0
    archive_count = 0
    segments: list[dict[str, Any]] = []
    for segment in manifest.segments:
        count = _count_jsonl_records(segment.path)
        if segment.kind == "active":
            active_count += count
        else:
            archive_count += count
        segments.append({
            "rel_path": segment.rel_path,
            "kind": segment.kind,
            "records": count,
            "bytes": segment.size,
        })
    projection: dict[str, Any] = {}
    try:
        from zf.web.projections import read_model

        status = read_model.projection_status(state_dir)
        projection = {
            "source": "read_model.sqlite",
            "projection_state": status.get("projection_state", "unknown"),
            "projected_seq": status.get("projected_seq", 0),
            "projection_lag": status.get("projection_lag"),
            "manifest_digest": status.get("manifest_digest", ""),
        }
    except Exception as exc:
        projection = {
            "source": "read_model.sqlite",
            "projection_state": "unavailable",
            "error": str(exc),
        }
    return {
        "schema_version": "event-source-summary.v1",
        "active_count": active_count,
        "archive_count": archive_count,
        "total_count": active_count + archive_count,
        "segment_count": len(manifest.segments),
        "total_bytes": manifest.total_bytes,
        "manifest_digest": manifest.digest,
        "segments": segments,
        "projection": projection,
    }


def _count_jsonl_records(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    last_newline = data.rfind(b"\n")
    if last_newline < 0:
        return 0
    return sum(1 for line in data[: last_newline + 1].splitlines() if line.strip())


def evaluate_case(spec: CaseSpec, events: Iterable[dict[str, Any]]) -> CaseResult:
    evidence: list[str] = []
    missing: list[str] = []
    event_list = list(events)
    for req in spec.requirements:
        match = next((event for event in event_list if req.matches(event)), None)
        if match is None:
            missing.append(req.description or req.event_type)
        else:
            evidence.append(event_id(match))
    passed = not missing
    return CaseResult(
        case_id=spec.case_id,
        label=spec.label,
        passed=passed,
        evidence_event_ids=[item for item in evidence if item],
        missing_events=missing,
        reason_class="" if passed else "missing_required_events",
        next_command="" if passed else spec.next_command,
    )


def _events_by_fanout(events: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        fanout_id = str(payload(event).get("fanout_id") or "")
        if fanout_id:
            grouped.setdefault(fanout_id, []).append(event)
    return grouped


def _manifest_for(state_dir: Path, fanout_id: str) -> tuple[Path | None, dict[str, Any]]:
    path = state_dir / "fanouts" / fanout_id / "manifest.json"
    if not path.exists():
        return None, {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return path, {}
    return path, value if isinstance(value, dict) else {}


def evaluate_fanout_gate(state_dir: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    fanouts = _events_by_fanout(events)
    details: list[dict[str, Any]] = []
    accepted = 0
    blocked_reasons: list[str] = []
    for fanout_id, fanout_events in sorted(fanouts.items()):
        types = {str(event.get("type") or "") for event in fanout_events}
        runtime_events = [
            event for event in fanout_events
            if str(event.get("type") or "") in FANOUT_RUNTIME_EVENTS
        ]
        synthetic = [event for event in runtime_events if is_synthetic_event(event)]
        missing = sorted(FANOUT_RUNTIME_EVENTS - types)
        terminal = [
            event for event in fanout_events
            if str(event.get("type") or "") in FANOUT_TERMINAL_EVENTS
        ]
        manifest_path, manifest = _manifest_for(state_dir, fanout_id)
        aggregate = manifest.get("aggregate") if isinstance(manifest.get("aggregate"), dict) else {}
        children = manifest.get("children") if isinstance(manifest, dict) else []
        manifest_ok = (
            manifest_path is not None
            and isinstance(children, list)
            and bool(children)
            and str(aggregate.get("status") or "") in {"completed", "passed", "ready"}
        )
        passed = not missing and bool(terminal) and not synthetic and manifest_ok
        if passed:
            accepted += 1
        else:
            if missing:
                blocked_reasons.append(f"{fanout_id}:missing:{','.join(missing)}")
            if synthetic:
                blocked_reasons.append(f"{fanout_id}:synthetic_runtime_events")
            if not terminal:
                blocked_reasons.append(f"{fanout_id}:missing_child_terminal")
            if not manifest_ok:
                blocked_reasons.append(f"{fanout_id}:manifest_not_complete")
        details.append({
            "fanout_id": fanout_id,
            "passed": passed,
            "event_types": sorted(types),
            "missing_runtime_events": missing,
            "terminal_child_events": [event_id(event) for event in terminal if event_id(event)],
            "synthetic_event_ids": [event_id(event) for event in synthetic if event_id(event)],
            "manifest_ref": str(manifest_path or ""),
            "manifest_status": str(aggregate.get("status") or ""),
        })
    return {
        "passed": accepted > 0,
        "accepted_fanout_count": accepted,
        "fanout_count": len(fanouts),
        "details": details,
        "reason_class": "" if accepted else "missing_or_synthetic_fanout_provenance",
        "blocked_reasons": blocked_reasons,
        "next_command": "" if accepted else (
            "re-run workflow fanout and require runtime fanout.started, "
            "fanout.child.dispatched, child terminal, fanout.aggregate.completed, "
            "and manifest aggregate.status=completed"
        ),
    }


def evaluate_codex_gate(events: list[dict[str, Any]], *, require_real_codex: bool) -> dict[str, Any]:
    hook_events = [
        event for event in events
        if str(event.get("type") or "").startswith("codex.hook.")
    ]
    usage_events = [
        event for event in events
        if event.get("type") == "agent.usage"
        and str(payload(event).get("backend") or "").startswith("codex")
    ]
    passed = True if not require_real_codex else bool(hook_events and usage_events)
    missing: list[str] = []
    if require_real_codex and not hook_events:
        missing.append("codex.hook.*")
    if require_real_codex and not usage_events:
        missing.append("agent.usage[backend=codex]")
    return {
        "passed": passed,
        "required": require_real_codex,
        "hook_count": len(hook_events),
        "usage_count": len(usage_events),
        "missing_events": missing,
        "reason_class": "" if passed else "real_codex_observability_missing",
        "next_command": "" if passed else (
            "start a real Codex role/turn with project .codex/hooks.json trusted "
            "and verify codex.hook.* plus agent.usage backend=codex"
        ),
    }


def build_scorecard(state_dir: Path, *, require_real_codex: bool = False) -> dict[str, Any]:
    state_dir = state_dir.resolve()
    events = read_events(state_dir)
    sources = event_source_summary(state_dir)
    case_results = [evaluate_case(spec, events) for spec in CASE_SPECS]
    fanout_gate = evaluate_fanout_gate(state_dir, events)
    codex_gate = evaluate_codex_gate(events, require_real_codex=require_real_codex)
    passed = (
        bool(events)
        and all(item.passed for item in case_results)
        and fanout_gate["passed"]
        and codex_gate["passed"]
    )
    missing_required_events = [
        f"{item.case_id}:{missing}"
        for item in case_results
        for missing in item.missing_events
    ]
    if not fanout_gate["passed"]:
        missing_required_events.append("fanout:trace_chain_provenance")
    missing_required_events.extend(f"codex:{item}" for item in codex_gate["missing_events"])
    return {
        "schema_version": SCHEMA_VERSION,
        "state_dir": str(state_dir),
        "status": "passed" if passed else "blocked",
        "passed": passed,
        "event_count": len(events),
        "event_sources": sources,
        "require_real_codex": require_real_codex,
        "cases": {item.case_id: item.to_dict() for item in case_results},
        "hard_gates": {
            "fanout_trace_chain": fanout_gate,
            "real_codex_observability": codex_gate,
        },
        "missing_required_events": missing_required_events,
    }


def write_markdown_report(scorecard: dict[str, Any], path: Path) -> None:
    lines = [
        "# ZaoFu Full-stack Validation",
        "",
        f"- status: `{scorecard['status']}`",
        f"- state_dir: `{scorecard['state_dir']}`",
        f"- event_count: `{scorecard['event_count']}`",
        f"- require_real_codex: `{scorecard['require_real_codex']}`",
        "",
        "## Cases",
        "",
        "| case | status | missing | next |",
        "|---|---|---|---|",
    ]
    for case in scorecard["cases"].values():
        status = "pass" if case["passed"] else "blocked"
        missing = ", ".join(case["missing_events"])
        lines.append(
            f"| `{case['case_id']}` | {status} | {missing} | {case['next_command']} |"
        )
    lines.extend([
        "",
        "## Hard Gates",
        "",
        "```json",
        json.dumps(scorecard["hard_gates"], ensure_ascii=False, indent=2),
        "```",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
