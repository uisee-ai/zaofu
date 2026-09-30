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
    from zf.runtime.run_write_scope import validate_run_write_scopes

    validate_run_write_scopes(
        state_dir, run_id=str(payload.get("workflow_run_id") or event.correlation_id or pdd_id),
        task_items=all_items,
    )
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
    requested_task_ids = _canonical_replan_task_ids(
        data=data,
        task_items=all_items,
        requested_task_ids=requested_task_ids,
        resume_scope=resume_scope,
    )
    items = all_items
    if requested_task_ids:
        allowed = set(requested_task_ids)
        available_ids = {str(item.get("task_id") or "") for item in all_items}
        missing_requested_ids = sorted(allowed - available_ids)
        if missing_requested_ids:
            raise RuntimeError(
                "writer fanout task_map missing requested task_id(s): "
                + ", ".join(missing_requested_ids)
            )
        items = [item for item in all_items if str(item.get("task_id") or "") in allowed]
    elif wave is not None:
        items = [item for item in all_items if _optional_int(item.get("wave")) == wave]
    validate_writer_task_items(items)
    if pipeline_spec is not None and not (
        resume_scope == "gap_tasks_only" and requested_task_ids
    ):
        # doc 90 §3.3.1 / A6:assembly/根 owner 内容校验在 admission 期
        # fail-closed(task_map 解析后才可校验,非编译期)。
        from zf.core.workflow.lane_pipeline import (
            validate_lane_pipeline_admission,
        )
        # Global lane_pipeline invariants (declared assembly/root owner) must
        # see the full task_map. Gap-only resumes intentionally dispatch a
        # filtered subset, so validating those global invariants against only
        # requested gap tasks produces a false assembly/root-owner cancellation.
        problems = validate_lane_pipeline_admission(
            pipeline_spec,
            all_items,
            task_map_payload=data if isinstance(data, dict) else None,
        )
        if problems:
            raise ValueError(
                "lane_pipeline admission rejected task_map: "
                + "; ".join(problems)
            )
    feature_id = str(payload.get("feature_id") or data.get("feature_id") or pdd_id or "").strip()
    source_refs = data.get("source_refs") if isinstance(data.get("source_refs"), dict) else {}
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    task_map_dispatch_base = str(
        data.get("target_commit")
        or metadata.get("target_commit")
        or payload.get("dispatch_base_commit")
        or ""
    ).strip()
    return LoadedWriterTaskMap(
        task_items=items,
        task_map_ref=task_map_ref,
        task_map_path=path,
        source_index_ref=str(
            payload.get("source_index_ref")
            or source_refs.get("source_index_ref")
            or ""
        ).strip(),
        feature_id=feature_id,
        pdd_id=str(payload.get("pdd_id") or pdd_id or feature_id or "").strip(),
        wave=wave,
        requested_task_ids=requested_task_ids,
        completed_task_ids=completed_task_ids,
        is_replan=bool(
            payload.get("rework_of") or payload.get("rework_attempt")
            or payload.get("replan")
            or payload.get("replan_classification")
            or payload.get("amend_of") or resume_scope == "gap_tasks_only"
        ),
        dispatch_base_commit=writer_fanout_dispatch_base_commit(
            items,
            fallback=task_map_dispatch_base,
        ),
        plan_artifact_package_id=str(
            payload.get("plan_artifact_package_id") or ""
        ).strip(),
        plan_artifact_package_ref=str(
            payload.get("plan_artifact_package_ref") or ""
        ).strip(),
        plan_artifact_package_digest=str(
            payload.get("plan_artifact_package_digest") or ""
        ).strip(),
        task_map_generation=str(
            payload.get("task_map_generation") or ""
        ).strip(),
        flow_kind=str(
            payload.get("flow_kind")
            or data.get("flow_kind")
            or getattr(stage, "flow_kind", "")
            or ""
        ).strip().lower(),
        workflow_run_id=str(
            payload.get("workflow_run_id")
            or payload.get("run_id")
            or event.correlation_id
            or ""
        ).strip(),
    )


