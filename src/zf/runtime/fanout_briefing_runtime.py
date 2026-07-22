"""Fanout child briefing rendering for the coordination runtime."""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import RoleConfig
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.task_contract_snapshot import (
    descriptor_from_payload as contract_descriptor_from_payload,
    hydrate_task_contract_snapshot,
)
from zf.runtime.workflow_inputs import render_workflow_input_briefing_section


_HANDOFF_REF_FIELDS = (
    "prd_ref", "task_map_ref", "artifact_refs", "evidence_refs", "source_index_ref",
)

_PRD_STAGE_LOOSE_FALLBACK = {
    "prd.ready": ("prd_ref", "artifact_refs", "evidence_refs"),
    "prd.approved": ("prd_ref", "artifact_refs", "evidence_refs"),
    "task_map.ready": ("task_map_ref", "artifact_refs", "evidence_refs"),
}


def _contract_handoff_ref_fields(config, success_event: str) -> list[str]:
    profile_name = str(
        getattr(getattr(config, "project", None), "schema_profile", "") or ""
    ).strip()
    profile = config.schema_profiles.get(profile_name) if profile_name else None
    required = list(getattr(profile, "required_fields", {}).get(success_event, ())) if profile else []
    fields = [field for field in required if field in _HANDOFF_REF_FIELDS]
    if fields:
        return fields
    return list(_PRD_STAGE_LOOSE_FALLBACK.get(success_event, ()))


