"""Deterministic routing metadata for task-level semantic replans."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore


SEMANTIC_REPLAN_ACTION = "semantic-replan-request"
SEMANTIC_REPLAN_SAFE_ACTION = "request_semantic_replan"
_SEMANTIC_REPLAN_OUTPUT_PROFILE_ID = "global-rescan"
_SEMANTIC_REPLAN_OUTPUT_PROFILE_REVISION = "1"

_PREFERRED_TRIGGER_ORDER = (
    "flow.discovery.requested",
    "verify.parity_scan.requested",
)
_SEMANTIC_REPLAN_SKILLS = {
    "zf-gap-task-synth",
}
_ANCHOR_EVENT_TYPES = {
    "task_map.ready",
    "task_map.amended",
    "task_map.admitted",
    "product_delivery.task_map.adopted",
    "candidate.ready",
    "fanout.started",
}
_TASK_MAP_ANCHOR_EVENT_TYPES = {
    "task_map.ready",
    "task_map.amended",
    "task_map.admitted",
    "product_delivery.task_map.adopted",
}
_TASK_MAP_ANCHOR_KEYS = {
    "task_map_ref",
    "source_index_ref",
    "task_map_generation",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
}
_ANCHOR_KEYS = (
    "workflow_run_id",
    "task_map_ref",
    "source_index_ref",
    "source_commit",
    "candidate_base_commit",
    "candidate_ref",
    "candidate_head_commit",
    "target_ref",
    "task_map_generation",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
)
_TASK_HANDOFF_KEYS = (
    "workflow_run_id",
    "task_ref",
    "base_commit",
    "contract_snapshot_ref",
    "contract_snapshot_digest",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
    "workflow_proposal_ref",
    "workflow_proposal_digest",
    "effective_config_ref",
    "effective_config_digest",
    "run_contract_ref",
    "run_contract_digest",
    "goal_claim_set_ref",
    "goal_claim_set_digest",
)
_PARTIAL_CONTINUATION_KEYS = (
    "partial_head_commit",
    "partial_source_branch",
    "partial_worktree_clean",
    "partial_dirty_files",
    "continuation_commit",
    "continuation_ref",
)


@dataclass(frozen=True)
class SemanticReplanRoute:
    trigger_event: str
    stage_id: str
    role: str
    flow_kind: str = ""


def _action_failure_event_ids(action: dict[str, Any]) -> set[str]:
    raw = action.get("failure_event_ids")
    values = list(raw) if isinstance(raw, (list, tuple, set)) else [raw]
    values.append(action.get("source_event_id"))
    return {str(value) for value in values if value}


def resolve_semantic_replan_route(config: ZfConfig) -> SemanticReplanRoute | None:
    """Find the declared gap-planning stage without hard-coding flow kinds."""

    stages = list(getattr(config.workflow, "stages", []) or [])
    for trigger in _PREFERRED_TRIGGER_ORDER:
        for stage in stages:
            if str(getattr(stage, "trigger", "") or "") == trigger:
                roles = list(getattr(stage, "roles", []) or [])
                return SemanticReplanRoute(
                    trigger_event=trigger,
                    stage_id=str(getattr(stage, "id", "") or ""),
                    role=str(roles[0] if roles else ""),
                    flow_kind=str(getattr(stage, "flow_kind", "") or ""),
                )

    skills_by_role = {
        str(getattr(role, "name", "") or ""): set(
            str(value) for value in getattr(role, "skills", []) or []
        )
        for role in getattr(config, "roles", []) or []
    }
    for stage in stages:
        roles = list(getattr(stage, "roles", []) or [])
        for role in roles:
            if skills_by_role.get(str(role), set()) & _SEMANTIC_REPLAN_SKILLS:
                trigger = str(getattr(stage, "trigger", "") or "")
                if trigger:
                    return SemanticReplanRoute(
                        trigger_event=trigger,
                        stage_id=str(getattr(stage, "id", "") or ""),
                        role=str(role),
                        flow_kind=str(getattr(stage, "flow_kind", "") or ""),
                    )
    return None


def enrich_semantic_replan_action(
    action: dict[str, Any],
    *,
    state_dir: Path,
    events: list[ZfEvent],
    config: ZfConfig,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Attach stage and current task-map anchors, or fall back to diagnosis."""

    if str(action.get("action") or "") != SEMANTIC_REPLAN_ACTION:
        return action
    route = resolve_semantic_replan_route(config)
    anchor = _semantic_replan_anchor(
        state_dir,
        events,
        task_id=str(action.get("task_id") or ""),
        failure_event_ids=_action_failure_event_ids(action),
        project_root=project_root,
    )
    if route is None or not anchor.get("task_map_ref") or not anchor.get("pdd_id"):
        reason = "semantic replan requires a declared gap-planning stage and current task-map anchor"
        return {
            **action,
            "action": "diagnose-attention",
            "safe_resume_action": "diagnose_attention",
            "failure_class": "semantic_replan_route_unavailable",
            "action_policy": "needs_diagnosis",
            "intervention_class": "diagnose",
            "summary": reason,
            "expected_downstream_events": [
                "run.manager.autoresearch.requested",
                "run.manager.resident.prompted",
            ],
            "verify_condition": (
                "expected_downstream_event:run.manager.autoresearch.requested,"
                "run.manager.resident.prompted"
            ),
        }
    return {
        **action,
        **anchor,
        "semantic_replan_trigger": route.trigger_event,
        "semantic_replan_stage_id": route.stage_id,
        "semantic_replan_role": route.role,
        "flow_kind": route.flow_kind,
        "stage_id": route.stage_id,
        "output_profile_id": _SEMANTIC_REPLAN_OUTPUT_PROFILE_ID,
        "output_profile_revision": _SEMANTIC_REPLAN_OUTPUT_PROFILE_REVISION,
        "action_policy": "auto_decide",
        "owner_route": "run_manager",
        "intervention_class": "semantic_replan",
        "expected_downstream_events": [route.trigger_event],
        "verify_condition": f"expected_downstream_event:{route.trigger_event}",
    }