def writer_task_map_policy_errors(
    task_items: list[dict[str, Any]],
    *,
    candidate_quality_source: str = "auto",
    work_units_config: Any = None,
) -> list[str]:
    """Validate mechanical run policy before canonical task materialization."""

    errors: list[str] = []
    if str(candidate_quality_source or "auto") == "task_contract_required":
        missing = [
            str(item.get("task_id") or "<unknown>")
            for item in task_items
            if not str(item.get("verification") or "").strip()
        ]
        if missing:
            errors.append(
                "task_contract_required missing executable verification for "
                + ", ".join(missing)
            )

    if not _config_value(work_units_config, "enabled", False):
        return errors
    split = _config_value(work_units_config, "split_quality", None)
    if str(_config_value(split, "mode", "warning") or "warning") != "blocking":
        return errors
    max_scope = int(_config_value(split, "max_scope_files", 0) or 0)
    max_acceptance = int(
        _config_value(split, "max_acceptance_criteria", 0) or 0
    )
    for item in task_items:
        task_id = str(item.get("task_id") or "<unknown>")
        scope_count = len(item.get("allowed_paths") or [])
        acceptance_count = len(item.get("acceptance_criteria") or [])
        if max_scope and scope_count > max_scope:
            errors.append(
                f"{task_id} scope has {scope_count} files, max is {max_scope}"
            )
        if max_acceptance and acceptance_count > max_acceptance:
            errors.append(
                f"{task_id} has {acceptance_count} acceptance criteria, "
                f"max is {max_acceptance}"
            )
    return errors


def _config_value(config: Any, key: str, default: Any) -> Any:
    if isinstance(config, Mapping):
        return config.get(key, default)
    return getattr(config, key, default)


def admit_writer_fanout(
    *,
    task_store: TaskStore,
    loaded: LoadedWriterTaskMap,
) -> WriterFanoutAdmission:
    missing: list[str] = []
    stale: list[str] = []
    superseded: list[str] = []
    terminal: list[str] = []
    admitted: list[dict[str, Any]] = []
    for item in loaded.task_items:
        task_id = str(item.get("task_id") or "")
        task = task_store.get(task_id)
        if task is None:
            missing.append(task_id)
            continue
        status = str(task.status or "")
        if status in TERMINAL_TASK_STATUSES:
            terminal.append(task_id)
            if status in {"cancelled", "superseded"}:
                superseded.append(task_id)
            continue
        task_map_ref = _task_map_ref(task)
        if loaded.task_map_ref and task_map_ref != loaded.task_map_ref:
            stale.append(task_id)
            continue
        admitted.append({
            **item,
            "task_map_ref": loaded.task_map_ref,
            "source_index_ref": loaded.source_index_ref,
            "feature_id": loaded.feature_id,
            "pdd_id": loaded.pdd_id,
            "dispatch_base_commit": loaded.dispatch_base_commit,
            "flow_kind": loaded.flow_kind,
            "workflow_run_id": loaded.workflow_run_id,
        })
    if missing or stale or superseded or terminal:
        reason = "writer_fanout_admission_failed"
        if missing and not (stale or superseded or terminal):
            reason = "missing_kanban_tasks"
        elif stale:
            reason = "stale_task_map"
        elif superseded:
            reason = "superseded_task_map"
        elif terminal:
            reason = "terminal_tasks"
        return WriterFanoutAdmission(
            passed=False,
            reason=reason,
            missing_task_ids=missing,
            stale_task_ids=stale,
            superseded_task_ids=superseded,
            terminal_task_ids=terminal,
            suggested_action=(
                "ingest_product_delivery_task_map"
                if missing else "use_latest_product_delivery_wave_ready"
            ),
        )
    return WriterFanoutAdmission(passed=True, task_items=admitted)


