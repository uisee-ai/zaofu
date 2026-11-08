"""Deterministic admission gate for writer fanout dispatch."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.task_map import task_verification_commands
from zf.runtime.task_map_successor import (
    task_map_successor_base_commit,
    task_map_supersedes_task_ids,
)
from zf.runtime.verification_commands import (
    materialize_task_verification_commands,
    task_map_command_registry,
    validation_with_commands,
)
from zf.runtime.task_contract_normalize import (
    canonical_verification_tiers,
    owner_fields_from_task_map_item,
)
from zf.runtime.writer_task_skills import validated_writer_task_skills

TERMINAL_TASK_STATUSES = {"done", "cancelled", "superseded"}


@dataclass(frozen=True)
class LoadedWriterTaskMap:
    task_items: list[dict[str, Any]]
    task_map_ref: str
    task_map_path: Path
    source_index_ref: str = ""
    feature_id: str = ""
    pdd_id: str = ""
    wave: int | None = None
    requested_task_ids: list[str] = field(default_factory=list)
    completed_task_ids: list[str] = field(default_factory=list)
    is_replan: bool = False
    dispatch_base_commit: str = ""
    plan_artifact_package_id: str = ""
    plan_artifact_package_ref: str = ""
    plan_artifact_package_digest: str = ""
    task_map_generation: str = ""
    flow_kind: str = ""
    workflow_run_id: str = ""


@dataclass(frozen=True)
class WriterFanoutAdmission:
    passed: bool
    task_items: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    missing_task_ids: list[str] = field(default_factory=list)
    stale_task_ids: list[str] = field(default_factory=list)
    superseded_task_ids: list[str] = field(default_factory=list)
    terminal_task_ids: list[str] = field(default_factory=list)
    suggested_action: str = ""

    def failure_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reason": self.reason,
            "missing_task_ids": list(self.missing_task_ids),
            "stale_task_ids": list(self.stale_task_ids),
            "superseded_task_ids": list(self.superseded_task_ids),
            "terminal_task_ids": list(self.terminal_task_ids),
        }
        if self.suggested_action:
            payload["suggested_action"] = self.suggested_action
        return payload


class WriterTaskMapPolicyError(ValueError):
    """A parsed task map failed run policy before writer dispatch."""

    def __init__(self, errors: list[str], task_items: list[dict[str, Any]]) -> None:
        super().__init__(
            "writer fanout task_map policy failed: " + "; ".join(errors)
        )
        self.errors = list(errors)
        self.task_items = list(task_items)


def writer_task_dispatch_base_commit(
    task_item: dict[str, Any],
    *,
    fallback: str = "",
) -> str:
    """Resolve the immutable checkout base for one admitted writer task."""

    return (
        task_map_successor_base_commit(task_item)
        or str(task_item.get("dispatch_base_commit") or "").strip()
        or str(fallback or "").strip()
    )


def writer_fanout_dispatch_base_commit(
    task_items: list[dict[str, Any]],
    *,
    fallback: str = "",
) -> str:
    """Resolve the single candidate base shared by a writer fanout."""

    task_bases = list(dict.fromkeys(
        base
        for item in task_items
        if (base := task_map_successor_base_commit(item))
    ))
    if len(task_bases) > 1:
        raise ValueError(
            "writer fanout tasks declare conflicting base_commit values: "
            + ", ".join(task_bases)
        )
    return task_bases[0] if task_bases else str(fallback or "").strip()


def bind_writer_task_dispatch_owner(
    *,
    task: Task,
    role: Any,
    config: Any,
    event_writer: Any,
    task_item: Mapping[str, Any] | None = None,
) -> object:
    """Pin a canonical writer task to the Flow-local lane being dispatched."""

    from zf.runtime.flow_roles import role_configs_for_flow

    contract = deepcopy(task.contract)
    if not str(getattr(task, "contract_authority_revision", "") or ""):
        contract = _bootstrap_legacy_writer_contract(contract, task_item or {})
    evidence = (
        dict(contract.evidence_contract)
        if isinstance(contract.evidence_contract, dict)
        else {}
    )
    flow_kind = str(evidence.get("flow_kind") or "")
    if flow_kind:
        allowed = {
            str(getattr(item, "instance_id", "") or "")
            for item in role_configs_for_flow(config, flow_kind)
        }
        if role.instance_id not in allowed:
            raise RuntimeError(
                "flow_owner_cross_flow: dispatch role "
                f"{role.instance_id!r} is outside Flow {flow_kind!r}"
            )
    contract.owner_role = role.name
    contract.owner_instance = role.instance_id
    contract.evidence_contract = evidence
    return contract


def _bootstrap_legacy_writer_contract(contract: Any, task_item: Mapping[str, Any]) -> Any:
    """Fill a legacy scheduler placeholder from its admitted Task Map once."""

    raw = (
        task_item.get("raw_task")
        if isinstance(task_item.get("raw_task"), Mapping)
        else {}
    )
    payload = (
        task_item.get("payload")
        if isinstance(task_item.get("payload"), Mapping)
        else {}
    )

    def pick(key: str, default: Any = None) -> Any:
        value = task_item.get(key)
        if value in (None, "", [], {}):
            value = raw.get(key)
        return default if value in (None, "", [], {}) else value

    if not contract.behavior:
        contract.behavior = str(
            pick("behavior")
            or payload.get("instruction")
            or pick("summary")
            or ""
        )
    if not contract.scope:
        contract.scope = _string_list(
            pick("allowed_paths") or pick("scope") or []
        )
    if not contract.verification:
        contract.verification = str(pick("verification") or "")
    if not contract.verification_tiers:
        contract.verification_tiers = _string_list(
            pick("verification_tiers") or []
        )
    if not contract.validation:
        contract.validation = dict(pick("validation", {}) or {})
    if not contract.acceptance_criteria:
        contract.acceptance_criteria = _acceptance_criteria_list(
            pick("acceptance_criteria") or pick("acceptance") or []
        )
    if contract.acceptance in {"", "exit_code=0"}:
        acceptance = pick("acceptance")
        if isinstance(acceptance, list):
            contract.acceptance = "\n".join(_string_list(acceptance))
        elif acceptance not in (None, ""):
            contract.acceptance = str(acceptance)
    if not contract.exclusions:
        contract.exclusions = _string_list(pick("exclusions") or [])
    if not contract.explicit_non_goals:
        contract.explicit_non_goals = _string_list(
            pick("explicit_non_goals") or []
        )
    evidence = dict(contract.evidence_contract or {})
    source_refs = (
        dict(evidence.get("source_refs") or {})
        if isinstance(evidence.get("source_refs"), Mapping)
        else {}
    )
    for key in ("task_map_ref", "task_map_generation", "source_index_ref"):
        value = str(task_item.get(key) or raw.get(key) or "").strip()
        if value and not source_refs.get(key):
            source_refs[key] = value
    if source_refs:
        evidence["source_refs"] = source_refs
    contract.evidence_contract = evidence
    return contract


def load_writer_task_map(
    *,
    stage: Any,
    event: ZfEvent,
    pdd_id: str,
    state_dir: Path,
    project_root: Path,
    pipeline_spec: Any = None,
    candidate_quality_source: str = "auto",
    work_units_config: Any = None,
) -> LoadedWriterTaskMap:
    payload = _payload(event)
    event_task_map_ref = str(payload.get("task_map_ref") or "").strip()
    template = event_task_map_ref or str(
        getattr(stage, "task_map", "") or f".zf/artifacts/{pdd_id}/task_map.json"
    )
    rendered = _render_template(template, event)
    if "${" in rendered:
        # R13 fix (backlog 2026-06-06-0401 §C): the config task_map template
        # referenced an event field the trigger did not carry — R13 had one
        # task_map.ready with task_map_ref=None, so the self-referential
        # ``task_map: ${task_map_ref}`` stayed literal and the path 404'd while
        # a sibling fanout (whose event DID carry the ref) succeeded. Fall back
        # to the canonical pdd-scoped task_map artifact rather than a literal
        # ``${...}`` path.
        rendered = f".zf/artifacts/{pdd_id}/task_map.json"
    task_map_ref = event_task_map_ref or rendered
    path = _resolve_artifact_ref(rendered, state_dir=state_dir, project_root=project_root)
    if not path.exists():
        # P-NEXT-1 (2026-06-19 e2e): a synth/worker that writes the task_map
        # into its own worktree (e.g. issue-plan → docs/plans/...) and emits a
        # project-relative ref leaves the artifact under
        # state_dir/workdirs/<instance>/project/, not the project root — so the
        # writer fanout fail-closed with "task_map not found" even though the
        # plan was produced. Flows that materialize the task_map to a durable
        # .zf/artifacts path (refactor) are unaffected; this only rescues the
        # worktree-relative case. Fall back to the worktree copy.
        worktree_path = _resolve_in_worktrees(rendered, state_dir=state_dir)
        if worktree_path is not None:
            path = worktree_path
        else:
            # A1(PRD goal-mode e2e finding-1):agent 按字面 `.zf/` 写进
            # project root 而 state_dir 名不同 → 别名解析 404。补字面
            # project_root 回退(仍要求文件真实存在,fail-closed 不变)。
            literal_path = Path(project_root) / rendered
            if literal_path.exists():
                path = literal_path
            else:
                raise RuntimeError(f"writer fanout task_map not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"writer fanout task_map is invalid JSON: {path}") from exc
    if isinstance(data, dict):
        from zf.runtime.task_map import validate_task_map_payload

        task_map_validation = validate_task_map_payload(
            data,
            require_task_verification=False,
        )
        if not task_map_validation.passed:
            raise ValueError(
                "writer fanout task_map validation failed: "
                + "; ".join(task_map_validation.errors)
            )
    all_items = writer_task_items(data)
    validate_writer_task_bases(all_items, project_root=project_root)
    policy_errors = writer_task_map_policy_errors(
        all_items,
        candidate_quality_source=candidate_quality_source,
        work_units_config=work_units_config,
    )
    if policy_errors:
        raise WriterTaskMapPolicyError(policy_errors, all_items)
    requested_task_ids = _string_list(payload.get("task_ids"))
    completed_task_ids = _string_list(payload.get("completed_task_ids"))
    wave = _optional_int(payload.get("wave"))
    event_resume_scope = str(payload.get("resume_scope") or "").strip()
    task_map_resume_scope = str(data.get("resume_scope") or "").strip()
    resume_scope = task_map_resume_scope or event_resume_scope
    from zf.runtime.writer_amend_selection import writer_amend_selection

    requested_task_ids, resume_scope = writer_amend_selection(
        data, requested_task_ids=requested_task_ids, resume_scope=resume_scope,
    )
