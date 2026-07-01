"""Project Spine Review read model."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from zf.autoresearch.triggers import scan_trigger_decisions
from zf.core.config.project_context import ProjectContext, resolve_project_context
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.project_spine_review_common import (
    ARTIFACT_EVENT,
    FAULT_EVENT_TYPES,
    REFLECTION_SCHEMA_VERSION,
    SCHEMA_VERSION,
    WORKFLOW_REQUIRED_EVENTS,
    SpineReviewError,
    cli_identity,
    now_iso,
    parse_since,
    parse_ts,
    project_id,
    read_events,
    review_id,
)
from zf.runtime.supervisor_inspection import (
    read_supervisor_snapshot,
    supervisor_snapshot_ref,
)


def resolve_spine_review_context(
    *,
    project_root: Path | None = None,
    explicit_state_dir: str | Path | None = None,
) -> ProjectContext:
    """Resolve project context and fail closed on state-dir mismatch."""
    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd()
    configured = resolve_project_context(cwd=root, require_config=True)
    if explicit_state_dir is None:
        return configured
    candidate = Path(explicit_state_dir).expanduser()
    if not candidate.is_absolute():
        candidate = configured.project_root / candidate
    candidate = candidate.resolve()
    if candidate != configured.state_dir:
        raise SpineReviewError(
            "state_dir mismatch: "
            f"zf.yaml={configured.state_dir} explicit={candidate}"
        )
    return configured


def build_project_spine_review(
    context: ProjectContext,
    *,
    since: str | None = None,
) -> dict[str, Any]:
    """Build a read-only spine review dict."""
    state_dir = context.state_dir
    project_root = context.project_root
    config = context.config
    pid = project_id(config=config, project_root=project_root)
    reviewed_at = now_iso()
    cutoff = parse_since(since)
    all_events = read_events(state_dir, config=config)
    events = all_events
    if cutoff is not None:
        events = [
            event for event in events
            if (parse_ts(event.ts) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
        ]
    tasks = _read_tasks(state_dir)
    design_spine = _design_spine(project_root=project_root, tasks=tasks)
    delivery_spine = _delivery_spine(tasks=tasks, events=events)
    runtime_spine = _runtime_spine(state_dir=state_dir, events=events)
    drift = _drift_classification(design_spine, delivery_spine, runtime_spine)
    verdict = _verdict(delivery_spine, runtime_spine, drift)
    rid = review_id(pid, reviewed_at, events)
    corrective_actions = _corrective_actions(
        verdict=verdict,
        design_spine=design_spine,
        delivery_spine=delivery_spine,
        runtime_spine=runtime_spine,
    )
    reflection = _reflection(
        verdict=verdict,
        drift=drift,
        corrective_actions=corrective_actions,
        previous_reflections=_previous_reflections(
            state_dir=state_dir,
            project_id_value=pid,
            current_review_id=rid,
            drift=drift,
            events=all_events,
        ),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "review_id": rid,
        "project_id": pid,
        "project_name": (
            config.project.name
            if config is not None and config.project.name else project_root.name
        ),
        "project_root": str(project_root),
        "config_path": str(context.config_path),
        "state_dir": str(state_dir),
        "reviewed_at": reviewed_at,
        "since": since or "all",
        "cli_identity": cli_identity(),
        "verdict": verdict,
        "confidence": _confidence(verdict, runtime_spine, delivery_spine),
        "drift": drift,
        "design_spine": design_spine,
        "delivery_spine": delivery_spine,
        "runtime_spine": runtime_spine,
        "reflection": reflection,
        "corrective_actions": corrective_actions,
    }


def _read_tasks(state_dir: Path) -> list[Task]:
    path = state_dir / "kanban.json"
    if not path.exists():
        return []
    try:
        return TaskStore(path).list_all_with_archive(last_days=14)
    except Exception:
        return []


def _design_spine(*, project_root: Path, tasks: list[Task]) -> dict[str, Any]:
    refs = _canonical_design_refs(project_root)
    missing_refs = []
    for task in tasks:
        contract = task.contract
        if task.status not in {"backlog", "in_progress", "review", "test", "judge"}:
            continue
        absent = [
            name for name in ("spec_ref", "plan_ref", "tdd_ref")
            if not getattr(contract, name, "")
        ]
        if absent:
            missing_refs.append({
                "task_id": task.id,
                "missing": absent,
            })
    findings = []
    if not refs:
        findings.append("未发现明确 canonical design/spec/plan 文档。")
    if missing_refs:
        findings.append(f"{len(missing_refs)} 个活跃/候选任务缺少设计或计划引用。")
    return {
        "status": "needs_attention" if missing_refs else ("observed" if refs else "unknown"),
        "canonical_refs": refs,
        "missing_contract_refs": missing_refs[:20],
        "findings": findings,
    }


def _delivery_spine(*, tasks: list[Task], events: list[ZfEvent]) -> dict[str, Any]:
    counts = Counter(task.status or "unknown" for task in tasks)
    workflow = [_audit_task(task.id, events) for task in tasks if _should_audit(task)]
    partial = [row for row in workflow if row["status"] != "complete"]
    active = sum(counts.get(key, 0) for key in ("in_progress", "review", "test", "judge"))
    findings = []
    if partial:
        findings.append(f"{len(partial)} 个任务 workflow evidence 不完整。")
    if counts.get("backlog", 0) and active == 0:
        findings.append("存在 backlog 但没有 active/review/test/judge 任务。")
    status = "blocked" if partial else ("idle_with_backlog" if counts.get("backlog", 0) and active == 0 else "healthy")
    return {
        "status": status,
        "task_counts": dict(counts),
        "active_count": active,
        "workflow_audit": {
            "audited": len(workflow),
            "complete": sum(1 for row in workflow if row["status"] == "complete"),
            "partial": len(partial),
            "tasks": workflow[:50],
        },
        "partial_tasks": [row["task_id"] for row in partial[:20]],
        "missing_evidence": sorted({
            miss for row in partial for miss in row.get("missing_events", [])
        }),
        "findings": findings,
    }


def _runtime_spine(*, state_dir: Path, events: list[ZfEvent]) -> dict[str, Any]:
    fault_events = [
        event for event in events
        if event.type in FAULT_EVENT_TYPES
    ]
    trigger_decisions = []
    try:
        trigger_decisions = [
            decision.to_dict()
            for decision in scan_trigger_decisions(state_dir)
        ]
    except Exception as exc:
        trigger_decisions = [{
            "decision": "scan_failed",
            "reason": str(exc),
        }]
    accepted = [
        row for row in trigger_decisions
        if row.get("decision") == "accepted"
    ]
    workers = _worker_summary(state_dir, events)
    supervisor = _supervisor_context(state_dir)
    supervisor_attention = int(
        (supervisor.get("attention_summary") or {}).get("open") or 0
    )
    findings = []
    if fault_events:
        findings.append(f"发现 {len(fault_events)} 个 runtime fault event。")
    if accepted:
        findings.append(f"autoresearch trigger accepted: {len(accepted)}。")
    if workers.get("stuck", 0):
        findings.append(f"{workers['stuck']} 个 worker stuck。")
    if supervisor_attention:
        findings.append(f"Supervisor attention open: {supervisor_attention}。")
    status = (
        "faulted"
        if fault_events or accepted or workers.get("stuck", 0)
        else "needs_attention"
        if supervisor_attention
        else "healthy"
    )
    return {
        "status": status,
        "fault_event_counts": dict(Counter(event.type for event in fault_events)),
        "fault_events": [_event_ref(event) for event in fault_events[-20:]],
        "autoresearch_triggers": trigger_decisions[:20],
        "accepted_triggers": accepted[:20],
        "worker_summary": workers,
        "supervisor_snapshot": supervisor,
        "findings": findings,
    }


def _supervisor_context(state_dir: Path) -> dict[str, Any]:
    snapshot = read_supervisor_snapshot(state_dir)
    if not snapshot:
        return {"status": "empty"}
    plan = (
        snapshot.get("plan_integrity")
        if isinstance(snapshot.get("plan_integrity"), dict)
        else {}
    )
    return {
        "status": "ready",
        "schema_version": str(snapshot.get("schema_version") or ""),
        "generated_at": str(snapshot.get("generated_at") or ""),
        "snapshot_ref": supervisor_snapshot_ref(state_dir),
        "attention_summary": (
            snapshot.get("attention_summary")
            if isinstance(snapshot.get("attention_summary"), dict)
            else {}
        ),
        "plan_integrity_summary": (
            plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
        ),
        "pause_lifecycle": (
            snapshot.get("pause_lifecycle")
            if isinstance(snapshot.get("pause_lifecycle"), dict)
            else {}
        ),
    }


def _drift_classification(
    design_spine: dict[str, Any],
    delivery_spine: dict[str, Any],
    runtime_spine: dict[str, Any],
) -> list[str]:
    drift: list[str] = []
    if design_spine.get("missing_contract_refs"):
        drift.append("contract_evidence_debt")
    if delivery_spine.get("status") in {"blocked", "idle_with_backlog"}:
        drift.append("task_decomposition_drift")
    if runtime_spine.get("status") == "faulted":
        drift.append("runtime_harness_fault")
    return drift


def _verdict(
    delivery_spine: dict[str, Any],
    runtime_spine: dict[str, Any],
    drift: list[str],
) -> str:
    if "runtime_harness_fault" in drift:
        return "pause_and_repair_harness"
    if "contract_evidence_debt" in drift:
        return "correct_task"
    if delivery_spine.get("status") == "idle_with_backlog":
        return "split_or_replan"
    return "continue"


def _corrective_actions(
    *,
    verdict: str,
    design_spine: dict[str, Any],
    delivery_spine: dict[str, Any],
    runtime_spine: dict[str, Any],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if verdict == "pause_and_repair_harness":
        refs = [
            f"event:{row.get('event_id')}"
            for row in runtime_spine.get("fault_events", [])
            if row.get("event_id")
        ]
        if not refs:
            refs = [
                f"trigger:{row.get('trigger_id')}"
                for row in runtime_spine.get("accepted_triggers", [])
                if row.get("trigger_id")
            ]
        actions.append({
            "action_id": "A1",
            "kind": "repair_harness",
            "priority": "P0",
            "target": "runtime fault blocks trustworthy dispatch",
            "proposal": "暂停继续派发项目任务,先复现 runtime fault 并创建聚焦修复任务。",
            "verify": "step -> verify: 同类任务重新派发不再产生相同 fault event 或 accepted trigger。",
            "evidence_refs": refs,
        })
    if design_spine.get("missing_contract_refs") or delivery_spine.get("missing_evidence"):
        evidence_refs = [
            f"task:{row.get('task_id')}"
            for row in design_spine.get("missing_contract_refs", [])[:10]
        ]
        evidence_refs.extend(
            f"missing:{item}" for item in delivery_spine.get("missing_evidence", [])[:10]
        )
        actions.append({
            "action_id": f"A{len(actions) + 1}",
            "kind": "correct_task",
            "priority": "P1",
            "target": "task contract / gate evidence debt",
            "proposal": "补齐 task contract 的 spec/plan/tdd/evidence refs,并重新运行 workflow audit。",
            "verify": "step -> verify: workflow audit 不再报告对应 missing evidence。",
            "evidence_refs": evidence_refs,
        })
    if not actions and delivery_spine.get("status") == "idle_with_backlog":
        actions.append({
            "action_id": "A1",
            "kind": "split_or_replan",
            "priority": "P1",
            "target": "backlog exists without active delivery",
            "proposal": "选择最高优先级 backlog 转 active,或明确 defer 触发条件。",
            "verify": "step -> verify: Kanban 出现 active task 或 backlog 项被标记 defer。",
            "evidence_refs": ["kanban:task_counts"],
        })
    return actions


def _reflection(
    *,
    verdict: str,
    drift: list[str],
    corrective_actions: list[dict[str, Any]],
    previous_reflections: list[dict[str, Any]],
) -> dict[str, Any]:
    selected = corrective_actions[0] if corrective_actions else {}
    if verdict == "pause_and_repair_harness":
        hypothesis = "当前阻塞更可能来自 harness/runtime fault,继续派发会放大噪音。"
        better = selected.get("proposal") or "先修 runtime,再恢复派发。"
    elif verdict == "correct_task":
        hypothesis = "当前问题更可能是任务 contract 和 evidence 约束不足。"
        better = selected.get("proposal") or "先补 contract/evidence,再继续执行。"
    elif verdict == "split_or_replan":
        hypothesis = "当前 backlog 未进入交付流,需要明确下一步或重新拆分。"
        better = selected.get("proposal") or "先选一条最小可验证主线。"
    else:
        hypothesis = "未发现需要暂停的主线漂移。"
        better = "继续当前主线,保留常规 gate 和 periodic spine review。"
    alternatives = [
        {
            "option": "continue with guard",
            "decision": "selected" if verdict == "continue" else "rejected",
            "reason": "仅当 runtime 和 evidence 都足够健康时才适合继续。",
        },
        {
            "option": "amend task",
            "decision": "selected" if verdict == "correct_task" else "candidate",
            "reason": "当 drift 主要来自 contract/evidence debt 时成本最低。",
        },
        {
            "option": "repair harness first",
            "decision": "selected" if verdict == "pause_and_repair_harness" else "candidate",
            "reason": "当 runtime fault 影响可信派发时优先级最高。",
        },
        {
            "option": "defer",
            "decision": "candidate",
            "reason": "当问题无法复现或不属于当前阶段时可延后。",
        },
    ]
    return {
        "schema_version": REFLECTION_SCHEMA_VERSION,
        "root_cause_hypothesis": hypothesis,
        "alternatives": alternatives,
        "selected_alternative": verdict,
        "rejected_alternatives": [
            item for item in alternatives if item.get("decision") == "rejected"
        ],
        "better_solution": better,
        "verify": selected.get("verify") or "step -> verify: 下一次 review verdict 仍为 continue 或问题消失。",
        "rollback_condition": "如果复现证据不成立,撤销对应 proposal 并降级为 observation。",
        "drift": drift,
        "previous_reflections": previous_reflections,
        "history_judgment": _history_judgment(
            previous_reflections=previous_reflections,
            verdict=verdict,
        ),
    }


def _previous_reflections(
    *,
    state_dir: Path,
    project_id_value: str,
    current_review_id: str,
    drift: list[str],
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    """Find the newest prior reflection artifacts with the same drift taxonomy."""
    if not drift:
        return []
    current_drift = sorted(str(item) for item in drift)
    matches: list[dict[str, Any]] = []
    for event in reversed(events):
        if event.type != ARTIFACT_EVENT:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        previous_review_id = str(payload.get("review_id") or "")
        if not previous_review_id or previous_review_id == current_review_id:
            continue
        pid = str(payload.get("project_id") or "")
        if pid and pid != project_id_value:
            continue
        previous_drift = sorted(str(item) for item in payload.get("drift") or [])
        if previous_drift != current_drift:
            continue
        artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
        reflection_ref = _artifact_by_kind(artifacts, "reflection")
        report_ref = _artifact_by_kind(artifacts, "report")
        previous_reflection = _read_artifact_json(state_dir, reflection_ref)
        matches.append({
            "review_id": previous_review_id,
            "artifact_event_id": event.id,
            "artifact_event_ts": event.ts,
            "verdict": str(payload.get("verdict") or ""),
            "drift": previous_drift,
            "reflection_ref": reflection_ref,
            "report_ref": report_ref,
            "previous_selected_alternative": str(
                previous_reflection.get("selected_alternative") or ""
            ),
            "previous_better_solution": str(
                previous_reflection.get("better_solution") or ""
            ),
            "relationship": "same_drift_taxonomy",
        })
        if len(matches) >= 3:
            break
    return matches


def _artifact_by_kind(artifacts: list[Any], kind: str) -> dict[str, Any]:
    for item in artifacts:
        if isinstance(item, dict) and str(item.get("kind") or "") == kind:
            return dict(item)
    return {}


def _read_artifact_json(state_dir: Path, artifact: dict[str, Any]) -> dict[str, Any]:
    raw_path = str(artifact.get("path") or "")
    if not raw_path:
        return {}
    path = Path(raw_path)
    if not path.is_absolute():
        path = state_dir / path
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _history_judgment(
    *,
    previous_reflections: list[dict[str, Any]],
    verdict: str,
) -> str:
    if not previous_reflections:
        return "no_prior_same_drift"
    previous_verdict = str(previous_reflections[0].get("verdict") or "")
    if previous_verdict == verdict:
        return "reuse_previous_judgment"
    return "revise_previous_judgment"


def _confidence(
    verdict: str,
    runtime_spine: dict[str, Any],
    delivery_spine: dict[str, Any],
) -> str:
    if verdict == "pause_and_repair_harness" and runtime_spine.get("fault_events"):
        return "high"
    if delivery_spine.get("partial_tasks"):
        return "medium"
    return "medium" if verdict != "continue" else "low"


def _canonical_design_refs(project_root: Path) -> list[str]:
    candidates: list[Path] = []
    docs = project_root / "docs"
    if docs.exists():
        patterns = (
            "*spec*.md",
            "*plan*.md",
            "*design*.md",
            "adr/*.md",
            "design/*.md",
        )
        for pattern in patterns:
            candidates.extend(docs.glob(pattern))
    root_patterns = ("*spec*.md", "*plan*.md", "README.md", "AGENTS.md")
    for pattern in root_patterns:
        candidates.extend(project_root.glob(pattern))
    refs: list[str] = []
    seen: set[str] = set()
    for path in sorted(candidates):
        if not path.is_file():
            continue
        try:
            rel = str(path.relative_to(project_root))
        except ValueError:
            rel = str(path)
        if rel in seen:
            continue
        seen.add(rel)
        refs.append(rel)
        if len(refs) >= 20:
            break
    return refs


def _should_audit(task: Task) -> bool:
    return task.status in {"in_progress", "review", "test", "judge", "done"}


def _audit_task(task_id: str, events: Iterable[ZfEvent]) -> dict[str, Any]:
    task_events = [event for event in events if event.task_id == task_id]
    seen = {event.type: event for event in task_events}
    covered = [
        {"type": event_type, "event_id": seen[event_type].id, "ts": seen[event_type].ts}
        for event_type in WORKFLOW_REQUIRED_EVENTS
        if event_type in seen
    ]
    missing = [
        event_type for event_type in WORKFLOW_REQUIRED_EVENTS
        if event_type not in seen
    ]
    status = "complete" if not missing else "partial"
    return {
        "task_id": task_id,
        "status": "no_events" if not task_events else status,
        "evidence_completeness": (
            len(covered) / len(WORKFLOW_REQUIRED_EVENTS)
            if WORKFLOW_REQUIRED_EVENTS else 1.0
        ),
        "covered_events": covered,
        "missing_events": [] if not task_events else missing,
        "stage_order_violations": [],
    }


def _worker_summary(state_dir: Path, events: list[ZfEvent]) -> dict[str, Any]:
    state_by_actor: dict[str, str] = {}
    for event in events:
        if event.type == "worker.state.changed" and event.actor:
            payload = event.payload if isinstance(event.payload, dict) else {}
            state_by_actor[event.actor] = str(payload.get("to") or "unknown")
        elif event.type == "worker.stuck" and event.actor:
            state_by_actor[event.actor] = "stuck"
    role_sessions = _role_sessions(state_dir)
    for instance_id in role_sessions:
        state_by_actor.setdefault(instance_id, "unknown")
    counts = Counter(state_by_actor.values())
    return {
        "total": len(state_by_actor),
        "states": dict(counts),
        "stuck": counts.get("stuck", 0),
        "workers": [
            {"instance_id": key, "state": value}
            for key, value in sorted(state_by_actor.items())
        ],
    }


def _role_sessions(state_dir: Path) -> dict[str, Any]:
    path = state_dir / "role_sessions.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("sessions"), dict):
        return data["sessions"]
    if isinstance(data, dict):
        return data
    return {}


def _event_ref(event: ZfEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "type": event.type,
        "ts": event.ts,
        "task_id": event.task_id or "",
        "actor": event.actor or "",
    }