def writer_completion_admission(
    *,
    task_store: TaskStore,
    task_id: str,
    task_map_ref: str,
) -> WriterFanoutAdmission:
    task = task_store.get(task_id)
    if task is None:
        return WriterFanoutAdmission(
            passed=False,
            reason="missing_kanban_task",
            missing_task_ids=[task_id],
            suggested_action="ingest_product_delivery_task_map",
        )
    status = str(task.status or "")
    if status in TERMINAL_TASK_STATUSES:
        return WriterFanoutAdmission(
            passed=False,
            reason="superseded_task_map" if status in {"cancelled", "superseded"} else "terminal_task",
            superseded_task_ids=[task_id] if status in {"cancelled", "superseded"} else [],
            terminal_task_ids=[task_id],
            suggested_action="use_latest_product_delivery_wave_ready",
        )
    if task_map_ref and _task_map_ref(task) != task_map_ref:
        return WriterFanoutAdmission(
            passed=False,
            reason="stale_task_map",
            stale_task_ids=[task_id],
            suggested_action="use_latest_product_delivery_wave_ready",
        )
    return WriterFanoutAdmission(passed=True)


def writer_task_items(data: object) -> list[dict[str, Any]]:
    raw_items: object = []
    wave_by_task: dict[str, int] = {}
    if isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        wave_by_task = _wave_by_task_id(data)
        for key in ("tasks", "children", "task_order", "order"):
            if isinstance(data.get(key), list):
                raw_items = data[key]
                break
    if not isinstance(raw_items, list):
        return []
    source_map = data if isinstance(data, Mapping) else {"tasks": raw_items}
    command_registry = task_map_command_registry(source_map)
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if isinstance(raw, str):
            items.append({
                "task_id": raw,
                "scope": raw,
                "allowed_paths": [],
                "protected_paths": [".zf/**"],
                "wave": 0,
                "payload": {},
            })
            continue
        if not isinstance(raw, dict):
            continue
        materialized_raw = materialize_task_verification_commands(raw, registry=command_registry)
        task_id = str(raw.get("task_id") or raw.get("id") or raw.get("task") or "")
        allowed_paths = _string_list(
            raw.get("allowed_paths")
            or raw.get("paths")
            or raw.get("path")
            or raw.get("scope")
            or raw.get("exclusive_files")
        )
        protected_paths = _string_list(raw.get("protected_paths")) or [".zf/**"]
        scope = str(
            raw.get("scope") if isinstance(raw.get("scope"), str) else ""
        ).strip() or str(
            raw.get("title")
            or ",".join(allowed_paths)
            or task_id
        )
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        owner_role, owner_instance = owner_fields_from_task_map_item(raw)
        verification_commands = task_verification_commands(materialized_raw)
        verification = str(
            verification_commands[0]["command"] if verification_commands else ""
        )
        validation = (
            materialized_raw.get("validation")
            if isinstance(materialized_raw.get("validation"), dict)
            else {}
        )
        raw_verification_tiers = _string_list(raw.get("verification_tiers"))
        items.append({
            "task_id": task_id,
            "scope": scope,
            "allowed_paths": allowed_paths,
            "protected_paths": protected_paths,
            "payload": dict(payload),
            "wave": _optional_int(
                raw.get("wave") if raw.get("wave") is not None else wave_by_task.get(task_id)
            ) or 0,
            "blocked_by": _source_refs_list(raw.get("blocked_by")),
            "owner_role": owner_role,
            "owner_instance": owner_instance,
            "affinity_tag": str(raw.get("affinity_tag") or "").strip(),
            "context_group": str(raw.get("context_group") or "").strip(),
            "root_owner_class": str(raw.get("root_owner_class") or "").strip(),
            "skills_required": validated_writer_task_skills(raw, task_id=task_id),
            "pipeline_declared_task_id": str(
                raw.get("pipeline_declared_task_id") or ""
            ).strip(),
            "preferred_impl_role": str(
                raw.get("preferred_impl_role") or ""
            ).strip(),
            "preferred_review_role": str(
                raw.get("preferred_review_role") or ""
            ).strip(),
            "preferred_verify_role": str(
                raw.get("preferred_verify_role") or ""
            ).strip(),
            "depends_on": _source_refs_list(raw.get("depends_on")),
            "exclusive_files": _string_list(raw.get("exclusive_files")),
            "git_fact_anchors": _source_refs_list(raw.get("git_fact_anchors")),
            "verification_tiers": canonical_verification_tiers(
                raw_verification_tiers,
                verification=verification,
                validation=validation,
            ),
            "raw_verification_tiers": raw_verification_tiers,
            "acceptance_criteria": _acceptance_criteria_list(
                raw.get("acceptance_criteria") or raw.get("acceptance")
            ),
            "source_key": str(raw.get("source_key") or "").strip(),
            "source_keys": _source_refs_list(raw.get("source_keys")),
            "source_ref": str(raw.get("source_ref") or "").strip(),
            "source_refs": _source_refs_list(raw.get("source_refs")),
            "base_commit": task_map_successor_base_commit(raw),
            "supersedes_task_ids": task_map_supersedes_task_ids(raw),
            "source_excerpt": str(raw.get("source_excerpt") or "").strip(),
            "verification": verification,
            "validation": (
                validation_with_commands(validation, verification_commands)
                if verification_commands else dict(validation)
            ),
            "raw_task": materialized_raw,
        })
    return _normalize_writer_task_items(items)


