"""Stage success-criteria evaluation for fanout aggregates."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from zf.core.config.schema import WorkflowStageConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.runtime.long_horizon import SuccessCriterion, evaluate_success_criteria


@dataclass(frozen=True)
class FanoutStageCriteriaFailure:
    artifact_payload: dict[str, Any]
    findings: list[dict[str, Any]]


def evaluate_fanout_stage_success_criteria(
    *,
    config: ZfConfig,
    state_dir: Path,
    project_root: Path,
    event_log: EventLog,
    manifest: dict[str, Any],
    artifact_payload: dict[str, Any],
) -> FanoutStageCriteriaFailure | None:
    stage = _stage_by_id(config, str(manifest.get("stage_id") or ""))
    if stage is None:
        return None
    criteria = [
        criterion
        for item in stage.criteria.success_criteria
        if (criterion := SuccessCriterion.from_obj(item)) is not None
    ]
    if not criteria:
        return None
    task = Task(
        id=str(manifest.get("stage_id") or manifest.get("fanout_id") or "fanout-stage"),
        title=f"fanout stage {manifest.get('stage_id') or ''}".strip(),
        contract=TaskContract(),
    )
    root = _stage_project_root(
        state_dir=state_dir,
        project_root=project_root,
        manifest=manifest,
        artifact_payload=artifact_payload,
    )
    sync = _sync_candidate_worktree_head(
        state_dir=state_dir,
        project_root=project_root,
        event_log=event_log,
        manifest=manifest,
        artifact_payload=artifact_payload,
        candidate_root=root,
    )
    if sync.get("status") == "dirty_stale":
        finding = {
            "finding_id": "stage-success-criteria-candidate-worktree-stale",
            "severity": "high",
            "category": "candidate_worktree_stale",
            "message": "candidate worktree HEAD is stale and dirty; refusing to run gate on old files",
            "evidence_refs": [str(root)],
            "target_ref_head": sync.get("target_ref_head", ""),
            "worktree_head": sync.get("worktree_head", ""),
            "dirty_paths": sync.get("dirty_paths", []),
        }
        payload = dict(artifact_payload)
        payload["stage_success_criteria"] = {
            "passed": False,
            "project_root": str(root),
            "failed": [finding],
            **sync,
        }
        existing = payload.get("findings") if isinstance(payload.get("findings"), list) else []
        payload["findings"] = [*existing, finding]
        return FanoutStageCriteriaFailure(artifact_payload=payload, findings=[finding])
    criteria = _criteria_with_project_gate_config(
        criteria,
        candidate_root=root,
        project_root=project_root,
    )
    results = evaluate_success_criteria(
        criteria,
        task=task,
        state_dir=state_dir,
        events=event_log.read_all(),
        project_root=root,
    )
    failed = [result for result in results if not result.passed]
    if not failed:
        return None
    findings = [
        {
            "finding_id": f"stage-success-criteria-{index}",
            "severity": "high",
            "category": "stage_success_criteria",
            "message": result.reason,
            "evidence_refs": list(result.evidence_refs),
            "criterion": asdict(result.criterion),
        }
        for index, result in enumerate(failed, start=1)
    ]
    payload = dict(artifact_payload)
    payload["stage_success_criteria"] = {
        "passed": False,
        "project_root": str(root),
        "failed": findings,
        **sync,
    }
    existing = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    payload["findings"] = [*existing, *findings]
    return FanoutStageCriteriaFailure(artifact_payload=payload, findings=findings)


def evaluate_fanout_stage_success_criteria_for_orchestrator(
    orchestrator: Any,
    *,
    manifest: dict[str, Any],
    artifact_payload: dict[str, Any],
) -> FanoutStageCriteriaFailure | None:
    return evaluate_fanout_stage_success_criteria(
        config=orchestrator.config,
        state_dir=orchestrator.state_dir,
        project_root=orchestrator.project_root,
        event_log=orchestrator.event_log,
        manifest=manifest,
        artifact_payload=artifact_payload,
    )


def _stage_by_id(config: ZfConfig, stage_id: str) -> WorkflowStageConfig | None:
    if not stage_id:
        return None
    for stage in config.workflow.stages:
        if stage.id == stage_id:
            return stage
    return None


def _stage_project_root(
    *,
    state_dir: Path,
    project_root: Path,
    manifest: dict[str, Any],
    artifact_payload: dict[str, Any],
) -> Path:
    for key in ("candidate_root", "worktree_path", "workspace_path", "project_root"):
        value = str(artifact_payload.get(key) or manifest.get(key) or "").strip()
        if value and Path(value).exists():
            return Path(value).resolve()
    pdd_id = str(artifact_payload.get("pdd_id") or manifest.get("pdd_id") or "").strip()
    if pdd_id:
        candidate = state_dir / "candidates" / pdd_id / "worktree"
        if candidate.exists():
            return candidate.resolve()
    return project_root.resolve()


def _sync_candidate_worktree_head(
    *,
    state_dir: Path,
    project_root: Path,
    event_log: EventLog,
    manifest: dict[str, Any],
    artifact_payload: dict[str, Any],
    candidate_root: Path,
) -> dict[str, Any]:
    pdd_id = str(artifact_payload.get("pdd_id") or manifest.get("pdd_id") or "").strip()
    expected_candidate = (state_dir / "candidates" / pdd_id / "worktree").resolve() if pdd_id else None
    if expected_candidate is None or candidate_root.resolve() != expected_candidate:
        return {}
    if not (candidate_root / ".git").exists():
        return {}
    target_ref = str(
        artifact_payload.get("target_ref")
        or artifact_payload.get("candidate_ref")
        or manifest.get("target_ref")
        or manifest.get("candidate_ref")
        or ""
    ).strip()
    if not target_ref:
        return {}
    target_head = _git(project_root, "rev-parse", "--verify", f"{target_ref}^{{commit}}")
    worktree_head = _git(candidate_root, "rev-parse", "HEAD")
    if not target_head or not worktree_head or target_head == worktree_head:
        return {
            "target_ref": target_ref,
            "target_ref_head": target_head,
            "worktree_head": worktree_head,
            "worktree_synced": False,
            "status": "current" if target_head and target_head == worktree_head else "unknown",
        }
    status = _git(candidate_root, "status", "--porcelain")
    dirty_paths = [line.strip() for line in status.splitlines() if line.strip()]
    if dirty_paths:
        payload = {
            "schema_version": "candidate-worktree-stale.v1",
            "pdd_id": pdd_id,
            "candidate_root": str(candidate_root),
            "target_ref": target_ref,
            "target_ref_head": target_head,
            "worktree_head": worktree_head,
            "dirty_paths": dirty_paths,
            "recommended_action": "sync_candidate_worktree_after_preserving_dirty_state",
        }
        try:
            EventWriter(event_log).append(ZfEvent(
                type="candidate.worktree.stale",
                actor="zf-cli",
                payload=payload,
                correlation_id=str(manifest.get("trace_id") or "") or None,
            ))
        except Exception:
            pass
        return {
            **payload,
            "status": "dirty_stale",
            "worktree_synced": False,
        }
    _git(candidate_root, "reset", "--hard", target_head)
    _git(candidate_root, "clean", "-fd")
    return {
        "target_ref": target_ref,
        "target_ref_head": target_head,
        "worktree_head": worktree_head,
        "worktree_synced": True,
        "synced_to_head": target_head,
        "status": "synced",
    }


def _git(cwd: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            text=True,
            capture_output=True,
            check=True,
        )
        return proc.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _criteria_with_project_gate_config(
    criteria: list[SuccessCriterion],
    *,
    candidate_root: Path,
    project_root: Path,
) -> list[SuccessCriterion]:
    """Load fixed artifact-matrix gate configs from project root when needed.

    The matrix/artifact paths are evaluated against the candidate worktree, but
    the gate config itself is harness configuration and can legitimately live
    beside zf.yaml. Prefer a candidate-local config when it exists; otherwise
    inline the project-root config before calling the generic evaluator.
    """
    out: list[SuccessCriterion] = []
    for criterion in criteria:
        if criterion.kind not in {
            "artifact_matrix_gate",
            "candidate_artifact_matrix_gate",
        }:
            out.append(criterion)
            continue
        params = dict(criterion.params or {})
        ref = str(params.get("config_ref") or params.get("gate_config_ref") or "").strip()
        if not ref or _dynamic_or_external_ref(ref):
            out.append(criterion)
            continue
        if (candidate_root / ref).exists() or not (project_root / ref).exists():
            out.append(criterion)
            continue
        try:
            loaded = json.loads((project_root / ref).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            out.append(criterion)
            continue
        if not isinstance(loaded, dict):
            out.append(criterion)
            continue
        merged = dict(loaded)
        merged.update(params)
        merged.pop("config_ref", None)
        merged.pop("gate_config_ref", None)
        out.append(replace(criterion, params=merged))
    return out


def _dynamic_or_external_ref(ref: str) -> bool:
    if "${" in ref or "$" in ref:
        return True
    path = Path(ref)
    return path.is_absolute() or ref.startswith(("~", ".."))
