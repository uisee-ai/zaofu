"""Workflow delivery preview and submit application helpers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from zf.core.config.loader import ConfigError, load_config
from zf.core.events import ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_intake import (
    _normalize_request_kind,
    _state_dir_for_config,
    _unique_request_id,
)
from zf.runtime.workflow_preflight import (
    _flow_kind,
    _load_json,
    _load_manifest_for_intake,
    build_flow_preflight_report,
)
from zf.runtime.workflow_submission_binding import (
    pin_artifact_delivery_goal_claim_set as _pin_artifact_delivery_goal_claim_set,
)
from zf.runtime.workflow_submission_binding import (
    pin_submitted_run_contract as _pin_submitted_run_contract,
)
from zf.runtime.workflow_delivery_replay import (
    submitted_request_replay_result as _submitted_request_replay_result,
)
from zf.runtime.workflow_task_lifecycle import (
    claim_submitted_workflow_task as _claim_submitted_workflow_task,
)
from zf.runtime.workflow_runtime_start import ensure_submitted_workflow_runtime

def build_flow_submit_preview(
    *,
    config_path: Path,
    intake_path: Path,
    flow_kind: str = "",
    task_id: str = "",
    pattern_id: str = "",
    requested_by: str = "zf-cli",
    reason: str = "",
    output: Path | None = None,
    allow_missing_env: bool = False,
    synthesis_result_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest_path, manifest = _load_manifest_for_intake(intake_path)
    if manifest_path is None:
        manifest = {
            "request_id": _request_id_from_path(intake_path),
            "kind": flow_kind,
            "intake_ref": str(intake_path),
            "artifact_refs": [str(intake_path)],
        }
    request_id = str(manifest.get("request_id") or _request_id_from_path(intake_path))
    # Intake/matrix artifacts are durable request inputs and may intentionally
    # live in the project tree.  Submit preflight and preview are runtime
    # projections: rewriting them on every submit must not dirty a project and
    # block its subsequent ship.  Keep explicit --output as the caller's
    # deliberate escape hatch, otherwise place them under the configured state.
    config = load_config(config_path)
    state_dir = _state_dir_for_config(config_path, config)
    projection_dir = state_dir / "artifacts" / "workflow" / request_id
    projection_dir.mkdir(parents=True, exist_ok=True)
    preflight_path = projection_dir / "workflow-preflight.json"
    preview_path = (output or projection_dir / "workflow-submit-preview.json").expanduser()
    request_projection: dict[str, Any] = {}
    request_blockers: list[dict[str, Any]] = []
    if manifest_path is not None:
        from zf.runtime.workflow_requests import (
            load_workflow_request,
            register_workflow_intake,
            request_readiness_blockers,
            workflow_request_path,
        )

        state_dir.mkdir(parents=True, exist_ok=True)
        request_writer = EventWriter(event_log_from_project(state_dir, config=config))
        request_projection = register_workflow_intake(
            state_dir,
            manifest_path,
            actor=requested_by or "zf-cli",
            writer=request_writer,
        )
        effective_manifest_ref = str(
            request_projection.get("workflow_input_manifest_ref") or ""
        )
        effective_manifest = (
            _load_json(Path(effective_manifest_ref))
            if effective_manifest_ref
            else {}
        )
        if effective_manifest:
            manifest = effective_manifest
        request_blockers = request_readiness_blockers(request_projection)
        from zf.runtime.workflow_prepare import prepared_task_error
        if error := prepared_task_error(state_dir, request_projection):
            request_blockers.append({"severity": "STOP", "kind": "workflow_task_stale", "message": error})
        bound_task_id = str((manifest.get("task_input_binding") or {}).get("task_id") or "")
        if task_id and bound_task_id and task_id != bound_task_id:
            request_blockers.append({"severity": "STOP", "kind": "workflow_task_stale", "message": "Task does not match prepared Request"})
        task_id = task_id or bound_task_id
        if flow_kind in {"", "auto"} and request_projection.get("selected_flow_family"):
            flow_kind = {"IssueFlow": "issue", "PrdFlow": "prd", "RefactorFlow": "refactor", "Workflow": "workflow"}.get(
                request_projection["selected_flow_family"], flow_kind)

        manifest["request_projection_ref"] = str(
            workflow_request_path(state_dir, request_id)
        )
        manifest["requirement_spec_ref"] = str(
            request_projection.get("requirement_spec_ref") or ""
        )
        manifest["requirement_spec_digest"] = str(
            request_projection.get("requirement_spec_digest") or ""
        )
        manifest.setdefault("artifact_refs", [])
        if (
            manifest["requirement_spec_ref"]
            and manifest["requirement_spec_ref"] not in manifest["artifact_refs"]
        ):
            manifest["artifact_refs"].append(manifest["requirement_spec_ref"])
    effective_manifest_path = Path(
        str(request_projection.get("workflow_input_manifest_ref") or "")
    ) if request_projection.get("workflow_input_manifest_ref") else manifest_path
    report = build_flow_preflight_report(
        config_path.resolve(),
        flow_kind=flow_kind or str(manifest.get("kind") or ""),
        intake_path=intake_path,
        workflow_input_manifest_path=effective_manifest_path,
        allow_missing_env=allow_missing_env,
    )
    effective_config_path = config_path.resolve()
    if request_projection and synthesis_result_ref is not None:
        from zf.runtime.workflow_proposal import (
            materialize_synthesized_workflow_candidate,
        )

        synthesis_kind = _normalize_request_kind(
            flow_kind or str(manifest.get("kind") or "")
        )
        effective_config_path = materialize_synthesized_workflow_candidate(
            state_dir,
            request=request_projection,
            base_config_path=config_path,
            synthesis_result_ref=synthesis_result_ref,
            flow_kind=synthesis_kind,
        )
        if effective_config_path != config_path.resolve():
            report = build_flow_preflight_report(
                effective_config_path,
                flow_kind=synthesis_kind,
                intake_path=intake_path,
                workflow_input_manifest_path=effective_manifest_path,
                allow_missing_env=allow_missing_env,
            )
    resolved_kind = _normalize_request_kind(
        flow_kind or str(manifest.get("kind") or report.get("flow_kind") or "")
    )
    if resolved_kind == "auto":
        from zf.runtime.workflow_request_routing import unresolved_route_blocker

        request_blockers.append(unresolved_route_blocker())
    else:
        from zf.core.workflow.request_policy import missing_fields_for_kind

        missing = missing_fields_for_kind(
            resolved_kind, objective=str(manifest.get("objective") or ""),
            source_ref=str(manifest.get("source_ref") or ""),
            source_root=str(manifest.get("source_root") or ""),
            target_root=str(manifest.get("target_root") or ""),
        )
        if missing:
            request_blockers.append({
                "severity": "STOP", "kind": "workflow_selected_route_inputs_missing",
                "message": ", ".join(missing), "safe_auto_fix": False,
            })
    source_ref_blockers = _submit_source_ref_blockers(
        config_path=config_path,
        source_ref=str(manifest.get("source_ref") or ""),
        flow_kind=resolved_kind,
    )
    resolved_task_id = _resolve_submit_task_id(task_id, request_id=request_id, kind=resolved_kind)
    workflow_tier = str(
        manifest.get("workflow_tier")
        or manifest.get("tier")
        or ""
    ).strip().lower()
    route_blockers: list[dict[str, Any]] = []
    try:
        resolved_pattern_id = _resolve_submit_pattern_id(
            config_path=effective_config_path,
            pattern_id=pattern_id,
            kind=resolved_kind,
            workflow_tier=workflow_tier,
        )
    except ConfigError as exc:
        resolved_pattern_id = ""
        route_blockers.append({
            "severity": "STOP",
            "kind": "workflow_route_unresolved",
            "title": "workflow route 无法确定",
            "message": str(exc),
            "why_it_matters": (
                "同一 canonical zf.yaml 承载多个 request kind 时,submit "
                "必须确定性选择 stage,不能猜第一个 stage。"
            ),
            "fix_it": (
                "在 workflow.kind_routes 中声明 kind -> pattern_id,或显式传 "
                "--pattern-id。"
            ),
            "safe_auto_fix": False,
        })
    preflight_path.write_text(
        json.dumps(_public_preflight_report(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_artifact_refs = _workflow_manifest_artifact_refs(
        manifest,
        manifest_path=effective_manifest_path,
        intake_path=intake_path,
        preflight_path=preflight_path,
    )
    matrix_ref_payload = {
        key: str(manifest.get(key) or "")
        for key in _WORKFLOW_MATRIX_REF_KEYS
        if str(manifest.get(key) or "").strip()
    }
    canonical_intake_ref = str(manifest.get("intake_json_ref") or intake_path)
    display_intake_ref = str(
        manifest.get("intake_markdown_ref")
        or manifest.get("intake_ref")
        or intake_path
    )
    submit_payload = {
        "schema_version": "workflow.submit.requested.v1",
        "request_id": request_id,
        "run_id": request_id,
        "kind": resolved_kind,
        "request_kind": str(manifest.get("request_kind") or resolved_kind),
        "workflow_tier": workflow_tier,
        "task_id": resolved_task_id,
        "pattern_id": resolved_pattern_id,
        "config_ref": str(config_path),
        "workflow_prompt_ref": canonical_intake_ref,
        "workflow_input_manifest_ref": str(effective_manifest_path or ""),
        "workflow_preflight_ref": str(preflight_path),
        "workflow_request_ref": str(manifest.get("request_projection_ref") or ""),
        "requirement_spec_ref": str(request_projection.get("requirement_spec_ref") or ""),
        "requirement_spec_digest": str(request_projection.get("requirement_spec_digest") or ""),
        "request_revision": int(request_projection.get("revision") or 0),
        "origin_binding": (
            dict(request_projection["origin_binding"])
            if isinstance(request_projection.get("origin_binding"), dict)
            else {}
        ),
        "requested_by": requested_by or "zf-cli",
        "reason": reason or f"workflow submit {request_id}",
        # E2(prd-goal e2e):objective 曾不入 submit payload,G0 铸造
        # 落到 reason(操作员备注被当成了 run 目标)。真源=manifest。
        "objective": str(manifest.get("objective") or ""),
        **matrix_ref_payload,
        "source_refs": {
            **(
                {
                    str(key): str(value)
                    for key, value in manifest.get("source_refs", {}).items()
                    if str(key).strip() and str(value).strip()
                }
                if isinstance(manifest.get("source_refs"), dict)
                else {}
            ),
            "source_ref": str(manifest.get("source_ref") or ""),
            "source_root": str(manifest.get("source_root") or ""),
            "target_root": str(manifest.get("target_root") or ""),
            "intake_ref": canonical_intake_ref,
            "intake_markdown_ref": display_intake_ref,
            "workflow_input_manifest_ref": str(effective_manifest_path or ""),
            **matrix_ref_payload,
        },
        "artifact_refs": manifest_artifact_refs,
    }
    blockers = [
        *(report.get("blockers") or []),
        *source_ref_blockers,
        *route_blockers,
        *request_blockers,
    ]
    status = (
        "STOP"
        if source_ref_blockers or route_blockers or request_blockers
        else report["status"]
    )
    proposal: dict[str, Any] = {}
    proposal_ref: dict[str, Any] = {}
    if request_projection and resolved_kind != "auto":
        from zf.runtime.workflow_proposal import (
            build_workflow_proposal,
            cleanup_synthesized_workflow_candidate,
        )

        try:
            proposal, proposal_ref = build_workflow_proposal(
                state_dir,
                request=request_projection,
                base_config_path=config_path,
                candidate_config_path=effective_config_path,
                synthesis_result_ref=synthesis_result_ref,
                preflight={
                    **_public_preflight_report(report),
                    "blockers": blockers,
                    "resolved_pattern_id": resolved_pattern_id,
                },
                flow_kind=resolved_kind,
                actor=requested_by or "zf-cli",
                writer=request_writer,
            )
        finally:
            cleanup_synthesized_workflow_candidate(
                effective_config_path,
                base_config_path=config_path,
            )
        for blocker in proposal.get("blockers", []):
            if isinstance(blocker, dict) and blocker not in blockers:
                blockers.append(blocker)
        if any(
            str(item.get("severity") or "").upper() == "STOP"
            for item in blockers
            if isinstance(item, dict)
        ):
            status = "STOP"
        request_projection = load_workflow_request(state_dir, request_id)
        proposal_completion = (
            proposal.get("completion_profile")
            if isinstance(proposal.get("completion_profile"), dict)
            else {}
        )
        submit_payload.update({
            "workflow_proposal_ref": proposal_ref,
            "workflow_proposal_digest": str(
                proposal.get("proposal_digest") or ""
            ),
            "goal_id": request_id,
            "workflow_generation": str(
                proposal.get("proposal_digest") or ""
            ),
            "workflow_intent": str(
                proposal_completion.get("intent") or ""
            ),
            "workflow_template": str(
                proposal_completion.get("template") or ""
            ),
            "completion_profile": str(
                proposal_completion.get("id") or "software_delivery"
            ),
            "generic_workflow_contract_digest": str(
                proposal_completion.get(
                    "generic_workflow_contract_digest"
                )
                or ""
            ),
            "required_delivery_artifacts": list(
                proposal_completion.get("required_delivery_artifacts")
                or []
            ),
            "effective_config_ref": dict(
                proposal.get("effective_config_ref") or {}
            ),
            "effective_config_digest": str(
                (proposal.get("effective_config_ref") or {}).get("sha256")
                if isinstance(proposal.get("effective_config_ref"), dict)
                else ""
            ),
        })
    result = {
        "schema_version": "workflow.submit.preview.v1",
        "status": status,
        "dry_run": True,
        "event_type": "workflow.submit.requested",
        "payload": submit_payload,
        "submit_preview_ref": str(preview_path),
        "preflight_ref": str(preflight_path),
        "blockers": blockers,
        "request": request_projection,
        "proposal": proposal,
        "proposal_ref": proposal_ref,
        "next": {
            "apply": "run `zf flow submit --apply ...` after operator approval",
        },
    }
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result

_WORKFLOW_MATRIX_REF_KEYS = (
    "source_inventory_ref",
    "capability_matrix_ref",
    "acceptance_matrix_ref",
    "test_matrix_ref",
    "task_map_ref",
    "real_e2e_matrix_ref",
    "skill_adapter_plan_ref",
    "intake_json_ref",
)

def _submit_source_ref_blockers(
    *,
    config_path: Path,
    source_ref: str,
    flow_kind: str,
) -> list[dict[str, Any]]:
    """Mirror reader target admission before a workflow is submitted."""

    ref = str(source_ref or "").strip()
    if flow_kind not in {"issue", "prd"} or not ref:
        return []
    project_root = config_path.resolve().parent
    candidate = Path(ref).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(project_root)
        if resolved.exists():
            return []
    except (OSError, RuntimeError, ValueError):
        pass
    git_ref = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if git_ref.returncode == 0:
        return []
    return [{
        "severity": "STOP",
        "kind": "workflow_source_ref_unresolvable",
        "title": "workflow source_ref 无法作为 reader target 使用",
        "message": (
            f"source_ref `{ref}` 既不是项目内现存路径，也不是可解析 Git ref"
        ),
        "why_it_matters": (
            "reader worktree 会 fail-closed；若继续 submit，只会消耗有界 rework "
            "而不会启动 scan agent。"
        ),
        "fix_it": (
            "将 Issue/PRD 输入复制到项目内并更新 workflow input manifest，"
            "或改用当前项目可解析的 Git ref。"
        ),
        "safe_auto_fix": False,
    }]

def _workflow_manifest_artifact_refs(
    manifest: dict[str, Any],
    *,
    manifest_path: Path | None,
    intake_path: Path,
    preflight_path: Path,
) -> list[str]:
    refs: list[str] = [str(intake_path), str(manifest_path or ""), str(preflight_path)]
    for item in manifest.get("artifact_refs") or []:
        if isinstance(item, dict):
            refs.extend(
                str(item.get(key) or "")
                for key in ("path", "ref", "uri")
            )
        else:
            refs.append(str(item or ""))
    for key in _WORKFLOW_MATRIX_REF_KEYS:
        refs.append(str(manifest.get(key) or ""))
    refs.append(str(manifest.get("intent_ref") or ""))
    return [ref for ref in dict.fromkeys(ref.strip() for ref in refs) if ref]

def apply_flow_submit(
    *,
    config_path: Path,
    intake_path: Path,
    flow_kind: str = "",
    task_id: str = "",
    pattern_id: str = "",
    requested_by: str = "zf-cli",
    reason: str = "",
    output: Path | None = None,
    allow_missing_env: bool = False,
) -> dict[str, Any]:
    config = load_config(config_path)
    state_dir = _state_dir_for_config(config_path, config)
    state_dir.mkdir(parents=True, exist_ok=True)
    manifest_path, _manifest = _load_manifest_for_intake(intake_path)
    if manifest_path is not None:
        from zf.runtime.workflow_requests import (
            register_workflow_intake,
            revise_workflow_request,
        )

        request_writer = EventWriter(event_log_from_project(state_dir, config=config))
        projection = register_workflow_intake(
            state_dir,
            manifest_path,
            actor=requested_by or "zf-cli",
            writer=request_writer,
        )
        if str(projection.get("status") or "") in {"submitted", "running"}:
            return ensure_submitted_workflow_runtime(_submitted_request_replay_result(
                config=config,
                state_dir=state_dir,
                projection=projection,
                events=request_writer.event_log.read_all(),
            ), config_path=config_path, config=config)
        if not bool(projection.get("confirmed")):
            projection = revise_workflow_request(
                state_dir,
                manifest_path,
                actor=requested_by or "zf-cli",
                confirm=True,
                writer=request_writer,
            )
    if manifest_path is not None:
        from zf.runtime.workflow_prepare import prepared_task_error
        if error := prepared_task_error(state_dir, projection, actor=requested_by):
            return {"status": "STOP", "reason": error, "blockers": [
                {"severity": "STOP", "kind": "workflow_task_stale", "message": error}]}
    preview = build_flow_submit_preview(
        config_path=config_path,
        intake_path=intake_path,
        flow_kind=flow_kind,
        task_id=task_id,
        pattern_id=pattern_id,
        requested_by=requested_by,
        reason=reason,
        output=output,
        allow_missing_env=allow_missing_env,
    )
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    payload = dict(preview.get("payload") or {})
    correlation_id = str(payload.get("request_id") or "")
    task = str(payload.get("task_id") or "")
    if preview["status"] != "STOP":
        _pin_submitted_run_contract(
            state_dir=state_dir,
            preview=preview,
            writer=writer,
            correlation_id=correlation_id,
            task_id=task,
        )
        _pin_artifact_delivery_goal_claim_set(
            state_dir=state_dir,
            project_root=config_path.resolve().parent,
            preview=preview,
            writer=writer,
            correlation_id=correlation_id,
            task_id=task,
        )
        payload.update(
            preview.get("payload")
            if isinstance(preview.get("payload"), dict)
            else {}
        )
    if preview["status"] != "STOP" and correlation_id:
        from zf.runtime.workflow_requests import (
            load_workflow_request,
            mark_workflow_request,
        )

        current_request = load_workflow_request(state_dir, correlation_id)
        if str(current_request.get("status") or "") == "proposed":
            mark_workflow_request(
                state_dir,
                correlation_id,
                status="approved",
                actor=str(payload.get("requested_by") or "zf-cli"),
                writer=writer,
                run_id=str(payload.get("run_id") or correlation_id),
                event_type="workflow.request.approved",
            )
    submit_requested = writer.append(ZfEvent(
        type="workflow.submit.requested",
        actor=str(payload.get("requested_by") or "zf-cli"),
        task_id=task,
        correlation_id=correlation_id,
        payload={**payload, "dry_run": False, "preflight_status": preview.get("status")},
    ))
    event_ids = [submit_requested.id]
    if preview["status"] == "STOP":
        rejected = writer.append(ZfEvent(
            type="workflow.submit.rejected",
            actor="zf-cli",
            task_id=task,
            causation_id=submit_requested.id,
            correlation_id=correlation_id,
            payload={
                "request_id": correlation_id,
                "source_event_id": submit_requested.id,
                "reason": "preflight failed",
                "preflight_ref": preview.get("preflight_ref", ""),
                "blockers": preview.get("blockers") or [],
            },
        ))
        event_ids.append(rejected.id)
        return {
            **preview,
            "schema_version": "workflow.submit.apply.v1",
            "dry_run": False,
            "status": "STOP",
            "workflow_invoke_status": "not_requested",
            "next_action": "fix flow preflight blockers before workflow invoke",
            "event_ids": event_ids,
            "state_dir": str(state_dir),
        }
    _claim_submitted_workflow_task(
        state_dir=state_dir,
        task_id=task,
        writer=writer,
        source_event=submit_requested,
        actor=str(payload.get("requested_by") or "zf-cli"),
    )
    accepted = writer.append(ZfEvent(
        type="workflow.submit.accepted",
        actor="zf-cli",
        task_id=task,
        causation_id=submit_requested.id,
        correlation_id=correlation_id,
        payload={
            "request_id": correlation_id,
            "run_id": str(payload.get("run_id") or correlation_id),
            "kind": str(payload.get("kind") or ""),
            "request_kind": str(payload.get("request_kind") or payload.get("kind") or ""),
            "workflow_tier": str(payload.get("workflow_tier") or ""),
            "source_event_id": submit_requested.id,
            "workflow_preflight_ref": payload.get("workflow_preflight_ref", ""),
            "workflow_input_manifest_ref": payload.get("workflow_input_manifest_ref", ""),
            "workflow_prompt_ref": payload.get("workflow_prompt_ref", ""),
            "config_ref": payload.get("config_ref", ""),
            "workflow_request_ref": payload.get("workflow_request_ref", ""),
            "requirement_spec_ref": payload.get("requirement_spec_ref", ""),
            "requirement_spec_digest": payload.get("requirement_spec_digest", ""),
            "request_revision": int(payload.get("request_revision") or 0),
            "goal_id": str(payload.get("goal_id") or ""),
            "workflow_generation": str(
                payload.get("workflow_generation") or ""
            ),
            "workflow_intent": str(
                payload.get("workflow_intent") or ""
            ),
            "workflow_template": str(
                payload.get("workflow_template") or ""
            ),
            "completion_profile": str(
                payload.get("completion_profile") or ""
            ),
            "generic_workflow_contract_digest": str(
                payload.get("generic_workflow_contract_digest") or ""
            ),
            "goal_claim_set_ref": str(
                payload.get("goal_claim_set_ref") or ""
            ),
            "goal_claim_set_digest": str(
                payload.get("goal_claim_set_digest") or ""
            ),
            "workflow_proposal_ref": payload.get("workflow_proposal_ref")
            if isinstance(payload.get("workflow_proposal_ref"), dict) else {},
            "workflow_proposal_digest": str(
                payload.get("workflow_proposal_digest") or ""
            ),
            "effective_config_ref": payload.get("effective_config_ref")
            if isinstance(payload.get("effective_config_ref"), dict) else {},
            "effective_config_digest": str(
                payload.get("effective_config_digest") or ""
            ),
        },
    ))
    event_ids.append(accepted.id)
    if correlation_id:
        from zf.runtime.workflow_requests import mark_workflow_request

        mark_workflow_request(
            state_dir,
            correlation_id,
            status="submitted",
            actor=str(payload.get("requested_by") or "zf-cli"),
            writer=writer,
            run_id=str(payload.get("run_id") or correlation_id),
            event_type="workflow.request.submitted",
        )
    # G0(133):goal 铸造——submit accepted 即 kernel 发 run.goal.started
    # (投影 build_run_goal_projection 已在等这个事件;灰度 goal.enabled)。
    goal_started: ZfEvent | None = None
    if bool(getattr(getattr(config, "goal", None), "enabled", False)):
        objective = str(
            payload.get("objective")
            or payload.get("summary")
            or payload.get("reason")
            or f"deliver workflow submit {correlation_id or task}"
        )
        goal_started = writer.append(ZfEvent(
            type="run.goal.started",
            actor="zf-cli",
            task_id=task,
            causation_id=accepted.id,
            correlation_id=correlation_id,
            payload={
                "objective": objective,
                "run_id": correlation_id or accepted.id,
                "goal_id": str(payload.get("goal_id") or correlation_id),
                "workflow_generation": str(
                    payload.get("workflow_generation") or ""
                ),
                "completion_profile": str(
                    payload.get("completion_profile") or ""
                ),
                "source_refs": [
                    ref for ref in (
                        payload.get("workflow_input_manifest_ref"),
                        payload.get("workflow_prompt_ref"),
                        payload.get("config_ref"),
                    ) if ref
                ],
            },
        ))
        event_ids.append(goal_started.id)
    # Light and standard flows share the same admission edge. The consumer
    # publishes the light entry trigger only after this Run is admitted.
    from zf.runtime.light_flow import light_flow_metadata
    light_metadata = light_flow_metadata(
        config,
        flow_kind=str(payload.get("kind") or ""),
    )
    invoke_payload = _submit_payload_to_workflow_invoke(payload)
    if light_metadata is not None:
        entry_trigger = str(light_metadata.get("light_entry_trigger") or "prd.requested")
        source_refs = (
            dict(payload.get("source_refs") or {})
            if isinstance(payload.get("source_refs"), dict)
            else {}
        )
        source_ref = str(source_refs.get("source_ref") or "")
        requirement_ref = str(
            payload.get("requirement_spec_ref") or ""
        ).strip()
        kind = str(payload.get("kind") or "prd")
        entry_payload = {
            **payload,
            "pdd_id": correlation_id,
            "feature_id": correlation_id,
            "workflow_run_id": correlation_id,
            "trace_id": correlation_id,
            "flow_kind": kind,
            "objective_ref": requirement_ref or source_ref,
            "target_root": str(source_refs.get("target_root") or ""),
            "source": "workflow-submit-light",
        }
        if not requirement_ref:
            if kind == "issue":
                entry_payload["issue_ref"] = source_ref
            else:
                entry_payload["prd_ref"] = source_ref
        invoke_payload.update({
            "light_entry_trigger": entry_trigger,
            "light_entry_payload": entry_payload,
        })
    invoked = writer.append(ZfEvent(
        type="workflow.invoke.requested",
        actor=str(payload.get("requested_by") or "zf-cli"),
        task_id=task,
        causation_id=accepted.id,
        correlation_id=correlation_id,
        payload=invoke_payload,
    ))
    event_ids.append(invoked.id)
    invoke_visibility = _workflow_invoke_visibility(
        writer.event_log.read_all(),
        source_event_id=invoked.id,
    )
    return ensure_submitted_workflow_runtime({
        **preview,
        "schema_version": "workflow.submit.apply.v1",
        "dry_run": False,
        "status": "accepted",
        "event_type": "workflow.submit.accepted",
        "workflow_invoke_event_id": invoked.id,
        "workflow_invoke_status": invoke_visibility["status"],
        "next_action": invoke_visibility["next_action"],
        "event_ids": event_ids,
        "state_dir": str(state_dir),
    }, config_path=config_path, config=config)


def _request_id_from_path(path: Path) -> str:
    stem = path.expanduser().name
    if stem.endswith((".md", ".json")):
        stem = Path(stem).stem
    return stem or _unique_request_id("auto")

def _resolve_submit_task_id(task_id: str, *, request_id: str, kind: str) -> str:
    value = str(task_id or "").strip()
    if value:
        return value
    prefix = {"issue": "ISSUE", "prd": "PRD", "refactor": "REFACTOR"}.get(kind, "FLOW")
    safe = "".join(ch if ch.isalnum() else "-" for ch in request_id.upper()).strip("-")
    return f"{prefix}-{safe or 'REQUEST'}"

def _resolve_submit_pattern_id(
    *,
    config_path: Path,
    pattern_id: str,
    kind: str = "",
    workflow_tier: str = "",
) -> str:
    value = str(pattern_id or "").strip()
    if value:
        return value
    config = load_config(config_path)
    stages = list(getattr(getattr(config, "workflow", None), "stages", []) or [])
    stage_ids = [str(getattr(stage, "id", "") or "").strip() for stage in stages]
    stage_ids = [sid for sid in stage_ids if sid]
    route = _flow_kind_route(config, kind)
    if route is not None:
        tier = str(workflow_tier or getattr(route, "default_tier", "") or "").strip().lower()
        tier_routes = dict(getattr(route, "tier_routes", {}) or {})
        if tier and tier in tier_routes and str(tier_routes[tier] or "").strip():
            return str(tier_routes[tier]).strip()
        if str(getattr(route, "pattern_id", "") or "").strip():
            return str(route.pattern_id).strip()
        raise ConfigError(
            f"workflow.kind_routes.{kind or 'unknown'} resolved but has no pattern_id"
        )
    metadata_kind = _normalize_request_kind(_flow_kind(config))
    requested_kind = _normalize_request_kind(kind)
    if stage_ids and metadata_kind and (
        not requested_kind or requested_kind == metadata_kind
    ):
        return stage_ids[0]
    if len(stage_ids) > 1:
        raise ConfigError(
            f"multiple workflow stages declared ({', '.join(stage_ids[:8])}); "
            "submit requires workflow.kind_routes or explicit --pattern-id"
        )
    for stage in stages:
        sid = str(getattr(stage, "id", "") or "").strip()
        if sid:
            return sid
    return ""

def _flow_kind_route(config: Any, kind: str) -> Any | None:
    routes = dict(getattr(getattr(config, "workflow", None), "kind_routes", {}) or {})
    requested = _normalize_request_kind(kind)
    route = routes.get(requested)
    seen: set[str] = set()
    while route is not None and str(getattr(route, "alias", "") or "").strip():
        if requested in seen:
            raise ConfigError(f"workflow.kind_routes alias cycle at {requested!r}")
        seen.add(requested)
        requested = _normalize_request_kind(str(route.alias))
        route = routes.get(requested)
    return route

def _submit_payload_to_workflow_invoke(payload: dict[str, Any]) -> dict[str, Any]:
    source_refs = payload.get("source_refs") if isinstance(payload.get("source_refs"), dict) else {}
    artifact_refs = payload.get("artifact_refs") if isinstance(payload.get("artifact_refs"), list) else []
    return {
        "task_id": str(payload.get("task_id") or ""),
        "request_id": str(payload.get("request_id") or ""),
        "run_id": str(payload.get("run_id") or payload.get("request_id") or ""),
        "workflow_run_id": str(
            payload.get("run_id") or payload.get("request_id") or ""
        ),
        "kind": str(payload.get("kind") or ""),
        "flow_kind": str(payload.get("kind") or ""),
        "request_kind": str(payload.get("request_kind") or payload.get("kind") or ""),
        "workflow_tier": str(payload.get("workflow_tier") or ""),
        "pattern_id": str(payload.get("pattern_id") or ""),
        "requested_by": str(payload.get("requested_by") or "zf-cli"),
        "reason": str(payload.get("reason") or "workflow submit accepted"),
        "source": "workflow-submit",
        "source_refs": dict(source_refs),
        "workflow_input_manifest_ref": str(payload.get("workflow_input_manifest_ref") or ""),
        "workflow_prompt_ref": str(payload.get("workflow_prompt_ref") or ""),
        "workflow_request_ref": str(payload.get("workflow_request_ref") or ""),
        "requirement_spec_ref": str(payload.get("requirement_spec_ref") or ""),
        "requirement_spec_digest": str(payload.get("requirement_spec_digest") or ""),
        "request_revision": int(payload.get("request_revision") or 0),
        "origin_binding": (
            dict(payload["origin_binding"])
            if isinstance(payload.get("origin_binding"), dict)
            else {}
        ),
        "goal_id": str(
            payload.get("goal_id")
            or payload.get("run_id")
            or payload.get("request_id")
            or ""
        ),
        "workflow_generation": str(
            payload.get("workflow_generation")
            or payload.get("workflow_proposal_digest")
            or ""
        ),
        "workflow_intent": str(payload.get("workflow_intent") or ""),
        "workflow_template": str(payload.get("workflow_template") or ""),
        "completion_profile": str(
            payload.get("completion_profile") or ""
        ),
        "generic_workflow_contract_digest": str(
            payload.get("generic_workflow_contract_digest") or ""
        ),
        "required_delivery_artifacts": [
            dict(item)
            for item in payload.get("required_delivery_artifacts") or []
            if isinstance(item, dict)
        ],
        "goal_claim_set_ref": str(
            payload.get("goal_claim_set_ref") or ""
        ),
        "goal_claim_set_digest": str(
            payload.get("goal_claim_set_digest") or ""
        ),
        "workflow_proposal_ref": (
            dict(payload["workflow_proposal_ref"])
            if isinstance(payload.get("workflow_proposal_ref"), dict)
            else {}
        ),
        "workflow_proposal_digest": str(
            payload.get("workflow_proposal_digest") or ""
        ),
        "effective_config_ref": (
            dict(payload["effective_config_ref"])
            if isinstance(payload.get("effective_config_ref"), dict)
            else {}
        ),
        "effective_config_digest": str(
            payload.get("effective_config_digest") or ""
        ),
        "run_contract_ref": str(payload.get("run_contract_ref") or ""),
        "run_contract_digest": str(
            payload.get("run_contract_digest") or ""
        ),
        "prompt_kind": str(payload.get("kind") or ""),
        "artifact_refs": [{"path": str(ref)} for ref in artifact_refs if str(ref).strip()],
        "expected_output": f"execute {payload.get('kind') or 'workflow'} workflow",
    }

def _workflow_invoke_visibility(
    events: list[ZfEvent],
    *,
    source_event_id: str,
) -> dict[str, str]:
    for event in reversed(events):
        if event.type not in {
            "workflow.invoke.accepted",
            "workflow.invoke.rejected",
            "run.admission.queued",
            "run.admission.admitted",
            "run.admission.rejected",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("source_event_id") or "") != source_event_id:
            continue
        if event.type == "workflow.invoke.accepted":
            return {
                "status": "accepted",
                "next_action": "watch fanout/task events; workflow invoke was consumed by the orchestrator",
            }
        if event.type == "run.admission.queued":
            return {
                "status": "queued",
                "next_action": "the request is queued behind the active Project Run",
            }
        if event.type == "run.admission.admitted":
            return {
                "status": "admitted",
                "next_action": "the Run is admitted; wait for the invoke consumer outcome",
            }
        return {
            "status": "rejected",
            "next_action": "inspect workflow.invoke.rejected reason and resubmit after correction",
        }
    return {
        "status": "pending_consumer",
        "next_action": "ensure `zf start` watcher is running so workflow.invoke.requested is consumed",
    }

def _public_preflight_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if not str(key).startswith("_")}
