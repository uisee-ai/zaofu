"""Freeze the approved candidate roster for a fresh milestone reviewer."""

from __future__ import annotations

from zf.core.workflow.flow_metadata import flow_metadata_for
from pathlib import Path
from typing import Any

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.milestone_task_lifecycles import SOURCE as LIFECYCLES, task_lifecycle_evidence
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import (
    load_workflow_operation,
    reduce_workflow_operations,
)

SOURCE = "milestone-handoff"
BINDINGS = (
    "workflow_run_id",
    "task_id",
    "task_map_generation",
    "target_commit",
    "target_snapshot_digest",
    "contract_snapshot_digest",
)


def _snapshot(state: Path, consumer: dict, kind: str) -> dict:
    return hydrate_sidecar_ref(
        state,
        {
            "ref": consumer[kind + "_snapshot_ref"],
            "sha256": consumer[kind + "_snapshot_digest"],
        },
    ).payload


def attach_milestone_handoff(
    runtime: Any, payload: dict, operation_id: str, attempt_id: str
) -> None:
    """Restore frozen bytes for old calls; only a newly authorized call can gain a view."""
    payload.pop("milestone_handoff_ref", None)
    inputs = [
        row
        for row in payload.get("input_refs", [])
        if not isinstance(row, dict) or row.get("source_id") not in {SOURCE, LIFECYCLES}
    ]
    payload["input_refs"] = inputs
    state = Path(runtime.state_dir)
    existing = load_workflow_operation(runtime.event_log, operation_id)
    if existing:
        request = hydrate_sidecar_ref(state, existing["request_ref"]).payload["request"]
        ref = request.get("milestone_handoff_ref")
        if not ref:
            return
        body = hydrate_sidecar_ref(state, ref).payload
        if body["consumer"] != {
            "operation_id": operation_id,
            "attempt_id": attempt_id,
            "actor": payload["role_instance"],
        }:
            raise ValueError("milestone handoff consumer changed")
    else:
        metadata = flow_metadata_for(runtime.config, payload=payload)
        if metadata.get("milestone_handoff") is not True or not all(
            payload.get(k) for k in BINDINGS
        ):
            return
        contract = _snapshot(state, payload, "contract")
        target = _snapshot(state, payload, "target")
        members = contract.get("source_refs", {}).get("child_authorities", [])
        roster = target.get("completed_task_ids", [])
        if (
            not roster
            or len(set(roster)) != len(roster)
            or sorted(roster) != sorted(m["task_id"] for m in members)
            or target["target_commit"] != payload["target_commit"]
        ):
            raise ValueError("milestone membership differs from frozen candidate")
        consumer = {
            "operation_id": operation_id,
            "attempt_id": attempt_id,
            "actor": payload["role_instance"],
        }
        body = {
            "schema_version": "milestone-handoff.v1",
            "consumer": consumer,
            "binding": {k: payload[k] for k in BINDINGS},
            "candidate_event_id": target["candidate_event_id"],
            "base_commit": contract["base_commit"],
            "objective": contract["behavior"],
            "allowed_paths": contract["allowed_paths"],
            "contract_ref": payload["contract_snapshot_ref"],
            "target_ref": payload["target_snapshot_ref"],
            "members": members,
            "remaining_acceptance": contract["acceptance_criteria"],
            "next_owner": payload["role_instance"],
            "authorizes_execution": False,
        }
        events = runtime.event_log.read_all()
        lifecycles = task_lifecycle_evidence(events, run=payload["workflow_run_id"],
            generation=payload["task_map_generation"], target=target["target_commit"],
            freeze_id=body["candidate_event_id"], members=members)
        if lifecycles:
            body["task_pipeline_lifecycles_ref"] = write_immutable_json_sidecar(state, lifecycles,
                root="milestone-task-lifecycles", kind=LIFECYCLES, schema_version=lifecycles["schema_version"],
                created_by="kernel", source_event_id=body["candidate_event_id"])
        verification_events = {e.id: e for e in events}
        from zf.runtime.task_handoff import _unconfirmed_continuations

        body["member_checkpoints"] = []
        for member in members:
            authority = verification_events[member["verification_event_id"]]
            attempt = authority.payload.get("attempt_id")
            record = next(
                (
                    e
                    for e in reversed(events)
                    if attempt
                    and e.origin == "kernel"
                    and e.type == "worker.work_record.published"
                    and e.task_id == member["task_id"]
                    and e.payload.get("attempt_id") == attempt
                    and e.payload.get("operation_id")
                    == authority.payload.get("operation_id")
                ),
                None,
            )
            body["member_checkpoints"].append(
                {
                    "task_id": member["task_id"],
                    "operation_id": authority.payload.get("operation_id"),
                    "attempt_id": attempt,
                    "verification_event": authority.id,
                    "actor": authority.payload.get("role_instance"),
                    "draft_event": record.id if record else None,
                    "draft_status": "published" if record else "unknown",
                    "workspace_generation": authority.payload.get(
                        "workspace_generation"
                    ),
                    "unknown_effects": _unconfirmed_continuations(
                        events, member["task_id"], attempt
                    )
                    if attempt
                    else [],
                }
            )
        policy = metadata.get("milestone_readiness", {})
        if not isinstance(policy, dict) or not isinstance(
            policy.get("required_gates", []), list
        ):
            raise ValueError("invalid milestone readiness policy")
        gates = policy.get("required_gates", [])
        if any(not isinstance(g, str) or not g.strip() for g in gates):
            raise ValueError("mandatory milestone gates need nonempty IDs")
        from zf.runtime.milestone_gates import freeze_gate_commands

        body["gate_command_ids"] = freeze_gate_commands(runtime.config, contract, gates)
        body["required_gates"] = sorted(set(gates))
        body["empty_waiver_gate"] = str(policy.get("empty_waiver_gate") or "")
        host_ref = payload.get("candidate_host_evidence_ref")
        if host_ref:
            host = hydrate_sidecar_ref(state, host_ref).payload
            if (
                any(host["consumer"].get(k) != payload[k] for k in BINDINGS)
                or host["consumer"].get("operation_id") != operation_id
            ):
                raise ValueError("milestone host binding differs")
            body["host_evidence_ref"] = host_ref
            body["execution_facts"] = [
                {
                    k: row[k]
                    for k in (
                        "command_id",
                        "receipt_id",
                        "source_operation_id",
                        "status",
                        "reason",
                    )
                    if k in row
                }
                for row in host["receipts"]
            ]
        ref = write_immutable_json_sidecar(
            state,
            body,
            root="milestone-handoffs",
            kind="milestone_handoff",
            schema_version="milestone-handoff.v1",
            created_by="kernel",
            source_event_id=body["candidate_event_id"],
        )
    if lifecycle_ref := body.get("task_pipeline_lifecycles_ref"):
        inputs.append({**lifecycle_ref, "source_id": LIFECYCLES, "artifact_id": LIFECYCLES,
            "allowed_paths": ["$.summary", *(f"$.tasks[{i}]" for i in range(len(body["members"])))],
            "access_scope": {"actors": [payload["role_instance"]], "purposes": ["candidate-verify"]}})
    payload["milestone_handoff_ref"] = ref
    inputs.append(
        {
            **ref,
            "source_id": SOURCE,
            "artifact_id": SOURCE,
            "allowed_paths": ["$"],
            "access_scope": {
                "actors": [payload["role_instance"]],
                "purposes": ["candidate-verify"],
            },
        }
    )


