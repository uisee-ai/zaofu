"""Fanout child briefing rendering for the coordination runtime."""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import RoleConfig
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.fanout_artifact_refs import refactor_plan_workdir_refs
from zf.runtime.fanout_execution_boundary import (
    fanout_task_id,
    render_execution_boundary,
)
from zf.runtime.impl_self_check import (
    descriptor_from_payload as self_check_descriptor_from_payload,
    hydrate_impl_self_check,
    reusable_command_receipts,
)
from zf.runtime import skill_briefing
from zf.runtime.task_contract_snapshot import (
    descriptor_from_payload as contract_descriptor_from_payload,
    hydrate_target_snapshot,
    hydrate_task_contract_snapshot,
    target_descriptor_from_payload,
)
from zf.runtime.task_pipeline_briefing import verification_result_template
from zf.runtime.workflow_inputs import (
    compact_workflow_input_payload_for_briefing,
    render_workflow_input_briefing_section,
)


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
        # Reader fanout children are workflow-operation owners, not canonical
        # Task assignees. On-demand activation may have rendered role
        # instructions from the root Task before this child was selected;
        # replace that projection with role-only constraints so it cannot
        # inject the Task ownership guard into the child result protocol.
        # Some renderer unit probes intentionally omit a complete runtime.
        if (
            getattr(self, "config", None) is not None
            and getattr(self, "project_root", None) is not None
        ):
            self._write_on_demand_role_instructions(
                role,
                task_id=None,
                skill_entries=list(skill_entries or []),
            )

        trigger_payload = (
            child_payload.get("trigger_payload")
            if isinstance(child_payload.get("trigger_payload"), dict)
            else {}
        )
        task_id = fanout_task_id(child_payload, trigger_payload)
        execution_boundary_lines = render_execution_boundary(
            getattr(self, "config", None),
            role,
            child_payload,
        )
        from zf.runtime.injection import _render_recursion_guard

        recursion_guard_lines = _render_recursion_guard(role).splitlines()
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
        is_artifact_delivery = (
            str(child_payload.get("output_profile_id") or "")
            == "artifact-delivery"
        )
        if is_artifact_delivery:
            from zf.runtime.artifact_delivery_result import (
                artifact_delivery_success_payload,
            )

            success_payload.update(artifact_delivery_success_payload(
                child_payload,
                verifier_stage_id=str(context.stage_id or ""),
                verifier_role=role.instance_id,
                state_dir=self.state_dir,
            ))
        elif is_goal_closure:
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
            from zf.runtime.product_acceptance import (
                product_acceptance_handoff_payload,
            )

            product_identity = product_acceptance_handoff_payload(child_payload)
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
            success_payload.update(product_identity)
            goal_closure_result = {
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
            goal_closure_result.update(product_identity)
            success_payload["goal_closure_result"] = goal_closure_result
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
            reusable_receipts: list[dict] = []
            if str(child_payload.get("impl_self_check_ref") or "").strip():
                target_snapshot = hydrate_target_snapshot(
                    self.state_dir,
                    target_descriptor_from_payload(child_payload),
                    expected={"task_id": str(child_payload.get("task_id") or "")},
                )
                self_check = hydrate_impl_self_check(
                    self.state_dir,
                    self_check_descriptor_from_payload(child_payload),
                    contract_snapshot=contract_snapshot,
                    target_snapshot=target_snapshot,
                    allow_prior_target=True,
                )
                reusable_receipts = reusable_command_receipts(
                    self_check,
                    contract_snapshot=contract_snapshot,
                    target_snapshot=target_snapshot,
                )
                success_payload["impl_self_check_ref"] = str(
                    child_payload.get("impl_self_check_ref") or ""
                )
                success_payload["impl_self_check_digest"] = str(
                    child_payload.get("impl_self_check_digest") or ""
                )
                success_payload["reusable_impl_receipts"] = reusable_receipts
            verification_template = verification_result_template(contract_snapshot)
            verification_template["reused_command_receipt_ids"] = [
                str(item.get("receipt_id") or "")
                for item in reusable_receipts
                if str(item.get("receipt_id") or "")
            ]
            if str(child_payload.get("output_profile_id") or "") == "candidate-verify":
                package_ref = str(
                    child_payload.get("plan_artifact_package_ref") or ""
                )
                package_digest = str(
                    child_payload.get("plan_artifact_package_digest") or ""
                )
                if package_ref and package_digest:
                    from zf.runtime.product_acceptance import (
                        product_acceptance_binding_from_package,
                        product_acceptance_report_template,
                    )

                    product_binding = product_acceptance_binding_from_package(
                        self.state_dir,
                        {"ref": package_ref, "sha256": package_digest},
                    )
                    if product_binding.get("spec"):
                        verification_template["product_acceptance_report"] = (
                            product_acceptance_report_template(
                                spec=dict(product_binding["spec"]),
                                spec_ref=str(product_binding.get("spec_ref") or ""),
                                spec_digest=str(
                                    product_binding.get("spec_digest") or ""
                                ),
                                candidate_ref=str(
                                    child_payload.get("candidate_ref") or ""
                                ),
                                target_commit=str(
                                    child_payload.get("target_commit") or ""
                                ),
                            )
                        )
            success_payload["verification_result"] = verification_template
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
                "rework_items": [
                    {
                        "rework_item_id": "RW-<acceptance-id>-1",
                        "status": "incomplete",
                        "acceptance_id": str(item.get("acceptance_id") or ""),
                        "expected": "The exact acceptance outcome.",
                        "observed": "The exact observed gap on this target.",
                        "required_delta": "The smallest required implementation delta.",
                        "reproduction_command_ids": [],
                        "allowed_scope": [],
                        "done_when": "The acceptance probe passes on the repaired target.",
                        "next_gate": "task_verify",
                        "owner": "implementation_owner",
                    }
                    for item in contract_snapshot.get("acceptance_criteria") or []
                    if isinstance(item, dict)
                ],
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
        from zf.runtime.research_adaptive_contract import apply_child_contract

        apply_child_contract(
            success_payload, child_payload, stage_instruction_lines,
            str(context.stage_id or ""), str(context.fanout_id or ""),
        )
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
        is_refactor_plan = success_event in {"zaofu.refactor.plan.ready", "refactor.plan.ready"}
        is_plan_artifact_stage = self._is_plan_artifact_stage(
            role=role,
            stage_id=str(context.stage_id),
            success_event=success_event,
            child_success_event=child_success_event,
        )
        if is_refactor_plan or is_plan_artifact_stage:
            from zf.runtime.plan_execution_review import planning_execution_context

            stage_instruction_lines.extend(planning_execution_context(
                getattr(self, "config", None), flow_kind=str(child_payload.get("flow_kind")
                                           or trigger_payload.get("flow_kind")
                                           or ("refactor" if is_refactor_plan else "")),
            ))
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
            workdir_refs = refactor_plan_workdir_refs(context.fanout_id)
            success_payload.update({
                **workdir_refs,
                "artifact_digests": {},
                "plan_ports": [],
            })
            if refactor_contract:
                success_payload["refactor_contract"] = dict(refactor_contract)
            success_payload["report"].update({
                "review_artifact_ref": review_artifact_ref,
                "plan_intent": plan_intent,
                "refactor_contract": refactor_contract,
                **workdir_refs,
                "refactor_plan_md": "## Refactor Plan\n\nReplace with the final plan.",
                "task_map": {"tasks": []},
                "gates": [],
                "risk_register": [],
                "backlog_candidates": [],
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
            controlled_inputs = render_attempt_source_briefing(
                child_payload,
                state_dir=self.state_dir,
            ).strip()
            if controlled_inputs:
                payload_section.extend([*controlled_inputs.splitlines(), ""])
            instruction = str(
                child_payload.get("instruction")
                or child_payload.get("summary")
                or ""
            ).strip()
            from zf.runtime.stage_execution_card import (
                compact_fanout_stage_context,
            )

            payload_section.extend([
                "## Child-Specific Context",
                "",
                "This compact card carries dispatch identity and scope. Read canonical "
                "semantic bodies through Controlled Artifact Inputs.",
                "```json",
                json.dumps(
                    compact_fanout_stage_context(child_payload),
                    ensure_ascii=False,
                    indent=2,
                ),
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
                self.event_log.read_all(),
                config=self.config,
                role=role.instance_id,
                stage=str(context.stage_id or ""),
                output_profile=str(child_payload.get("output_profile_id") or ""),
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
            compact_workflow_input_payload_for_briefing(child_payload),
        ).strip()
        workflow_input_lines = (
            [*workflow_input_section.splitlines(), ""]
            if workflow_input_section
            else []
        )
        from zf.runtime.stage_execution_card import (
            ARTIFACT_DELIVERY_RESULT_GUIDANCE,
            prepare_fanout_result_guidance,
            render_review_subject_lines,
        )

        result_guidance, semantic_submit, result_prefix = (
            prepare_fanout_result_guidance(
                child_payload=child_payload,
                has_contract_snapshot=bool(contract_snapshot),
            )
        )
        if is_artifact_delivery:
            result_guidance.extend(ARTIFACT_DELIVERY_RESULT_GUIDANCE)
        if (
            success_event == "flow.discovery.completed"
            and failure_event == "flow.discovery.failed"
        ):
            result_guidance.extend([
                "A blocking product gap is a completed semantic discovery: emit "
                f"the failure result with bounded `{result_prefix}gap_tasks`; the "
                "kernel will amend the task map instead of rescanning the unchanged "
                "candidate.",
                "Every gap task MUST use the canonical task-map shape: non-empty `task_id`, `owner_role`, `claim_paths` or `allowed_paths`, `acceptance` or `acceptance_criteria`, `verify_commands` or `verification`, and `source_refs`.",
                "Use `task_id`, not a bare `id`; `acceptance_refs` and `verification_commands` do not replace the canonical acceptance and verification fields.",
                "Gap tasks MUST NOT claim overlapping paths. Combine related fixes into one task or give each task disjoint file ownership; ordering does not make duplicate path ownership valid.",
                "When several gap tasks replace one old task, declare an explicit dependency chain. Exactly one terminal gap task supersedes the old task; predecessor tasks do not. The kernel inherits the old task's incoming dependencies onto the chain root and rewires downstream dependents to that terminal successor.",
                "The terminal successor MUST declare `supersedes_task_ids`, the immutable `base_commit`, and a matching `git:<base_commit>` source ref. Every predecessor relation MUST be explicit in `blocked_by`; list order is not dependency evidence.",
                "Keep suggestions out of `gap_tasks`; only implementation work required to close a blocking goal claim belongs there.",
            ])
            candidate_policy = (
                child_payload.get("candidate_policy")
                or trigger_payload.get("candidate_policy")
            )
            candidate_policy = (
                candidate_policy
                if isinstance(candidate_policy, dict)
                else {}
            )
            if str(candidate_policy.get("rolling_smoke") or "") == "required":
                result_guidance.extend([
                    "The active candidate policy requires rolling smoke. Every gap task MUST include `validation.commands` with at least one cheap behavior check marked with JSON boolean `rolling_smoke: true`, `deterministic: true`, `reusable: true`, and tier `static` or `runtime`.",
                    "Do not rely on bare `verify_commands` for this policy and do not mark a source-delta-only assertion as the behavior smoke.",
                ])
        if is_refactor_review:
            result_guidance.extend([
                "For this refactor review workflow, finding severity describes planning risk.",
                "Emit the success event when the review report is complete, even if findings include `high` or `critical` items.",
                f"For a complete review report, keep `{result_prefix}status` as "
                f"`passed` and `{result_prefix}recommendation` as `approve`; put "
                "caveats in findings, risks, refactor_slices, and summary.",
                "Do not invent custom recommendation values; valid values are `approve`, `reject`, `needs_rework`, and `abstain`.",
                "Emit the failure event only when you cannot inspect the assigned scope or cannot provide `coverage_matrix` / `evidence_refs`.",
                "Replace placeholder arrays in the success result with actual "
                "coverage, evidence, uncovered areas, findings, and refactor slices.",
            ])
        elif is_refactor_plan:
            result_guidance.extend([
                "For this refactor plan workflow, emit the success event only when `refactor_plan_md`, `task_map`, and `gates` are complete.",
                f"For a complete plan artifact, keep `{result_prefix}status` as "
                f"`passed` and `{result_prefix}recommendation` as `approve`.",
                "Do not invent custom recommendation values; valid values are `approve`, `reject`, `needs_rework`, and `abstain`.",
                "Use the provided `review_artifact_ref` and `plan_intent`; do not invent facts for uncovered review areas.",
                "Emit the failure event only when the plan artifact cannot be produced.",
                *self._plan_artifact_contract_lines(),
                *self._plan_port_contract_lines(flow_kind="refactor"),
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
            flow_kind = str(
                child_payload.get("flow_kind")
                or trigger_payload.get("flow_kind")
                or ""
            )
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
                "plan_ports": [],
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
                "plan_ports": [],
            })
            result_guidance.extend(self._plan_artifact_contract_lines())
            result_guidance.extend(
                self._plan_port_contract_lines(flow_kind=flow_kind)
            )
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
        failure_payload_file = ""
        if not semantic_submit:
            from zf.runtime.fanout_result_draft import prepare_fanout_result_draft

            success_payload_file = prepare_fanout_result_draft(
                self.state_dir, run_id=run_id, label="success", payload=success_payload,
            )
            failure_payload_file = prepare_fanout_result_draft(
                self.state_dir, run_id=run_id, label="failure", payload=failure_payload,
            )
        success_command = _emit_command(
            child_success_event,
            success_payload,
            payload_file=success_payload_file,
        )
        failure_command = _emit_command(
            child_failure_event,
            failure_payload,
            payload_file=failure_payload_file,
        )
        output_contract_lines: list[str] = []
        if semantic_submit:
            from zf.runtime.stage_execution_card import prepare_profiled_stage_result

            success_command, output_contract_lines = prepare_profiled_stage_result(
                state_dir=self.state_dir,
                child_payload=child_payload,
                success_payload=success_payload,
                run_id=run_id,
                cli_command=zf_cli_cmd(),
            )
            failure_command = success_command
        from zf.runtime.stage_execution_card import render_fanout_submit_commands

        command_lines = render_fanout_submit_commands(
            success_command=success_command,
            failure_command=failure_command,
            semantic_submit=semantic_submit,
        )
        result_preview_lines: list[str] = []
        if not semantic_submit:
            # Non-semantic readers still benefit from the schema example.  The
            # actual command submits the editable draft; semantic submitters
            # use their scratch contract and omit this potentially large block.
            result_preview_lines = [
                "Edit the selected payload draft in place using native Edit or "
                "apply_patch Update File. Do not delete, move, or recreate it. "
                "Preserve the prefilled identity fields and replace report placeholders "
                "with your findings before submission. The draft is not runtime truth.",
                "This JSON is only a shape preview. Never copy it into an inline "
                "shell `--payload` argument.",
                "```json",
                json.dumps(success_payload, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        review_subject_lines = render_review_subject_lines(
            candidate_ref=candidate_eval_ref,
            candidate_head=candidate_eval_head,
            candidate_prefix=candidate_prefix,
            subject_pdd_id=subject_pdd_id,
            verification_reader=verification_reader,
            artifact_delivery=is_artifact_delivery,
            handoff_kind=str(
                trigger_payload.get("handoff_kind")
                or child_payload.get("handoff_kind")
                or ""
            ),
            target_ref=str(context.target_ref or ""),
            target_commit=str(child_payload.get("target_commit") or ""),
        )
        briefing_header = (
            [f"Active task: {task_id}", "", f"# Fanout Reader Child: {child_id}"]
            if task_id
            else [f"# Fanout Reader Child: {child_id}"]
        )
        briefing_text = "\n".join([
                *briefing_header,
                "",
                f"- fanout_id: `{context.fanout_id}`",
                f"- stage_id: `{context.stage_id}`",
                f"- run_id: `{run_id}`",
                f"- target_ref: `{context.target_ref}`",
                "",
                *execution_boundary_lines,
                *recursion_guard_lines,
                "",
                *review_subject_lines,
                (
                        "This is a planning reader: do not modify product "
                        "implementation files, but write the plan/task-map artifacts "
                        "and inline plan_ports required by this briefing."
                        if is_plan_artifact_stage
                        else (
                            "Evaluate the target ref as a read-only fanout child."
                        if (not candidate_eval_ref or is_artifact_delivery)
                        and not verification_reader
                        else "Read-only fanout child."
                    )
                ),
                *(
                    []
                    if is_plan_artifact_stage
                    else ["Do not modify project source files."]
                ),
                "",
                *stage_instruction_lines,
                # Scope task review by affinity, while preserving a bound
                # candidate Product Acceptance obligation.
                *affinity_scope_briefing_lines(
                    child_payload,
                    product_acceptance_required=bool(
                        (success_payload.get("verification_result") or {}).get(
                            "product_acceptance_report"
                        )
                    ),
                ),
                *skill_briefing.render_skill_briefing_section(role, skill_entries),
                *workflow_input_lines,
                *payload_section,
                *output_contract_lines,
                "## Child Result Authority",
                "",
                "`Active task` is lineage only; this reader does not own the "
                "canonical Task. Do not run `zf guard ownership` for that Task. "
                "Submit one result below; fanout, operation, attempt, and lease "
                "identity is authoritative.",
                "",
                "Use `--state-dir`; this role may run from a detached workdir.",
                "",
                *result_preview_lines,
                *command_lines,
                "Do not emit the aggregate success/failure event directly; the kernel publishes it after the fanout barrier or synth role finishes.",
                "",
                *result_guidance,
                "",
                "Emit once: command exit 0 is success; there is no asynchronous ack. "
                "Never retry or re-send (stale generations are discarded). Then stop.",
                "",
                f"Child success event: `{child_success_event}`",
                f"Child failure event: `{child_failure_event}`",
                f"Aggregate success event: `{success_event}`",
                f"Aggregate failure event: `{failure_event}`",
                "",
            ])
        from zf.runtime.briefing_metrics import write_briefing_with_metrics

        write_briefing_with_metrics(
            path,
            briefing_text,
            state_dir=self.state_dir,
            stage=str(context.stage_id or ""),
            role=role.instance_id,
            payload=child_payload,
            indexed_skills=list(role.skills),
            auto_injected_skills=skill_briefing.auto_injected_skill_entries(skill_entries),
        )
        return path
