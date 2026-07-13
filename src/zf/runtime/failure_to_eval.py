"""Failure-to-eval candidate generation.

This module turns observed workflow/runtime failures into reviewable artifacts:
backlog drafts, eval fixtures, or skill-candidate notes. It is intentionally
non-mutating with respect to kernel truth.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.runtime.event_problem_registry import looks_actionable_event, spec_for_event


FAILURE_TO_EVAL_EVENT_TYPES = frozenset({
    "flow.goal.blocked",
    "flow.preflight.blocked",
    "gate.failed",
    "supervisor.attention.opened",
    "supervisor.projection.stale",
    "autoresearch.repair.dispatch_blocked",
    "autoresearch.repair.closeout.required",
    "candidate.integration.failed",
    "human.escalate",
    "orchestrator.tick.failed",
})

# ZF-E2E-PRDCTL-P0-3 (2026-07-12): Run Manager events about its own actions
# must not become failure candidates — that files "RM diagnoses RM" work for
# every honest no-op verify.failed (live: 2 self-referential candidates per
# round). RM machinery health belongs to the watchdog channel; excluded
# events are tallied into projections/rm_health.json instead.
_RM_SELF_EVENT_PREFIX = "run.manager."


def failure_candidate_from_event(
    event: Any,
    *,
    source: str = "run_manager",
) -> dict[str, Any]:
    event_type = str(getattr(event, "type", "") or "")
    event_id = str(getattr(event, "id", "") or "")
    payload = getattr(event, "payload", None)
    payload = payload if isinstance(payload, dict) else {}
    spec = spec_for_event(event_type)
    failure_id = _failure_id(event_type, event_id, payload)
    evidence_refs = _evidence_refs(payload)
    return {
        "schema_version": "failure-candidate.v1",
        "failure_id": failure_id,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event": {
            "id": event_id,
            "type": event_type,
            "task_id": str(getattr(event, "task_id", "") or payload.get("task_id") or ""),
            "correlation_id": str(getattr(event, "correlation_id", "") or ""),
        },
        "classification": {
            "event_class": getattr(spec, "event_class", "") if spec is not None else "",
            "problem_class": getattr(spec, "problem_class", "") if spec is not None else "unknown",
            "failure_class": (
                getattr(spec, "failure_class", "") if spec is not None else event_type.replace(".", "_")
            ),
            "owner_route": getattr(spec, "owner_route", "") if spec is not None else "run_manager",
            "action_policy": getattr(spec, "action_policy", "") if spec is not None else "needs_diagnosis",
        },
        "summary": _summary(event_type, payload),
        "evidence_refs": evidence_refs,
        "payload_excerpt": _compact_payload(payload),
    }


def write_failure_candidate(
    state_dir: Path,
    candidate: Mapping[str, Any],
) -> Path:
    failure_id = str(candidate.get("failure_id") or "failure")
    path = state_dir.expanduser() / "failure-candidates" / f"{failure_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(candidate), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def materialize_failure_candidates_from_events(
    state_dir: Path,
    events: list[Any],
    *,
    source: str = "runtime_tick",
    limit: int = 50,
) -> list[Path]:
    """Write missing failure candidates for recent actionable failures.

    This is a rebuildable projection under ``failure-candidates/``. It does not
    change kanban/task truth and is safe to run every tick.
    """
    written: list[Path] = []
    seen = _existing_failure_ids(state_dir)
    rm_self_events: list[Any] = []
    for event in list(events)[-limit:]:
        event_type = str(getattr(event, "type", "") or "")
        if event_type.startswith(_RM_SELF_EVENT_PREFIX) and (
            event_type.endswith(".failed") or event_type.endswith(".blocked")
        ):
            rm_self_events.append(event)
        if not _should_materialize_event(event_type):
            continue
        candidate = failure_candidate_from_event(event, source=source)
        failure_id = str(candidate.get("failure_id") or "")
        if not failure_id or failure_id in seen:
            continue
        path = write_failure_candidate(state_dir, candidate)
        seen.add(failure_id)
        written.append(path)
    if rm_self_events:
        _record_rm_health_events(state_dir, rm_self_events)
    return written


def _record_rm_health_events(state_dir: Path, events: list[Any]) -> None:
    """Tally excluded RM self-referential failures (rebuildable projection)."""
    path = state_dir.expanduser() / "projections" / "rm_health.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    seen_ids = [str(v) for v in data.get("seen_ids") or []]
    recent = [item for item in data.get("recent") or [] if isinstance(item, dict)]
    for event in events:
        event_id = str(getattr(event, "id", "") or "")
        if event_id and event_id in seen_ids:
            continue
        event_type = str(getattr(event, "type", "") or "")
        counts[event_type] = int(counts.get(event_type) or 0) + 1
        if event_id:
            seen_ids.append(event_id)
        recent.append({
            "id": event_id,
            "type": event_type,
            "ts": str(getattr(event, "ts", "") or ""),
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "rm-health.v1",
        "is_derived_projection": True,
        "counts": counts,
        "seen_ids": seen_ids[-200:],
        "recent": recent[-20:],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def materialize_failure_closeout(
    state_dir: Path,
    *,
    output_root: Path | None = None,
    kinds: Sequence[str] = ("backlog", "eval", "skill"),
    candidate_refs: Sequence[Path] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Materialize failure candidates into reviewable drafts plus a manifest.

    The closeout manifest is a rebuildable artifact. It does not mutate kernel
    truth; operators or Run Manager can decide whether a generated draft should
    become an active task, eval, or skill.
    """
    state_dir = state_dir.expanduser()
    output_root = (output_root or state_dir / "failure-closeout").expanduser()
    requested_kinds = [_normalize_kind(item) for item in kinds if str(item).strip()]
    if not requested_kinds:
        requested_kinds = ["backlog"]
    invalid = [item for item in requested_kinds if item not in {"backlog", "eval", "skill", "waive"}]
    if invalid:
        raise ValueError(f"unsupported failure closeout kind(s): {', '.join(sorted(set(invalid)))}")
    paths = list(candidate_refs) if candidate_refs is not None else _candidate_paths(state_dir)
    if limit is not None:
        paths = paths[:max(0, int(limit))]
    output_root.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for candidate_ref in paths:
        candidate_ref = candidate_ref.expanduser()
        if not candidate_ref.exists():
            continue
        try:
            candidate = json.loads(candidate_ref.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(candidate, dict):
            continue
        outputs: dict[str, str] = {}
        for kind in requested_kinds:
            out_dir = _closeout_kind_dir(output_root, kind)
            outputs[kind] = str(
                materialize_failure_candidate(
                    candidate_ref,
                    output_dir=out_dir,
                    kind=kind,
                )
            )
        items.append({
            "failure_id": str(candidate.get("failure_id") or candidate_ref.stem),
            "candidate_ref": str(candidate_ref),
            "event": candidate.get("event") if isinstance(candidate.get("event"), dict) else {},
            "classification": (
                candidate.get("classification")
                if isinstance(candidate.get("classification"), dict)
                else {}
            ),
            "outputs": outputs,
        })
    manifest = {
        "schema_version": "failure-closeout.v1",
        "status": "ready" if items else "empty",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state_dir": str(state_dir),
        "output_root": str(output_root),
        "requested_kinds": requested_kinds,
        "candidate_count": len(paths),
        "materialized_count": len(items),
        "items": items,
    }
    manifest_ref = output_root / "failure-closeout-manifest.json"
    manifest["manifest_ref"] = str(manifest_ref)
    manifest_ref.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def promote_failure_closeout_backlogs(
    manifest_ref: Path,
    *,
    project_root: Path,
    approval_ref: str,
    output_dir: Path | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Promote closeout backlog drafts into ``tasks/active``.

    This is intentionally an operator-approved source-tree action. The failure
    candidates and closeout manifest remain rebuildable artifacts; only an
    explicit approval reference may turn their backlog drafts into active sprint
    tasks.
    """
    approval_ref = str(approval_ref or "").strip()
    if not approval_ref:
        raise ValueError("approval_ref is required to promote failure closeout backlogs")
    root = project_root.expanduser().resolve(strict=False)
    manifest_path = _resolve_project_ref(root, manifest_ref)
    if not manifest_path.exists():
        raise FileNotFoundError(f"failure closeout manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("failure closeout manifest must be a JSON object")
    items = manifest.get("items")
    if not isinstance(items, list):
        items = []
    if limit is not None:
        items = items[:max(0, int(limit))]
    active_dir = _resolve_project_ref(root, output_dir or Path("tasks/active"))
    _require_under_project(root, active_dir)
    active_dir.mkdir(parents=True, exist_ok=True)
    report_dir = root / "artifacts" / "failure-closeout"
    report_dir.mkdir(parents=True, exist_ok=True)
    promoted: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        outputs = item.get("outputs")
        outputs = outputs if isinstance(outputs, dict) else {}
        draft_raw = str(outputs.get("backlog") or "").strip()
        if not draft_raw:
            skipped.append({"failure_id": str(item.get("failure_id") or ""), "reason": "missing backlog output"})
            continue
        draft_path = _resolve_project_ref(root, Path(draft_raw))
        if not draft_path.exists():
            skipped.append({
                "failure_id": str(item.get("failure_id") or ""),
                "reason": f"backlog draft missing: {draft_raw}",
            })
            continue
        destination = _unique_destination(active_dir, draft_path.name)
        text = draft_path.read_text(encoding="utf-8")
        destination.write_text(
            _activate_backlog_text(
                text,
                approval_ref=approval_ref,
                manifest_ref=str(manifest_path),
                draft_ref=str(draft_path),
                failure_id=str(item.get("failure_id") or draft_path.stem),
            ),
            encoding="utf-8",
        )
        promoted.append({
            "failure_id": str(item.get("failure_id") or draft_path.stem),
            "draft_ref": str(draft_path),
            "task_ref": str(destination),
        })
    report_ref = report_dir / "failure-closeout-promotion-report.json"
    result = {
        "schema_version": "failure-closeout-promotion.v1",
        "status": "promoted" if promoted else "empty",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "approval_ref": approval_ref,
        "manifest_ref": str(manifest_path),
        "output_dir": str(active_dir),
        "report_ref": str(report_ref),
        "promoted_count": len(promoted),
        "skipped_count": len(skipped),
        "promoted": promoted,
        "skipped": skipped,
    }
    report_ref.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def materialize_failure_candidate(
    candidate_ref: Path,
    *,
    output_dir: Path,
    kind: str = "backlog",
) -> Path:
    candidate = json.loads(candidate_ref.expanduser().read_text(encoding="utf-8"))
    if not isinstance(candidate, dict):
        raise ValueError("failure candidate must be a JSON object")
    output_dir = output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    if kind == "eval":
        path = output_dir / f"{candidate.get('failure_id', 'failure')}.json"
        path.write_text(
            json.dumps({
                "schema_version": "failure-eval-fixture.v1",
                "candidate_ref": str(candidate_ref),
                "candidate": candidate,
            }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path
    if kind == "skill":
        path = output_dir / f"{candidate.get('failure_id', 'failure')}-skill-candidate.md"
        path.write_text(_render_skill_candidate(candidate), encoding="utf-8")
        return path
    if kind == "waive":
        # 131 §16.3-5:waive 也是四选一 closeout 的一种终结——留记录,
        # 不产生后续工作;waive_reason 由 operator/RM 事后补(proposed)。
        path = output_dir / f"{candidate.get('failure_id', 'failure')}-waive.json"
        path.write_text(
            json.dumps({
                "schema_version": "failure-waive.v1",
                "failure_id": str(candidate.get("failure_id") or ""),
                "status": "proposed",
                "waive_reason": "",
                "summary": str(candidate.get("summary") or ""),
            }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path
    path = output_dir / f"{_timestamp()}-{candidate.get('failure_id', 'failure')}.md"
    path.write_text(_render_backlog(candidate), encoding="utf-8")
    return path


def _candidate_paths(state_dir: Path) -> list[Path]:
    root = state_dir.expanduser() / "failure-candidates"
    return sorted(root.glob("*.json"))


def _normalize_kind(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"backlogs", "task", "tasks"}:
        return "backlog"
    if text in {"evals", "evaluation"}:
        return "eval"
    if text in {"skills", "skill-candidate"}:
        return "skill"
    return text


def _closeout_kind_dir(output_root: Path, kind: str) -> Path:
    if kind == "backlog":
        return output_root / "backlogs"
    if kind == "eval":
        return output_root / "evals"
    if kind == "waive":
        return output_root / "waived"
    return output_root / "skills"


def failure_closeout_status(state_dir: Path) -> dict[str, Any]:
    """131 §16.3-5:候选必须终结于 backlog/eval/skill/waive 四选一。

    列出尚无任何 closeout 产物的 open candidates——机械面,谁开着一目
    了然;不 mutate 任何 truth。
    """
    state_dir = state_dir.expanduser()
    manifest_ref = state_dir / "failure-closeout" / "failure-closeout-manifest.json"
    closed: set[str] = set()
    if manifest_ref.exists():
        try:
            manifest = json.loads(manifest_ref.read_text(encoding="utf-8"))
            for item in manifest.get("items") or []:
                if isinstance(item, dict) and item.get("outputs"):
                    closed.add(str(item.get("failure_id") or ""))
        except (OSError, json.JSONDecodeError):
            pass
    open_candidates: list[dict[str, str]] = []
    for path in _candidate_paths(state_dir):
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        failure_id = str(candidate.get("failure_id") or path.stem)
        if failure_id in closed:
            continue
        open_candidates.append({
            "failure_id": failure_id,
            "candidate_ref": str(path),
            "summary": str(candidate.get("summary") or "")[:200],
        })
    return {
        "schema_version": "failure-closeout-status.v1",
        "total_candidates": len(_candidate_paths(state_dir)),
        "closed": len(closed),
        "open": len(open_candidates),
        "open_candidates": open_candidates,
    }


def _failure_id(event_type: str, event_id: str, payload: Mapping[str, Any]) -> str:
    raw = "|".join([
        event_type,
        event_id,
        str(payload.get("checkpoint_id") or ""),
        str(payload.get("task_id") or ""),
        str(payload.get("reason") or payload.get("error") or payload.get("status") or ""),
    ])
    return "fail-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _should_materialize_event(event_type: str) -> bool:
    if event_type.startswith(_RM_SELF_EVENT_PREFIX):
        return False
    if event_type in FAILURE_TO_EVAL_EVENT_TYPES:
        return True
    if event_type.endswith(".failed") or event_type.endswith(".blocked"):
        return True
    # 131-P1-5 unknown→eval 强制链:actionable 形状但 registry 无 spec 的
    # 事件(典型来源:agent 经 zf emit 的自定义类型)不许静默消失,
    # 一律物化为 failure candidate(classification 落 problem_class=unknown)。
    return looks_actionable_event(event_type) and spec_for_event(event_type) is None


def _existing_failure_ids(state_dir: Path) -> set[str]:
    out: set[str] = set()
    root = state_dir.expanduser() / "failure-candidates"
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and str(data.get("failure_id") or "").strip():
            out.add(str(data.get("failure_id")))
    return out


def _evidence_refs(payload: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in (
        "evidence_ref",
        "evidence_refs",
        "artifact_ref",
        "artifact_refs",
        "trace_ref",
        "preflight_ref",
        "workflow_preflight_ref",
    ):
        refs.extend(_string_list(payload.get(key)))
    return list(dict.fromkeys(refs))


def _summary(event_type: str, payload: Mapping[str, Any]) -> str:
    reason = str(payload.get("reason") or payload.get("error") or payload.get("message") or "")
    if reason:
        return f"{event_type}: {reason[:300]}"
    return event_type or "unknown failure"


def _compact_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    allow = (
        "reason",
        "error",
        "status",
        "task_id",
        "stage_id",
        "checkpoint_id",
        "safe_resume_action",
        "failure_class",
        "owner_route",
        "action_policy",
        "verify_condition",
    )
    return {key: payload.get(key) for key in allow if key in payload}


def _render_backlog(candidate: Mapping[str, Any]) -> str:
    classification = candidate.get("classification")
    classification = classification if isinstance(classification, dict) else {}
    event = candidate.get("event")
    event = event if isinstance(event, dict) else {}
    evidence = candidate.get("evidence_refs")
    evidence = evidence if isinstance(evidence, list) else []
    lines = [
        f"# Failure Candidate {candidate.get('failure_id', '')}",
        "",
        "> 状态: proposed",
        "",
        "## 背景",
        "",
        str(candidate.get("summary") or ""),
        "",
        "## 分类",
        "",
        f"- event_type: `{event.get('type', '')}`",
        f"- problem_class: `{classification.get('problem_class', '')}`",
        f"- failure_class: `{classification.get('failure_class', '')}`",
        f"- owner_route: `{classification.get('owner_route', '')}`",
        "",
        "## 证据",
        "",
    ]
    lines.extend([f"- `{ref}`" for ref in evidence] or ["- 暂无外置 evidence ref"])
    lines.extend([
        "",
        "## 验收",
        "",
        "- step: 复现 failure candidate -> verify: 失败事件可被稳定重放或被现有测试覆盖。",
        "- step: 修复或生成 skill/eval -> verify: 对应回归测试通过。",
        "",
    ])
    return "\n".join(lines)


def _render_skill_candidate(candidate: Mapping[str, Any]) -> str:
    return (
        f"# Skill Candidate: {candidate.get('failure_id', '')}\n\n"
        f"> 状态: proposed\n\n"
        "## 触发失败\n\n"
        f"{candidate.get('summary', '')}\n\n"
        "## 待沉淀方法\n\n"
        "- 描述如何在 scan/plan/verify 阶段提前避免该 failure。\n"
    )


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = str(item.get("path") or item.get("ref") or "")
            else:
                text = str(item)
            if text.strip():
                out.append(text.strip())
        return out
    if isinstance(value, dict):
        text = str(value.get("path") or value.get("ref") or "")
        return [text] if text.strip() else []
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _resolve_project_ref(root: Path, value: Path) -> Path:
    path = value.expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve(strict=False)


def _require_under_project(root: Path, path: Path) -> None:
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path must stay under project root: {path}") from exc


def _unique_destination(output_dir: Path, name: str) -> Path:
    stem = Path(name).stem or "failure-closeout"
    suffix = Path(name).suffix or ".md"
    candidate = output_dir / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        candidate = output_dir / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"unable to allocate unique task path for {name}")


def _activate_backlog_text(
    text: str,
    *,
    approval_ref: str,
    manifest_ref: str,
    draft_ref: str,
    failure_id: str,
) -> str:
    active = text.replace("> 状态: proposed", "> 状态: active", 1)
    if active == text and "> 状态:" not in active:
        active = "> 状态: active\n\n" + active
    activation = (
        "\n\n## Activation\n\n"
        f"- approval_ref: `{approval_ref}`\n"
        f"- manifest_ref: `{manifest_ref}`\n"
        f"- draft_ref: `{draft_ref}`\n"
        f"- failure_id: `{failure_id}`\n"
    )
    return active.rstrip() + activation + "\n"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")


__all__ = [
    "FAILURE_TO_EVAL_EVENT_TYPES",
    "failure_candidate_from_event",
    "failure_closeout_status",
    "materialize_failure_closeout",
    "promote_failure_closeout_backlogs",
    "materialize_failure_candidates_from_events",
    "materialize_failure_candidate",
    "write_failure_candidate",
]