def _semantic_replan_anchor(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    task_id: str,
    failure_event_ids: set[str] | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    task = TaskStore(Path(state_dir) / "kanban.json").get(task_id) if task_id else None
    pdd_id = ""
    feature_id = ""
    source_index_ref = ""
    if task is not None:
        feature_id = str(task.contract.feature_id or "")
        pdd_id = feature_id
        source_index_ref = str(task.contract.source_index_ref or "")
        evidence_contract = (
            task.contract.evidence_contract
            if isinstance(task.contract.evidence_contract, dict)
            else {}
        )
        source_refs = (
            evidence_contract.get("source_refs")
            if isinstance(evidence_contract.get("source_refs"), dict)
            else {}
        )
    else:
        evidence_contract = {}
        source_refs = {}
    anchor: dict[str, Any] = {
        "pdd_id": pdd_id,
        "feature_id": feature_id or pdd_id,
        "source_index_ref": source_index_ref,
    }
    for key in (
        "workflow_run_id",
        "task_map_ref",
        "source_index_ref",
        "task_map_generation",
        "plan_artifact_package_id",
        "plan_artifact_package_ref",
        "plan_artifact_package_digest",
    ):
        value = evidence_contract.get(key) or source_refs.get(key)
        if value not in (None, ""):
            anchor[key] = value
    for event in events:
        if event.type not in _ANCHOR_EVENT_TYPES:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_pdd = str(payload.get("pdd_id") or payload.get("feature_id") or "")
        if pdd_id and event_pdd and event_pdd != pdd_id:
            continue
        if not pdd_id and event_pdd:
            pdd_id = event_pdd
            anchor["pdd_id"] = pdd_id
            anchor["feature_id"] = str(payload.get("feature_id") or pdd_id)
        for key in _ANCHOR_KEYS:
            if (
                key in _TASK_MAP_ANCHOR_KEYS
                and event.type not in _TASK_MAP_ANCHOR_EVENT_TYPES
                and anchor.get(key) not in (None, "")
            ):
                # Candidate snapshots may finish after a newer task map was
                # admitted. They own candidate identity, not task-map lineage.
                continue
            value = payload.get(key)
            if value not in (None, ""):
                anchor[key] = value
        if event.type == "candidate.ready" and str(
            payload.get("candidate_head_commit") or ""
        ).strip():
            anchor["candidate_event_id"] = event.id
    if pdd_id and not anchor.get("task_map_ref"):
        fallback = Path(state_dir) / "artifacts" / pdd_id / "task_map.json"
        if fallback.exists():
            anchor["task_map_ref"] = str(fallback)
    if task is not None:
        from zf.runtime.task_contract_snapshot import (
            TaskContractSnapshotError,
            current_task_contract_identity,
        )

        try:
            identity = current_task_contract_identity(
                task,
                task_map_ref=str(anchor.get("task_map_ref") or ""),
            )
        except TaskContractSnapshotError:
            identity = {}
        if identity:
            for event in events:
                payload = event.payload if isinstance(event.payload, dict) else {}
                event_task_id = str(event.task_id or payload.get("task_id") or "")
                if event_task_id != task_id:
                    continue
                incoming_revision = str(payload.get("contract_revision") or "")
                incoming_generation = str(payload.get("task_map_generation") or "")
                if (
                    incoming_revision
                    and incoming_revision != identity["contract_revision"]
                ):
                    continue
                if (
                    incoming_generation
                    and incoming_generation != identity["task_map_generation"]
                ):
                    continue
                for key in _TASK_HANDOFF_KEYS:
                    value = payload.get(key)
                    if value not in (None, "", [], {}):
                        anchor[key] = value
            anchor.update(identity)
            for event in reversed(events):
                if event.type != "fanout.child.failed":
                    continue
                if failure_event_ids and not (
                    event.id in failure_event_ids
                    or str(event.causation_id or "") in failure_event_ids
                ):
                    continue
                payload = event.payload if isinstance(event.payload, dict) else {}
                event_task_id = str(event.task_id or payload.get("task_id") or "")
                if event_task_id != task_id:
                    continue
                incoming_revision = str(payload.get("contract_revision") or "")
                incoming_generation = str(payload.get("task_map_generation") or "")
                if (
                    incoming_revision
                    and incoming_revision != identity["contract_revision"]
                ):
                    continue
                if (
                    incoming_generation
                    and incoming_generation != identity["task_map_generation"]
                ):
                    continue
                continuation_commit = str(
                    payload.get("continuation_commit")
                    or payload.get("partial_head_commit")
                    or ""
                ).strip()
                if not continuation_commit:
                    continue
                previous_candidate_ref = str(anchor.get("candidate_ref") or "")
                previous_candidate_head = str(
                    anchor.get("candidate_head_commit") or ""
                )
                failed_base_commit = str(payload.get("base_commit") or "").strip()
                if continuation_commit in {
                    previous_candidate_head,
                    failed_base_commit,
                }:
                    continue
                for key in _PARTIAL_CONTINUATION_KEYS:
                    value = payload.get(key)
                    if value not in (None, "", [], {}):
                        anchor[key] = value
                continuation_ref = str(
                    payload.get("continuation_ref")
                    or payload.get("partial_source_branch")
                    or payload.get("source_branch")
                    or continuation_commit
                ).strip()
                anchor.update({
                    "handoff_kind": "partial_task_continuation",
                    "continuation_commit": continuation_commit,
                    "continuation_ref": continuation_ref,
                    "continuation_source_event_id": event.id,
                    "previous_candidate_ref": previous_candidate_ref,
                    "previous_candidate_head_commit": previous_candidate_head,
                    "candidate_ref": continuation_ref,
                    "candidate_head_commit": continuation_commit,
                    "target_ref": continuation_commit,
                })
                break
    if (
        not anchor.get("continuation_commit")
        and anchor.get("candidate_head_commit")
        and anchor.get("candidate_ref")
    ):
        # Parity discovery must inspect the delivered candidate. The task-map
        # baseline remains source_commit/candidate_base_commit provenance.
        candidate_head = str(anchor.get("candidate_head_commit") or "").strip()
        failed_base = str(anchor.get("base_commit") or "").strip()
        candidate_contains_base = _git_commit_is_ancestor(
            project_root or Path(state_dir).parent,
            ancestor=failed_base,
            descendant=candidate_head,
        )
        if failed_base and candidate_contains_base is False:
            # A task may have started from a newer lane continuation than the
            # last integrated candidate. Inspecting that stale candidate would
            # rediscover already-delivered gaps and discard the task base.
            anchor["previous_candidate_ref"] = str(anchor["candidate_ref"])
            anchor["previous_candidate_head_commit"] = candidate_head
            anchor["target_ref"] = failed_base
            anchor["handoff_kind"] = "task_base_recovery"
        else:
            anchor["target_ref"] = anchor["candidate_ref"]
    elif not anchor.get("continuation_commit"):
        # A task-scoped replan can happen before the writer fanout has emitted
        # candidate.ready. The task snapshot still pins the integrated task
        # base; inspect that immutable commit instead of falling back to the
        # plan-stage HEAD and rediscovering already accepted slices.
        failed_base = str(anchor.get("base_commit") or "").strip()
        if failed_base:
            anchor["target_ref"] = failed_base
            anchor["handoff_kind"] = "task_base_recovery"
    anchor["supersedes_task_ids"] = [task_id] if task_id else []
    anchor["affected_task_ids"] = [task_id] if task_id else []
    anchor.update(_admitted_requirement_identity(state_dir, anchor))
    return anchor


def _admitted_requirement_identity(state_dir: Path, anchor: dict[str, Any]) -> dict[str, str]:
    """Recover the requirement pinned by this Run's admitted proposal."""
    from zf.runtime.run_contract import load_run_contract
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    contract = load_run_contract(state_dir) or {}
    workflow = contract.get("workflow")
    if not isinstance(workflow, dict) or not isinstance(workflow.get("proposal_ref"), dict):
        return {}
    try:
        hydrated = hydrate_sidecar_ref(state_dir, workflow["proposal_ref"])
    except (OSError, ValueError):
        return {}
    proposal = hydrated.payload if hydrated.ok else None
    if not isinstance(proposal, dict) or not anchor.get("workflow_run_id"):
        return {}
    if str(proposal.get("request_id") or "") != str(anchor["workflow_run_id"]):
        return {}
    ref = str(proposal.get("requirement_spec_ref") or "")
    digest = str(proposal.get("requirement_spec_digest") or "")
    return {"requirement_spec_ref": ref, "requirement_spec_digest": digest} if ref and digest else {}


def _git_commit_is_ancestor(
    project_root: Path,
    *,
    ancestor: str,
    descendant: str,
) -> bool | None:
    if not ancestor or not descendant:
        return None
    if ancestor == descendant:
        return True
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


__all__ = [
    "SEMANTIC_REPLAN_ACTION",
    "SEMANTIC_REPLAN_SAFE_ACTION",
    "SemanticReplanRoute",
    "enrich_semantic_replan_action",
    "resolve_semantic_replan_route",
]