class FanoutBriefingMixin:
    def _write_fanout_briefing(
        self,
        *,
        role: RoleConfig,
        context,
        child_id: str,
        run_id: str,
        aggregate,
        child_payload: dict | None = None,
        skill_entries: list | None = None,
    ) -> Path:
        import json
        import shlex

        briefings_dir = self.state_dir / "briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        path = briefings_dir / f"{role.instance_id}-{context.fanout_id}-{child_id}.md"
        child_payload = child_payload if isinstance(child_payload, dict) else {}
        from zf.runtime.affinity_review_scope import (
            affinity_scope_briefing_lines,
            affinity_scope_identity_errors,
        )

        identity_errors = affinity_scope_identity_errors(
            child_payload,
            role_instance=role.instance_id,
        )
        if identity_errors:
            raise RuntimeError(
                "fanout affinity child identity invalid: "
                + ", ".join(identity_errors)
            )

        trigger_payload = (
            child_payload.get("trigger_payload")
            if isinstance(child_payload.get("trigger_payload"), dict)
            else {}
        )
        success_payload = {
            "fanout_id": context.fanout_id,
            "stage_id": context.stage_id,
            "child_id": child_id,
            "run_id": run_id,
            "role_instance": role.instance_id,
            "status": "completed",
            "report": {
                "child_id": child_id,
                "status": "passed",
                "summary": "Short outcome summary.",
                "findings": [],
                "recommendation": "approve",
            },
        }
        contract_snapshot: dict = {}
        is_goal_closure = bool(
            str(child_payload.get("closure_identity") or "").strip()
            and str(child_payload.get("goal_claim_set_ref") or "").strip()
        )
        if is_goal_closure:
            from zf.runtime.goal_closure_identity import (
                validate_goal_closure_dispatch_snapshots,
            )
            from zf.runtime.sidecar_refs import hydrate_sidecar_ref

            validate_goal_closure_dispatch_snapshots(self.state_dir, child_payload)
            claim_set = hydrate_sidecar_ref(
                self.state_dir,
                {
                    "ref": str(child_payload.get("goal_claim_set_ref") or ""),
                    "sha256": str(child_payload.get("goal_claim_set_digest") or ""),
                },
            ).payload
            claims = (
                claim_set.get("claims")
                if isinstance(claim_set, dict) and isinstance(claim_set.get("claims"), list)
                else []
            )
            result_refs = [
                str(ref) for ref in child_payload.get("input_result_refs") or []
                if str(ref or "").strip()
            ]
            for key in (
                "workflow_run_id",
                "task_map_generation",
                "contract_snapshot_ref",
                "contract_snapshot_digest",
                "target_snapshot_ref",
                "target_snapshot_digest",
                "target_commit",
                "goal_id",
                "flow_kind",
                "goal_claim_set_ref",
                "goal_claim_set_digest",
                "closure_fact_ref",
                "closure_fact_digest",
            ):
                value = child_payload.get(key)
                if value not in (None, ""):
                    success_payload[key] = value
            success_payload["goal_closure_result"] = {
                "schema_version": "goal-closure-result.v1",
                "workflow_run_id": str(child_payload.get("workflow_run_id") or ""),
                "goal_id": str(child_payload.get("goal_id") or ""),
                "flow_kind": str(child_payload.get("flow_kind") or ""),
                "task_map_generation": str(child_payload.get("task_map_generation") or ""),
                "target_commit": str(child_payload.get("target_commit") or ""),
                "objective_ref": str(child_payload.get("objective_ref") or ""),
                "goal_claim_set_ref": str(child_payload.get("goal_claim_set_ref") or ""),
                "goal_claim_set_digest": str(child_payload.get("goal_claim_set_digest") or ""),
                "planning_result_ref": str(child_payload.get("planning_result_ref") or ""),
                "candidate_ref": str(child_payload.get("candidate_ref") or ""),
                "closure_fact_ref": str(child_payload.get("closure_fact_ref") or ""),
                "closure_fact_digest": str(child_payload.get("closure_fact_digest") or ""),
                "verdict": "passed",
                "goal_coverage": [
                    {
                        "goal_claim_id": str(claim.get("goal_claim_id") or ""),
                        "status": "closed",
                        "supporting_result_refs": result_refs,
                    }
                    for claim in claims
                    if isinstance(claim, dict)
                    and str(claim.get("goal_claim_id") or "").strip()
                ],
                "input_result_refs": result_refs,
                "open_gap_refs": [],
                "recommended_action": "complete",
                "summary": "All mandatory Goal claims are closed by admitted results.",
            }
        elif str(child_payload.get("contract_snapshot_ref") or "").strip():
            contract_snapshot = hydrate_task_contract_snapshot(
                self.state_dir,
                contract_descriptor_from_payload(child_payload),
                expected={"task_id": str(child_payload.get("task_id") or "")},
            )
            for key in (
                "workflow_run_id",
                "task_id",
                "contract_revision",
                "task_map_generation",
                "base_commit",
                "task_ref",
                "contract_snapshot_ref",
                "contract_snapshot_digest",
                "target_snapshot_ref",
                "target_commit",
                "target_snapshot_digest",
            ):
                value = (
                    child_payload.get(key)
                    if key not in contract_snapshot
                    else contract_snapshot.get(key)
                )
                if value not in (None, ""):
                    success_payload[key] = value
            criteria = contract_snapshot.get("acceptance_criteria") or []
            success_payload["verification_result"] = {
                "schema_version": "verification-result.v1",
                "execution_status": "completed",
                "verdict": "passed",
                "verification_owner": "task_verify",
                "verification_tier": "runtime",
                "requirement_results": [
                    {
                        "acceptance_id": str(item.get("acceptance_id") or ""),
                        "status": "passed",
                        "verification_owner": str(item.get("verification_owner") or "task_verify"),
                        "verification_tier": str(item.get("verification_tier") or "runtime"),
                        "evidence_refs": ["artifact-or-event-ref"],
                        "findings": [],
                        "reproduction_commands": [],
                    }
                    for item in criteria
                    if isinstance(item, dict)
                ],
            }
        for key in (
            "workflow_run_id", "operation_id", "parent_operation_id",
            "request_hash", "attempt_id", "result_protocol_mode",
            "attempt_source_manifest_ref", "attempt_source_manifest_digest",
            "attempt_source_manifest", "input_consumption_policy_ref",
            "input_consumption_policy", "input_consumption_policy_digest",
            "required_reads",
        ):
            value = child_payload.get(key)
            if value not in (None, "", [], {}):
                success_payload[key] = value
        failure_payload = {
            **success_payload,
            "status": "failed",
            "reason": "Blocking finding.",
            "report": {
                "child_id": child_id,
                "status": "failed",
                "summary": "Short failure summary.",
                "findings": [],
                "recommendation": "reject",
            },
        }
        if contract_snapshot:
            failure_payload["verification_result"] = {
                **success_payload["verification_result"],
                "verdict": "rejected",
                "requirement_results": [
                    {
                        **item,
                        "status": "failed",
                        "evidence_refs": ["artifact-or-event-ref"],
                        "findings": [{"message": "Blocking acceptance finding."}],
                        "reproduction_commands": ["command that reproduces the finding"],
                    }
                    for item in success_payload["verification_result"]["requirement_results"]
                ],
            }
        success_event = str(getattr(aggregate, "success_event", "") or "")
        failure_event = str(getattr(aggregate, "failure_event", "") or "")
        child_success_event, child_failure_event = self._fanout_child_result_events(
            aggregate,
        )
        config = getattr(self, "config", None)
        workflow = getattr(config, "workflow", None)
        stages = getattr(workflow, "stages", []) or []
        stage = next(
            (
                s for s in stages
                if getattr(s, "id", "") == context.stage_id
            ),
            None,
        )
        stage_instruction_values = (
            getattr(getattr(stage, "criteria", None), "instructions", [])
            if stage is not None else []
        )
        stage_instruction_lines: list[str] = []
        if stage_instruction_values:
            stage_instruction_lines.extend([
                "## Stage Intent",
                "",
                "Follow these configured stage instructions. They explain the "
                "workflow meaning of this reader stage; they are guidance, not "
                "additional mechanical success criteria.",
                "",
            ])
            stage_instruction_lines.extend(
                f"- {str(item).strip()}"
                for item in stage_instruction_values
                if str(item).strip()
            )
            stage_instruction_lines.append("")
        # FIX-14(bizsim r4 F14):briefing 样例与 schema 合约配对。schema 对
        # report 要求的字段必须出现在样例里——r4 全轮 9 份 verify report
        # 矩阵全 0 行,根因即"校验器不带教育"(payload-contract 教训重演)。
        success_payload["report"].update(self._schema_education_report_fields(
            child_success_event, existing=success_payload["report"],
        ))
        failure_payload["report"].update(self._schema_education_report_fields(
            child_failure_event, existing=failure_payload["report"],
        ))
        # LB-4:顶层 required/non_empty 字段(summary/evidence_refs)同样进样例。
        success_payload.update(self._schema_education_toplevel_fields(
            child_success_event, existing=success_payload,
        ))
        failure_payload.update(self._schema_education_toplevel_fields(
            child_failure_event, existing=failure_payload,
        ))
        if contract_snapshot:
            binding_fields = (
                "workflow_run_id",
                "task_id",
                "contract_revision",
                "task_map_generation",
                "base_commit",
                "task_ref",
                "contract_snapshot_ref",
                "contract_snapshot_digest",
                "target_snapshot_ref",
                "target_commit",
                "target_snapshot_digest",
            )
            for result_payload in (success_payload, failure_payload):
                verification_result = result_payload["verification_result"]
                for key in binding_fields:
                    value = result_payload.get(key)
                    if value not in (None, ""):
                        verification_result[key] = value
        is_refactor_review = success_event == "zaofu.refactor.review.ready"
        is_refactor_plan = success_event in {
            "zaofu.refactor.plan.ready",
            "refactor.plan.ready",
        }
        is_plan_artifact_stage = self._is_plan_artifact_stage(
            role=role,
            stage_id=str(context.stage_id),
            success_event=success_event,
            child_success_event=child_success_event,
        )
        if is_refactor_review:
            success_payload["report"].update({
                "coverage_matrix": [{
                    "subsystem": child_id,
                    "inspected_paths": [],
                    "evidence_refs": [],
                    "coverage": "partial",
                    "uncovered": [],
                }],
                "evidence_refs": [],
                "uncovered": [],
                "confidence": "confirmed|inferred|uncovered",
            })
            failure_payload["reason"] = (
                "Unable to produce a coverage/evidence-backed review report."
            )
            failure_payload["report"].update({
                "coverage_matrix": [],
                "evidence_refs": [],
                "uncovered": ["Unable to inspect assigned scope."],
            })
        elif is_refactor_plan:
            review_artifact_ref = str(
                trigger_payload.get("review_artifact_ref")
                or "Path to the review artifact used."
            )
            plan_intent = str(trigger_payload.get("plan_intent") or "")
            refactor_contract = (
                child_payload.get("refactor_contract")
                if isinstance(child_payload.get("refactor_contract"), dict)
                else {}
            )
            scan_quality_audit_ref = (
                "Path to scan-quality-audit.json proving scan inputs were "
                "consumed before task_map synthesis."
            )
            success_payload.update({
                "scan_quality_audit_ref": scan_quality_audit_ref,
                "artifact_refs": [scan_quality_audit_ref],
                "artifact_digests": {},
            })
            if refactor_contract:
                success_payload["refactor_contract"] = dict(refactor_contract)
            success_payload["report"].update({
                "review_artifact_ref": review_artifact_ref,
                "plan_intent": plan_intent,
                "refactor_contract": refactor_contract,
                "scan_quality_audit_ref": scan_quality_audit_ref,
                "refactor_plan_md": "## Refactor Plan\n\nReplace with the final plan.",
                "task_map": {"tasks": []},
                "gates": [],
                "risk_register": [],
                "backlog_candidates": [],
                "artifact_refs": [scan_quality_audit_ref],
                "evidence_refs": [],
            })
            failure_payload["reason"] = (
                "Unable to produce a plan artifact from the review artifact."
            )
            failure_payload["report"].update({
                "review_artifact_ref": review_artifact_ref,
                "plan_intent": plan_intent,
                "missing_fields": [],
            })

        def _emit_command(
            event_type: str,
            payload: dict,
            *,
            payload_file: str = "",
        ) -> str:
            if not event_type:
                return "# no event configured"
            cli_parts = shlex.split(zf_cli_cmd()) or ["zf"]
            payload_args = (
                ["--payload-file", shlex.quote(payload_file)]
                if payload_file
                else ["--payload", shlex.quote(json.dumps(payload, ensure_ascii=False))]
            )
            return " ".join([
                *[shlex.quote(part) for part in cli_parts],
                "emit",
                shlex.quote(event_type),
                "--actor",
                shlex.quote(role.instance_id),
                "--state-dir",
                shlex.quote(str(self.state_dir)),
                *payload_args,
            ])

        payload_section: list[str] = []
        if child_payload:
            from zf.runtime.injection import materialize_instruction_refs
            from zf.runtime.artifact_read_ledger import render_attempt_source_briefing

            child_payload = materialize_instruction_refs(
                child_payload, project_root=self.project_root,
            )
            controlled_inputs = render_attempt_source_briefing(child_payload).strip()
            if controlled_inputs:
                payload_section.extend([*controlled_inputs.splitlines(), ""])
            instruction = str(
                child_payload.get("instruction")
                or child_payload.get("summary")
                or ""
            ).strip()
            payload_section.extend([
                "## Child-Specific Context",
                "",
                "Treat this workflow child payload as the verification scope for this run:",
                "```json",
                json.dumps(child_payload, ensure_ascii=False, indent=2),
                "```",
                "",
            ])
            if instruction:
                payload_section.extend([
                    "Instruction:",
                    instruction,
                "",
            ])
        # r6-F4(F6 缺口实弹):waiver 只渲染进 injection 路径,fanout child
        # briefing 不带 → verifier 看不见 operator 豁免令,waive 对 fanout
        # 审角色无效。所有 child briefing 统一携带活跃 waiver 清单。
        waiver_section: list[str] = []
        try:
            from zf.runtime.waivers import load_active_waivers

            waiver_task = str(
                (child_payload or {}).get("task_id")
                or (child_payload or {}).get("upstream_task_id")
                or "*"
            )
            waivers = load_active_waivers(self.state_dir, waiver_task)
            if waivers:
                waiver_section = [
                    "## Active Operator Waivers (verification.waived)",
                    "",
                    "Operator 已豁免下列验证要求;凡命中 signature 的缺失/失败",
                    "不得作为拒收依据(裁决事件化,truth 在 events.jsonl):",
                    "",
                ]
                for waiver in waivers[:10]:
                    waiver_section.append(
                        f"- signature: {waiver.get('signature', '')}"
                    )
                    reason = str(waiver.get("reason") or "")[:300]
                    if reason:
                        waiver_section.append(f"  reason: {reason}")
                waiver_section.append("")
        except Exception:
            waiver_section = []
        payload_section.extend(waiver_section)
        # G4/U21(灰度 goal.enabled):goal 块 + 地面真值/完成自检条款,
        # 文案在 sibling goal_briefing.py。
        try:
            from zf.runtime.goal_briefing import goal_briefing_section

            payload_section.extend(goal_briefing_section(
                self.event_log.read_all(), config=self.config,
            ))
        except Exception:
            pass
        # r6-F2:required_runtime_evidence 的命名合同以前只活在验收矩阵,
        # dev 交语义等价物被按字面拒(四轮 cap 耗在文件名官僚)。凡 child
        # payload 携带该声明,briefing 明示精确路径清单。
        evidence_names: list[str] = []
        for source in (
            child_payload or {},
            (child_payload or {}).get("raw_task") or {},
            (child_payload or {}).get("payload") or {},
        ):
            raw = source.get("required_runtime_evidence") if isinstance(source, dict) else None
            if isinstance(raw, list):
                evidence_names.extend(str(item) for item in raw if str(item or "").strip())
        if evidence_names:
            payload_section.extend([
                "## Required Runtime Evidence (exact paths)",
                "",
                "验收按**精确路径字面**匹配下列文件;语义等价但异名的产物",
                "会被拒收。逐一生成并 commit:",
                "",
                *[f"- `{name}`" for name in dict.fromkeys(evidence_names)],
                "",
            ])
        workflow_input_section = render_workflow_input_briefing_section(
            child_payload,
        ).strip()
        workflow_input_lines = (
            [*workflow_input_section.splitlines(), ""]
            if workflow_input_section
            else []
        )

        result_guidance = [
            "Finding schema: use `severity` = info|low|medium|high|critical, `path`, `message`, and optional integer `line`.",
            "`fanout_id`, `stage_id`, `child_id`, `run_id`, `role_instance`, and `status` must stay as top-level payload fields; do not place them only inside `report`.",
        ]
        if contract_snapshot:
            result_guidance.append(
                "For `verification_result.requirement_results[].status`, use only "
                "`passed`, `failed`, `blocked`, `waived`, or `not_applicable`; "
                "a `rejected` verdict requires at least one `failed` requirement."
            )
        if (
            success_event == "flow.discovery.completed"
            and failure_event == "flow.discovery.failed"
        ):
            result_guidance.extend([
                "A blocking product gap is a completed semantic discovery: emit the failure event with bounded `report.gap_tasks`; the kernel will amend the task map instead of rescanning the unchanged candidate.",
                "Every gap task MUST use the canonical task-map shape: non-empty `task_id`, `owner_role`, `claim_paths` or `allowed_paths`, `acceptance` or `acceptance_criteria`, `verify_commands` or `verification`, and `source_refs`.",
                "Use `task_id`, not a bare `id`; `acceptance_refs` and `verification_commands` do not replace the canonical acceptance and verification fields.",
                "Gap tasks MUST NOT claim overlapping paths. Combine related fixes into one task or give each task disjoint file ownership; ordering does not make duplicate path ownership valid.",
                "Keep suggestions out of `gap_tasks`; only implementation work required to close a blocking goal claim belongs there.",
            ])
        if is_refactor_review:
            result_guidance.extend([
                "For this refactor review workflow, finding severity describes planning risk.",
                "Emit the success event when the review report is complete, even if findings include `high` or `critical` items.",
                "For a complete review report, keep `report.status` as `passed` and `report.recommendation` as `approve`; put caveats in findings, risks, refactor_slices, and summary.",
                "Do not invent custom recommendation values; valid values are `approve`, `reject`, `needs_rework`, and `abstain`.",
                "Emit the failure event only when you cannot inspect the assigned scope or cannot provide `coverage_matrix` / `evidence_refs`.",
                "Replace placeholder arrays in the success payload with actual coverage, evidence, uncovered areas, findings, and refactor slices.",
            ])
        elif is_refactor_plan:
            result_guidance.extend([
                "For this refactor plan workflow, emit the success event only when `refactor_plan_md`, `task_map`, and `gates` are complete.",
                "For a complete plan artifact, keep `report.status` as `passed` and `report.recommendation` as `approve`.",
                "Do not invent custom recommendation values; valid values are `approve`, `reject`, `needs_rework`, and `abstain`.",
                "Use the provided `review_artifact_ref` and `plan_intent`; do not invent facts for uncovered review areas.",
                "Emit the failure event only when the plan artifact cannot be produced.",
                *self._plan_artifact_contract_lines(),
            ])
            refactor_contract = (
                child_payload.get("refactor_contract")
                if isinstance(child_payload.get("refactor_contract"), dict)
                else {}
            )
            if refactor_contract:
                result_guidance.extend([
                    "Workflow refactor contract is included in Child-Specific Context as `refactor_contract`:",
                    json.dumps(refactor_contract, ensure_ascii=False),
                    "If `assembly_policy` is `declared_task`, do not emit success unless `task_map.tasks` includes `assembly_task_id` or one task has `root_owner_class: \"assembly\"`.",
                    "If `assembly_policy` is `none`, a one-bundle serial plan may omit assembly, but every task still needs owned paths and source anchors.",
                ])
        elif is_plan_artifact_stage:
            plan_ref = f"docs/plans/{context.stage_id}-{child_id}-plan.md"
            # prod-e2e(2026-07-04 prd 轮实弹,F4 契约分叉 prd 变体):
            # success_event 是 task_map.ready 的 plan 子任务,briefing 曾
            # 预填 task_map_ref="" 且只讲 markdown 合同 → planner 100%
            # 履约地交了 .md,下游 writer admission 按 JSON 拒收,死端。
            # briefing 合同必须与 admission 合同一致:预填 JSON 路径 +
            # 明文 schema 要求。
            produces_task_map = success_event == "task_map.ready"
            task_map_ref_prefill = (
                "artifacts/plan/task_map.json"
                if produces_task_map else ""
            )
            success_payload.update({
                "plan_artifact_ref": plan_ref,
                "artifact_refs": (
                    [plan_ref, task_map_ref_prefill]
                    if produces_task_map else [plan_ref]
                ),
                "evidence_refs": [],
            })
            if produces_task_map:
                success_payload["task_map_ref"] = task_map_ref_prefill
            success_payload["report"].update({
                "plan_artifact_ref": plan_ref,
                "plan_md": "## Plan\n\nReplace with the durable plan artifact content.",
                "task_map_ref": task_map_ref_prefill,
                "backlog_ref": "",
                "source_index_ref": "",
                "evidence_refs": [],
            })
            result_guidance.extend(self._plan_artifact_contract_lines())
            if produces_task_map:
                result_guidance.extend([
                    "THIS stage's success event is `task_map.ready`: write the JSON "
                    f"task map to the workdir-relative path `{task_map_ref_prefill}` "
                    "before emitting success. Report that same relative ref in the "
                    "payload; the kernel relocates it into runtime artifact storage. "
                    "Do not write the configured state dir directly.",
                    "The task map is JSON (not markdown): {\"tasks\": [{\"task_id\", "
                    "\"title\", \"description\", \"allowed_paths\", \"verification\", "
                    "\"acceptance_criteria\"}...]} — downstream writer-fanout admission "
                    "rejects anything that is not valid JSON matching this contract.",
                    "Keep the markdown plan as the human-readable companion; the JSON "
                    "task map is the machine handoff.",
                    # C1(finding-5/7):共享约定单源——布局/路径口径分属
                    # 多任务时,谁也没有权威版本,verify 只能逐层考古。
                    "CROSS-TASK CONVENTIONS: the task map MUST include a top-level "
                    "`shared_conventions` object that fixes every convention more "
                    "than one task depends on — at minimum `test_path_prefix` "
                    "(single directory prefix all test files live under), plus "
                    "package layout, `target_root`, `package_root`, "
                    "`packaging_file`, and entrypoint naming where relevant. Every "
                    "task's repo-relative allowed_paths and verification MUST follow it "
                    "(admission validates test_path_prefix mechanically).",
                    "PATH RULE: verification commands may run `cd <target_root>`, "
                    "but task ownership stays repo-relative. If test_path_prefix is "
                    "`app/tests/`, do not list or verify against bare `tests/...` "
                    "unless the task also owns the corresponding `app/tests/...` path.",
                    "Declare REAL dependencies: if task B imports code owned by "
                    "task A, B `blocked_by` A — parallel waves of secretly "
                    "coupled tasks produce convention races and doomed slice "
                    "verification.",
                    # C3(finding-6):greenfield 首波单任务立骨架
                    "GREENFIELD RULE: when the target root is empty/new, wave 1 "
                    "MUST be a single scaffolding task (package metadata, "
                    "directory layout, conventions doc). The scaffold task must "
                    "own package metadata such as `package.json`, `pyproject.toml`, "
                    "`setup.py`, `setup.cfg`, `tsconfig.json`, or lockfiles. "
                    "Parallel implementation starts at wave 2 on top of the scaffold.",
                    # C2(finding-2/14):验证层级
                    "VERIFICATION LEVELS: a slice task's `verification` runs on "
                    "its ISOLATED task branch — only slice-local checks belong "
                    "there. System-level checks (package install, console-script "
                    "smoke, cross-package integration) go to the integration/"
                    "test stage, NOT to slice tasks (admission rejects installs "
                    "in slice verification).",
                ])
        else:
            # Planning/gate aggregates (prd.ready/prd.approved/task_map.ready,
            # and any DAG flow whose contract declares handoff refs) fall through
            # the plan/refactor branches above with a plain reader template that
            # has no prd_ref/evidence_refs slot. The aggregate
            # `_generic_fanout_success_payload` COLLECTS evidence_refs from the
            # children — finding none it synthesizes `evidence_refs: []` and the
            # gate loops on `prd.blocked: requires evidence_refs` (ledger e2e
            # 2026-06-20). Derive the required slot from the event contract
            # (event_schemas) instead of hardcoding event names: this stays a
            # single source of truth with the gate and generalizes to custom DAG
            # flows; loose-mode falls back to the PRD-stage defaults.
            handoff_refs = _contract_handoff_ref_fields(self.config, success_event)
            if handoff_refs:
                prd_ref = str(
                    trigger_payload.get("prd_ref") or "docs/prd/<product>.md"
                )
                task_map_ref = str(
                    trigger_payload.get("task_map_ref")
                    or f"{self.state_dir}/artifacts/task_map.json"
                )
                primary_ref = (
                    task_map_ref if "task_map_ref" in handoff_refs else prd_ref
                )
                seed: dict = {}
                for field_name in handoff_refs:
                    if field_name == "prd_ref":
                        seed["prd_ref"] = prd_ref
                    elif field_name == "task_map_ref":
                        seed["task_map_ref"] = task_map_ref
                    else:  # artifact_refs / evidence_refs / source_index_ref
                        seed[field_name] = [primary_ref]
                success_payload.update(seed)
                success_payload["report"].update(seed)
                result_guidance.append(
                    f"This is a planning/gate aggregate. Success event "
                    f"`{success_event}` REQUIRES non-empty {handoff_refs} per the "
                    f"workflow contract — empty makes the kernel emit "
                    f"`{failure_event}` and route rework. Replace the placeholders "
                    f"with the real artifact you wrote, keep the primary ref inside "
                    f"`artifact_refs`, and put concrete pointers in `evidence_refs` "
                    f"(git:<sha>, the artifact path, the source document, or the "
                    f"trigger event id); never leave them empty."
                )
            else:
                result_guidance.append(
                    "Use `high` or `critical` for blocking findings; do not invent new severity names."
                )

        # A3:candidate.ready 触发的读者(终审 judge/整体 review)拿到
        # 明确的受审对象——candidate 分支+头,而非 ship 目的地。
        # LB-5(r3 light 误拒):candidate_ref 缺席的验收读者(light 终审
        # 拿不到 candidate.ready payload)也必须拿到受审对象语义,否则
        # judge 评空 target 树、把 target_ref 不可解析当拒因。
        candidate_eval_ref = str(
            trigger_payload.get("candidate_ref")
            or trigger_payload.get("branch")
            or child_payload.get("candidate_ref")
            or ""
        ).strip()
        candidate_eval_head = str(
            trigger_payload.get("candidate_head_commit")
            or trigger_payload.get("head_commit")
            or child_payload.get("candidate_head_commit")
            or ""
        ).strip()
        subject_pdd_id = str(
            trigger_payload.get("pdd_id")
            or trigger_payload.get("feature_id")
            or child_payload.get("pdd_id")
            or ""
        ).strip()
        candidate_prefix = str(getattr(
            getattr(
                getattr(getattr(self, "config", None), "runtime", None),
                "git", None,
            ),
            "candidate_branch_prefix",
            "candidate",
        ) or "candidate")
        from zf.runtime.report_evidence_gate import is_verification_stage

        verification_reader = is_verification_stage(
            stage_id=str(context.stage_id or ""),
            event_type=str(child_success_event or ""),
        )
        success_payload_file = ""
        if is_goal_closure:
            from zf.runtime.call_result_envelope import write_immutable_json_sidecar

            descriptor = write_immutable_json_sidecar(
                self.state_dir,
                success_payload,
                root=f"attempts/{run_id}/completion-payloads",
                kind="fanout_child_completion_payload",
                schema_version="fanout-child-completion-payload.v1",
                created_by="fanout-briefing-runtime",
                source_event_id=str(context.trigger_event_id or ""),
            )
            success_payload_file = str(
                self.state_dir.expanduser().resolve() / descriptor["ref"]
            )
        path.write_text(
            "\n".join([
                f"# Fanout Reader Child: {child_id}",
                "",
                f"- fanout_id: `{context.fanout_id}`",
                f"- stage_id: `{context.stage_id}`",
                f"- run_id: `{run_id}`",
                f"- target_ref: `{context.target_ref}`",
                "",
                # A3(v3 judge 顺序缺陷,三流 3/3 + prd-goal e2e 第 4 次
                # 复现):candidate.ready 触发的终审必须评 candidate 本身;
                # target_ref 是 ship 之后的合流目的地,审时可能不存在/为空。
                *(
                    [
                        f"- candidate_ref: `{candidate_eval_ref}`",
                        f"- candidate_head_commit: `{candidate_eval_head}`",
                        "",
                        "EVALUATE THE CANDIDATE: judge/inspect `candidate_ref` at "
                        "`candidate_head_commit` — this is the deliverable under "
                        "review. `target_ref` is only the merge DESTINATION after "
                        "ship; it may be unresolved or stale at review time and its "
                        "state MUST NOT be a rejection reason.",
                        "",
                    ]
                    if candidate_eval_ref
                    # LB-5:candidate_ref 缺席的验收读者仍拿受审对象语义,
                    # 不许把 target_ref 状态当拒因(scan/plan 读者不适用)。
                    else [
                        "SUBJECT OF REVIEW: no candidate_ref accompanied this "
                        "dispatch. If a deliverable branch exists (default "
                        f"prefix `{candidate_prefix}/`"
                        + (
                            f", e.g. `{candidate_prefix}/{subject_pdd_id}`"
                            if subject_pdd_id else ""
                        )
                        + "), evaluate THAT branch at its head. `target_ref` is "
                        "only the merge DESTINATION after ship; it may be "
                        "unresolved or empty at review time and its state MUST "
                        "NOT be a rejection reason.",
                        "",
                    ]
                    if verification_reader
                    else []
                ),
                "Evaluate the target ref as a read-only fanout child."
                if not candidate_eval_ref and not verification_reader
                else "Read-only fanout child. Do not modify project source files.",
                "Do not modify project source files.",
                "",
                *stage_instruction_lines,
                # B3 (R20): affinity lanes inspect ONLY their slice — not the full
                # candidate — so a large candidate doesn't exhaust review context.
                *affinity_scope_briefing_lines(child_payload),
                *self._skill_briefing_section(role, skill_entries),
                *workflow_input_lines,
                *payload_section,
                "Use the runtime state dir explicitly because this role may run from a detached workdir.",
                "",
                "Success command:",
                "```bash",
                _emit_command(
                    child_success_event,
                    success_payload,
                    payload_file=success_payload_file,
                ),
                "```",
                "",
                "Failure command:",
                "```bash",
                _emit_command(child_failure_event, failure_payload),
                "```",
                "",
                "Do not emit the aggregate success/failure event directly; the kernel publishes it after the fanout barrier or synth role finishes.",
                "",
                *result_guidance,
                "",
                "Emit-once protocol: the result event is consumed asynchronously — you will",
                "NOT receive an acknowledgement. Emitting succeeds when the command exits 0.",
                "NEVER re-emit the same completion (no retry loops, no periodic re-sends):",
                "if this fanout generation was superseded, every duplicate is marked",
                "stale_completion and discarded, and re-sending floods the event log",
                "(r10 forensics: one lane re-emitting every ~7s produced 4.5k junk rows).",
                "After emitting once, stop and wait for new instructions.",
                "",
                "When finished, emit exactly one result event with this payload:",
                "```json",
                json.dumps(success_payload, indent=2),
                "```",
                f"Child success event: `{child_success_event}`",
                f"Child failure event: `{child_failure_event}`",
                f"Aggregate success event: `{success_event}`",
                f"Aggregate failure event: `{failure_event}`",
                "",
            ]),
            encoding="utf-8",
        )
        return path
