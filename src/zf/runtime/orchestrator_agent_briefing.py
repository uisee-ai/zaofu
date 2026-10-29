"""Operation-scoped Product Flow briefing for the Orchestrator Agent."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from zf.runtime.orchestrator_agent_operations import (
    PreparedOrchestratorAgentOperation,
)
from zf.runtime.stage_execution_card import prepare_result_file_command


def build_orchestrator_agent_operation_briefing(
    *,
    state_dir: Path,
    prepared: PreparedOrchestratorAgentOperation,
    cli_command: str = "zf",
) -> str:
    context = prepared.context
    identity = context.input_body["identity"]
    decision_identity = {
        "operation_id": prepared.operation_id,
        "workflow_run_id": prepared.workflow_run_id,
        "checkpoint": prepared.checkpoint,
        "input_digest": str(context.input_ref["sha256"]),
        "effective_config_digest": str(
            context.effective_config_ref["sha256"]
        ),
    }
    for key in (
        "plan_artifact_package_id",
        "plan_artifact_package_ref",
        "plan_artifact_package_digest",
        "task_map_generation",
    ):
        if identity.get(key):
            decision_identity[key] = identity[key]
    if prepared.checkpoint == "owner_delivery":
        delivery = context.input_body.get("checkpoint_context") or {}
        template = {
            "schema_version": "owner-delivery-narrative.v1",
            "execution_status": "completed",
            "identity": {
                "operation_id": prepared.operation_id,
                "workflow_run_id": prepared.workflow_run_id,
                "terminal_event_id": delivery.get("terminal_event_id", ""),
                "terminal_event_type": delivery.get("terminal_event_type", ""),
                "dossier_ref": delivery.get("dossier_ref", ""),
                "dossier_source_fingerprint": delivery.get(
                    "dossier_source_fingerprint",
                    "",
                ),
                "completion_receipt_ref": delivery.get(
                    "completion_receipt_ref",
                    "",
                ),
                "completion_receipt_fingerprint": delivery.get(
                    "completion_receipt_fingerprint",
                    "",
                ),
            },
            "status": (
                "completed"
                if delivery.get("terminal_event_type") == "run.goal.completed"
                else "blocked"
            ),
            "executive_summary": "replace with a cited owner-readable summary",
            "delivered_outcomes": [],
            "decisions_and_tradeoffs": [],
            "remaining_risks": [],
            "recommended_next_actions": [],
        }
        output_schema = "owner-delivery-narrative.v1"
        output_rules = [
            "- cite only claim/task/result/evidence/gap identities present in the Dossier",
            "- completed delivery requires at least one cited delivered outcome",
            "- do not change terminal status or invent new runtime facts",
        ]
    else:
        template = {
            "schema_version": "orchestration-decision.v1",
            "execution_status": "completed",
            "identity": decision_identity,
            "decision": {
                "run_start": "adopt",
                "pre_impl": "adopt",
                "plan_candidate": "adopt",
                "stage_barrier": "continue",
                "semantic_failure": "continue",
                "goal_revision": "halt",
                "pre_closeout": "aggregate",
            }[prepared.checkpoint],
            "reason_codes": ["replace_with_evidence_grounded_reason"],
            "summary": "replace with a concise owner-readable explanation",
            "affected_work_units": [],
            "required_followup": "continue",
            "expected_outcome": "continue admitted graph",
            "confidence": 0.0,
        }
        if prepared.checkpoint in {"run_start", "pre_impl"}:
            template["run_plan"] = {
                "schema_version": "run-orchestration-plan.v1",
                "identity": {
                    "operation_id": prepared.operation_id,
                    "workflow_run_id": prepared.workflow_run_id,
                    "goal_id": identity.get("goal_id", ""),
                    "plan_revision": 1,
                    "effective_config_digest": identity.get(
                        "effective_config_digest", ""
                    ),
                    "run_contract_ref": identity.get("run_contract_ref", ""),
                    "run_contract_digest": identity.get(
                        "run_contract_digest", ""
                    ),
                },
                "goal_model": {
                    "objective": "replace with the admitted run objective",
                    "mandatory_claims": ["replace with a mandatory claim"],
                    "constraints": [],
                    "assumptions": [],
                    "exclusions": [],
                },
                "graph": {
                    "work_units": [{"work_unit_id": "replace-with-task-or-stage"}],
                    "edges": [],
                    "barriers": [],
                    "semantic_checkpoints": [],
                },
                "delegation": [{
                    "work_unit_id": "replace-with-task-or-stage",
                    "capability_refs": [],
                    "preferred_role_refs": ["replace-with-configured-role"],
                    "skill_refs": [],
                    "dispatch_rank": 0,
                }],
                "context_routes": [{
                    "work_unit_id": "replace-with-task-or-stage",
                    "required_sources": [],
                    "return_policy": "selective",
                }],
                "quality": {},
                "control": {},
            }
        elif prepared.checkpoint in {"stage_barrier", "pre_closeout"}:
            result_sources = [
                {
                    "ref": str(source.get("ref") or ""),
                    "sha256": str(source.get("sha256") or ""),
                }
                for source in context.input_body.get("aggregation_input_refs", [])
                if isinstance(source, Mapping)
                and str(source.get("ref") or "")
                and str(source.get("sha256") or "")
            ]
            template["aggregation_result"] = {
                "schema_version": "orchestration-result.v1",
                "identity": {
                    "operation_id": prepared.operation_id,
                    "workflow_run_id": prepared.workflow_run_id,
                    "checkpoint": prepared.checkpoint,
                },
                "input_result_refs": result_sources,
                "selected_result_refs": result_sources,
                "rejected_result_refs": [],
                "unclosed_claim_ids": [],
                "provenance_map": [],
                "remaining_uncertainty": [],
                "recommendation": template["decision"],
            }
        output_schema = "orchestration-decision.v1"
        output_rules = [
            "- actions are checkpoint-scoped and mechanically admitted",
            "- any mutation/rework/replan action requires a typed `delta`",
        ]
        if prepared.checkpoint == "plan_candidate":
            output_rules.extend([
                "- treat Task Map `owner_role` values as logical capability or "
                "affinity labels, not configured runtime role-instance names",
                "- declare a work unit unroutable only when canonical routing or "
                "admission evidence shows that no eligible writer lane exists; "
                "a logical-owner/physical-lane name mismatch is not evidence",
            ])
        if prepared.checkpoint in {"run_start", "pre_impl"}:
            output_rules.append(
                "- adopt requires a `run-orchestration-plan.v1` bound to the "
                "prefilled Run/Goal/config/Run Contract identity. For v4 delegation, "
                "use work_units.task_ids and stage_ids (impl, verify, acceptance_review); "
                "preferred_role_refs are ordered soft preferences within the stage pool. "
                "WRC enforces capabilities/dependencies and may use a compatible idle fallback. "
                "Graph/dependency changes require Planner revision and normal approval, "
                "not new edges in this advisory execution plan."
            )
        elif prepared.checkpoint in {"stage_barrier", "pre_closeout"}:
            output_rules.append(
                "- include `orchestration-result.v1`; every input result ref "
                "must come from this operation's canonical source manifest"
            )
    command, scratch = prepare_result_file_command(
        state_dir=state_dir,
        result_scratch_ref=prepared.result_scratch_ref,
        operation_id=prepared.operation_id,
        cli_command=cli_command,
        semantic_template=template,
    )
    source_lines: list[str] = []
    optional_source_lines: list[str] = []
    read_commands: list[str] = []
    state_root = Path(state_dir).expanduser().resolve()
    required = {
        (str(item.get("source_id") or ""), str(item.get("artifact_id") or ""))
        for item in context.read_policy.get("required_reads", [])
        if isinstance(item, Mapping)
    }
    for source in context.source_manifest.get("sources", []):
        source_id = str(source.get("source_id") or "")
        artifact_id = str(source.get("artifact_id") or "")
        line = (
            f"- `{source_id}` / `{artifact_id}` -> `{source.get('ref')}` "
            f"(`{source.get('sha256')}`)"
        )
        if (source_id, artifact_id) not in required:
            optional_source_lines.append(line)
            continue
        source_lines.append(line)
        read_commands.append(
            "zf artifact read "
            f"--attempt {prepared.attempt_id} "
            f"--source {source_id} "
            f"--artifact {artifact_id} "
            f"--json-path '$' --state-dir {state_root}"
        )
    return "\n".join([
        "# Orchestrator Agent Semantic Checkpoint",
        "",
        "## Execution Identity",
        "",
        f"- workflow_run_id: `{prepared.workflow_run_id}`",
        f"- operation_id: `{prepared.operation_id}`",
        f"- attempt_id: `{prepared.attempt_id}`",
        f"- checkpoint: `{prepared.checkpoint}`",
        f"- policy: `{prepared.checkpoint_policy}`",
        f"- checkpoint_input_digest: `{context.input_ref['sha256']}`",
        "",
        "## Objective",
        "",
        str(context.input_body["objective"]),
        "",
        "Submit semantic intent only. Do not write Task/Feature/Session state, "
        "dispatch a provider, or declare terminal completion.",
        "",
        "## Required Canonical Inputs",
        "",
        *source_lines,
        "",
        "Read every required source through the controlled interface before submitting:",
        "",
        "```bash",
        *read_commands,
        "```",
        "",
        "## Optional Canonical Inputs",
        "",
        *optional_source_lines,
        "",
        "Optional sources remain available through the same controlled interface "
        "when the checkpoint pack exposes a risk or evidence gap.",
        "",
        "## Output Contract",
        "",
        f"- schema: `{output_schema}`",
        *output_rules,
        f"- edit `{scratch}`; preserve all prefilled identity fields",
        "- update that prefilled file in place; with apply_patch use `*** Update File`; "
        "never delete, add, move, or recreate the scratch path",
        "- do not wrap the JSON under another key",
        "",
        "Submit once:",
        "",
        "```bash",
        command,
        "```",
        "",
    ])


__all__ = ["build_orchestrator_agent_operation_briefing"]