def milestone_readiness(state: Path, events: list, handoff: dict) -> dict:
    """Read-only status of the exact review, using admitted evidence only."""
    consumer, binding = handoff["consumer"], handoff["binding"]
    operation = reduce_workflow_operations(events).get(consumer["operation_id"], {})
    adjudication = {}
    verdict = "pending"
    admitted_id = ""
    probes = {}
    findings = []
    for event in reversed(events):
        p = event.payload
        if (
            event.origin != "kernel"
            or event.actor != "zf-cli"
            or event.type != "workflow.call.result.admitted"
            or p.get("admission_status") != "admitted"
            or not p.get("request_hash")
            or p.get("request_hash") != operation.get("request_hash")
            or p.get("operation_id") != consumer["operation_id"]
            or p.get("attempt_id") != consumer["attempt_id"]
            or event.task_id != binding["task_id"]
            or operation.get("role_instance") != consumer["actor"]
        ):
            continue
        result = hydrate_sidecar_ref(state, p["control_result_ref"]).payload
        if (
            any(result.get(k) != v for k, v in binding.items())
            or result.get("verification_owner") != "candidate_verify"
            or result.get("role_instance") != consumer["actor"]
            or result.get("request_hash") != operation["request_hash"]
            or any(result.get(k) != consumer[k] for k in ("attempt_id", "operation_id"))
        ):
            continue
        verdict = result["verdict"]
        probes = {
            r.get("command_id"): r
            for r in result.get("probe_receipts", [])
            if r.get("command_id")
        }
        findings = list(result.get("findings") or []) + [
            finding
            for r in result.get("requirement_results", [])
            for finding in r.get("findings", [])
        ]
        adjudication = {
            row["acceptance_id"]: row for row in result.get("requirement_results", [])
        }
        admitted_id = event.id
        break
    obligations = []
    for row in handoff["remaining_acceptance"]:
        if not row.get("mandatory", True):
            continue
        evidence = adjudication.get(row["acceptance_id"], {})
        obligations.append(
            {
                "acceptance_id": row["acceptance_id"],
                "owner": handoff["next_owner"],
                "status": evidence.get("status", "pending"),
                "evidence_refs": evidence.get("evidence_refs", []),
                "expected_commands": row.get("verification_command_ids", []),
            }
        )
    unresolved = [
        r for r in handoff.get("execution_facts", []) if r["status"] != "available"
    ]
    unknown = [
        e for m in handoff.get("member_checkpoints", []) for e in m["unknown_effects"]
    ]
    gates = {}
    waiver = handoff.get("empty_waiver_gate", "")
    for gate in [
        *handoff.get("required_gates", []),
        *([waiver] if waiver and not obligations else []),
    ]:
        last = next(
            (
                e
                for e in reversed(events)
                if e.origin == "kernel"
                and e.type
                in {
                    "gate.passed",
                    "gate.failed",
                    "static_gate.passed",
                    "static_gate.failed",
                    "static_gate.skipped",
                }
                and (e.payload.get("gate_id") == gate
                     or (e.type.startswith("static_gate.") and gate in e.payload.get("static_gate_ids", [])))
                and all(
                    e.payload.get(k) == binding[k]
                    for k in (
                        "workflow_run_id",
                        "contract_snapshot_digest",
                        "task_map_generation",
                        "target_commit",
                    )
                )
            ),
            None,
        )
        gates[gate] = {
            "status": "passed"
            if last
            and last.type in {"gate.passed", "static_gate.passed"}
            and last.payload.get("evidence_refs")
            else "pending"
            if not last
            else "blocked",
            "event_id": last.id if last else None,
        }
        checks = handoff.get("gate_command_ids", {}).get(gate, [])
        if not last and admitted_id and checks and all(
            probes.get(cmd, {}).get("status") == "passed"
            and probes[cmd].get("target_commit") == binding["target_commit"]
            and probes[cmd].get("evidence_refs") for cmd in checks
        ):
            gates[gate] = {"status": "passed", "event_id": admitted_id, "command_ids": checks}
    latest = next(
        (
            e
            for e in reversed(events)
            if e.type == "candidate.ready"
            and e.origin == "kernel"
            and e.payload.get("workflow_run_id") == binding["workflow_run_id"]
            and e.payload.get("target_commit")
            and e.payload.get("freeze_receipt_ref")
        ),
        None,
    )
    current_target = bool(
        latest
        and latest.payload["target_commit"] == binding["target_commit"]
        and latest.payload.get("task_map_generation") == binding["task_map_generation"]
    )
    available = {
        r["command_id"]
        for r in handoff.get("execution_facts", [])
        if r["status"] == "available"
    }
    commands = {cmd for o in obligations for cmd in o["expected_commands"]}
    unmet_commands = sorted(
        cmd
        for cmd in commands
        if cmd not in available
        and not (
            probes.get(cmd, {}).get("status") == "passed"
            and probes[cmd].get("target_commit") == binding["target_commit"]
            and probes[cmd].get("evidence_refs")
        )
    )
    ready = (
        current_target
        and (bool(obligations) or bool(waiver and gates[waiver]["status"] == "passed"))
        and verdict == "passed"
        and not unresolved
        and not unmet_commands
        and not unknown
        and not findings
        and all(g["status"] == "passed" for g in gates.values())
        and all(o["status"] == "passed" and o["evidence_refs"] for o in obligations)
    )
    lifecycle_view = {}
    if lifecycle_ref := handoff.get("task_pipeline_lifecycles_ref"):
        summary = hydrate_sidecar_ref(state, lifecycle_ref).payload["summary"]
        lifecycle_view["task_pipeline_lifecycles"] = {"source_id": LIFECYCLES, "artifact_id": LIFECYCLES,
                                                   "sha256": lifecycle_ref["sha256"], "summary": summary}
    return {
        **lifecycle_view,
        "ready": ready,
        "verdict": verdict,
        "admitted_event_id": admitted_id,
        "current_target": current_target,
        "obligations": obligations,
        "unresolved_execution_facts": unresolved,
        "unconfirmed_effect_ids": unknown,
        "mandatory_gates": gates,
        "missing_command_proofs": unmet_commands,
        "findings": findings,
        "next_owner": handoff["next_owner"],
        "authorizes_execution": False,
    }