def validate_writer_task_bases(
    task_items: list[dict[str, Any]],
    *,
    project_root: Path,
) -> None:
    """Require every explicit task baseline to resolve to a local commit."""

    for item in task_items:
        task_id = str(item.get("task_id") or "<unknown>")
        base_commit = str(item.get("base_commit") or "").strip()
        if not base_commit:
            continue
        resolved = subprocess.run(
            ["git", "rev-parse", "--verify", f"{base_commit}^{{commit}}"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        full_commit = resolved.stdout.strip()
        if resolved.returncode != 0 or not full_commit:
            raise RuntimeError(
                f"writer fanout task {task_id} base_commit "
                f"{base_commit!r} is not a local commit"
            )
        if full_commit.lower() != base_commit.lower():
            raise RuntimeError(
                f"writer fanout task {task_id} base_commit must be "
                "the full immutable commit id"
            )


def _normalize_writer_task_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize task-local ownership noise before cross-task admission.

    The admission gate must remain strict for real cross-slice ownership
    conflicts, but generated plans often use placeholder files such as
    ``app/test/.gitkeep`` to scaffold a directory later owned by a test slice
    via ``app/test/**``. Treat those placeholder files as directory-creation
    implementation detail, not durable write ownership.
    """
    if not items:
        return items
    subtree_prefixes: list[tuple[str, ...]] = []
    for item in items:
        for raw_path in item.get("allowed_paths") or []:
            path = str(raw_path or "").strip()
            if _is_subtree_glob(path):
                subtree_prefixes.append(_normalize_path_prefix(path))

    out: list[dict[str, Any]] = []
    for item in items:
        allowed = _string_list(item.get("allowed_paths"))
        normalized: list[str] = []
        for path in allowed:
            if _is_placeholder_path(path):
                norm = _normalize_path_prefix(path)
                if any(_prefix_contains(prefix, norm) for prefix in subtree_prefixes):
                    contract_text = json.dumps(
                        {
                            "acceptance_criteria": item.get("acceptance_criteria") or [],
                            "verification": item.get("verification") or [],
                        },
                        ensure_ascii=False,
                    )
                    if path in contract_text:
                        raise RuntimeError(
                            "writer fanout task "
                            f"{item.get('task_id')!r} has delegated placeholder "
                            f"{path!r} to a sibling subtree owner but still "
                            "requires it in acceptance/verification"
                        )
                    continue
            normalized.append(path)
        normalized = _dedupe_redundant_task_paths(normalized)
        if normalized == allowed:
            out.append(item)
        else:
            updated = dict(item)
            updated["allowed_paths"] = normalized
            out.append(updated)
    return out


def _dedupe_redundant_task_paths(paths: list[str]) -> list[str]:
    subtree_prefixes = [
        _normalize_path_prefix(path)
        for path in paths
        if _is_subtree_glob(path)
    ]
    out: list[str] = []
    for path in paths:
        norm = _normalize_path_prefix(path)
        if (
            not _is_subtree_glob(path)
            and any(_prefix_contains(prefix, norm) for prefix in subtree_prefixes)
        ):
            continue
        if path not in out:
            out.append(path)
    return out


def _is_subtree_glob(path: str) -> bool:
    text = str(path or "").strip().rstrip("/")
    return text.endswith("/**") or text == "**"


def _is_placeholder_path(path: str) -> bool:
    name = Path(str(path or "").strip()).name
    return name in {".gitkeep", ".keep", ".placeholder"}


def _prefix_contains(prefix: tuple[str, ...], child: tuple[str, ...]) -> bool:
    if not prefix:
        return True
    if len(child) < len(prefix):
        return False
    return child[: len(prefix)] == prefix


def _wave_by_task_id(data: dict[str, Any]) -> dict[str, int]:
    waves = data.get("waves")
    if not isinstance(waves, list):
        return {}
    out: dict[str, int] = {}
    for raw in waves:
        if not isinstance(raw, dict):
            continue
        wave = _optional_int(raw.get("wave")) or 0
        for task_id in _string_list(raw.get("tasks")):
            out.setdefault(task_id, wave)
    return out


def validate_writer_task_items(task_items: list[dict[str, Any]]) -> None:
    if not task_items:
        raise RuntimeError("writer fanout task_map has no tasks")
    task_ids: set[str] = set()
    scopes: set[str] = set()
    # Each entry: (normalized path components, original path string). Only
    # CROSS-slice overlap is a conflict, so we compare a slice's paths against
    # paths already seen from OTHER slices (doc 78 W1: parent/child prefix
    # subsumption, not just exact reuse — the T1 越权 root cause).
    seen_paths: list[tuple[tuple[str, ...], str]] = []
    for item in task_items:
        task_id = str(item.get("task_id") or "")
        if not task_id:
            raise RuntimeError("writer fanout task_map contains task without task_id")
        if task_id in task_ids:
            raise RuntimeError(f"writer fanout task_map duplicates task_id {task_id!r}")
        task_ids.add(task_id)
        scope = str(item.get("scope") or "")
        allowed_paths = [str(path) for path in item.get("allowed_paths", []) or [] if str(path)]
        if not scope and not allowed_paths:
            raise RuntimeError(f"writer fanout task {task_id} has ambiguous scope")
        if scope:
            if scope in scopes:
                raise RuntimeError(f"writer fanout task_map duplicates scope {scope!r}")
            scopes.add(scope)
        item_paths = [(_normalize_path_prefix(path), path) for path in allowed_paths]
        for norm, original in item_paths:
            for prev_norm, prev_original in seen_paths:
                if _paths_overlap(norm, prev_norm):
                    raise RuntimeError(
                        "writer fanout task_map has overlapping allowed paths "
                        f"{prev_original!r} and {original!r}"
                    )
        seen_paths.extend(item_paths)


def _normalize_path_prefix(path: str) -> tuple[str, ...]:
    """Reduce an allowed-path glob to its concrete directory components.

    Strips trailing glob segments (``**``, ``*``, ``*.ts``) and surrounding
    slashes so ``cj-min/packages/**`` and ``cj-min/packages/`` both normalize to
    ``("cj-min", "packages")``. An empty result (e.g. bare ``**``) denotes
    whole-repo scope, which overlaps every slice.
    """
    parts = [seg for seg in path.strip().strip("/").split("/") if seg and seg != "."]
    # Truncate at the FIRST glob segment, not only trailing ones: a mid-path
    # recursion glob (`src/foo/**/*.py`) spans everything under its concrete
    # prefix, so the prefix ends there. Keeping the literal `**` component
    # made `src/foo/**/*.py` vs `src/foo/bar.py` compare as disjoint — the
    # doc-78 T1 越权 class in mid-glob shape (2026-06-10 review P1-5).
    # A filename glob like `*.md` or `config.*` in LEAF position is kept as
    # a concrete-ish leaf so a root-level file glob does NOT collapse to the
    # empty tuple (whole-repo scope) and wrongly reject a disjoint slice —
    # only a bare `**`/`*` denotes whole-repo.
    out: list[str] = []
    for i, seg in enumerate(parts):
        if seg in ("*", "**"):
            break
        is_leaf = i == len(parts) - 1
        if not is_leaf and any(ch in seg for ch in "*?["):
            # Directory-position glob (`src/foo*/bar`) matches arbitrary
            # sibling dirs; the concrete prefix ends before it.
            break
        out.append(seg)
    return tuple(out)


def _paths_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """True when two normalized paths are equal or one is an ancestor dir of the other."""
    if not a or not b:
        # Whole-repo scope on either side overlaps any other path.
        return True
    n = min(len(a), len(b))
    return a[:n] == b[:n]


def _task_map_ref(task: Task) -> str:
    contract = task.contract
    evidence = contract.evidence_contract if contract else {}
    if not isinstance(evidence, dict):
        return ""
    refs = evidence.get("source_refs") or {}
    if isinstance(refs, dict) and refs.get("task_map_ref"):
        return str(refs.get("task_map_ref") or "").strip()
    manual = evidence.get("manual_evidence") or {}
    if isinstance(manual, dict):
        manual_refs = manual.get("source_refs") or {}
        if isinstance(manual_refs, dict):
            return str(manual_refs.get("task_map_ref") or "").strip()
    return ""


def _resolve_artifact_ref(ref: str, *, state_dir: Path, project_root: Path) -> Path:
    from zf.runtime.artifact_refs import resolve_runtime_artifact_ref

    return resolve_runtime_artifact_ref(
        ref,
        state_dir=state_dir,
        project_root=project_root,
    )


def _resolve_in_worktrees(ref: str, *, state_dir: Path) -> Path | None:
    """P-NEXT-1 fallback: a worker may have written the artifact inside its own
    worktree (``state_dir/workdirs/<instance>/project/<ref>``) and emitted a
    project-relative ref. Return the worktree copy if exactly one exists, else
    None (ambiguity stays fail-closed). Only meaningful for relative refs."""
    rel = Path(ref)
    if rel.is_absolute():
        return None
    # P-NEXT-1b (2026-06-19 e2e): a synth may write the task_map into its own
    # worktree under a ``.zf/``-prefixed path (e.g. workdirs/<inst>/project/.zf/
    # artifacts/<id>/task_map.json) and emit ``.zf/artifacts/...`` — the primary
    # resolver maps ``.zf/`` to the real state_dir where it isn't. Try the
    # worktree copy for both plain and ``.zf``-prefixed relative refs.
    workdirs = state_dir / "workdirs"
    if not workdirs.is_dir():
        return None
    matches = [
        candidate / "project" / rel
        for candidate in sorted(workdirs.iterdir())
        if candidate.is_dir() and (candidate / "project" / rel).exists()
    ]
    return matches[0] if len(matches) == 1 else None


def _render_template(template: str, event: ZfEvent) -> str:
    values: dict[str, str] = {}
    payload = _payload(event)
    values.update({str(key): str(value) for key, value in payload.items()})
    if event.task_id:
        values.setdefault("task_id", event.task_id)
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("${" + key + "}", value)
    return rendered or str(values.get("target_ref") or values.get("candidate_ref") or values.get("task_ref") or "")


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _canonical_replan_task_ids(
    *,
    data: dict[str, Any],
    task_items: list[dict[str, Any]],
    requested_task_ids: list[str],
    resume_scope: str,
) -> list[str]:
    """Resolve stale feedback ids to an explicit gap-only successor map.

    Candidate replan triggers retain the failed generation's task ids as
    diagnosis context. A synth may replace that generation with new task ids;
    those old ids must not become writer dispatch selectors. Only a task map
    that explicitly declares ``gap_tasks_only`` may replace a stale selection,
    and the replacement must either declare its ids or consist entirely of
    mechanically anchored successor tasks. All other mismatches remain
    fail-closed in ``load_writer_task_map``.
    """

    if not requested_task_ids or resume_scope not in {
        "gap_tasks_only",
        "failed_children_only",
        "failed_children_and_downstream",
    }:
        return requested_task_ids
    available_ids = {
        str(item.get("task_id") or "").strip()
        for item in task_items
        if str(item.get("task_id") or "").strip()
    }
    successor_ids_by_predecessor: dict[str, list[str]] = {}
    for item in task_items:
        successor_id = str(item.get("task_id") or "").strip()
        if not successor_id:
            continue
        for predecessor_id in task_map_supersedes_task_ids(item):
            successor_ids_by_predecessor.setdefault(predecessor_id, []).append(
                successor_id
            )

    resolved_ids: list[str] = []
    unresolved_ids: list[str] = []
    for requested_id in requested_task_ids:
        successors = list(dict.fromkeys(
            successor_ids_by_predecessor.get(requested_id, [])
        ))
        if len(successors) > 1:
            raise RuntimeError(
                "writer fanout task_map has ambiguous successor mapping for "
                f"{requested_id}: {', '.join(successors)}"
            )
        if successors:
            resolved_id = successors[0]
        elif requested_id in available_ids:
            resolved_id = requested_id
        else:
            unresolved_ids.append(requested_id)
            continue
        if resolved_id not in resolved_ids:
            resolved_ids.append(resolved_id)

    if not unresolved_ids:
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        declared_delta_ids = _string_list(
            data.get("dispatch_delta_task_ids")
        ) or _string_list(metadata.get("dispatch_delta_task_ids"))
        if declared_delta_ids and set(declared_delta_ids) != set(resolved_ids):
            raise RuntimeError(
                "writer fanout task_map dispatch_delta_task_ids disagree with "
                "the requested/superseding task selection"
            )
        return resolved_ids

    if resume_scope != "gap_tasks_only":
        return requested_task_ids

    map_task_ids = _string_list(data.get("task_ids")) or _string_list(
        data.get("gap_task_ids")
    )
    if map_task_ids:
        return (
            map_task_ids
            if set(map_task_ids) <= available_ids
            else requested_task_ids
        )

    successor_ids = [
        str(item.get("task_id") or "").strip()
        for item in task_items
        if str(item.get("task_id") or "").strip()
        and task_map_supersedes_task_ids(item)
    ]
    if successor_ids and len(successor_ids) == len(task_items):
        return successor_ids
    return requested_task_ids


def _source_refs_list(value: Any) -> list[str]:
    if isinstance(value, dict):
        refs: list[str] = []
        for key, raw in value.items():
            key_text = str(key).strip()
            if isinstance(raw, (list, tuple)):
                raw_values = raw
            else:
                raw_values = [raw]
            for item in raw_values:
                text = str(item).strip()
                if not text:
                    continue
                refs.append(text)
                if key_text:
                    refs.append(f"{key_text}:{text}")
        return refs
    return _string_list(value)


def _acceptance_criteria_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return _string_list(value)
    out: list[Any] = []
    for item in value:
        if isinstance(item, dict):
            text = str(
                item.get("text")
                or item.get("statement")
                or item.get("criterion")
                or item.get("description")
                or item.get("acceptance")
                or ""
            ).strip()
            if text:
                out.append(dict(item))
        elif str(item).strip():
            out.append(str(item).strip())
    return out


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
