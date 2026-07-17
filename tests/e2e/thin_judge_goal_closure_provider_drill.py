#!/usr/bin/env python3
"""Run one real provider through the Thin Judge Goal-closure authority path."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from zf.core.config.schema import (
    FanoutAggregateConfig,
    GitIsolationConfig,
    GoalConfig,
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_envelope import (
    normalize_call_result_envelope,
    write_immutable_json_sidecar,
)
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.goal_completion_gate import maybe_complete_run_goal
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


FLOW_CLOSURE_EVENTS = {
    "issue": "flow.goal.closed",
    "prd": "flow.goal.closed",
    "refactor": "module.parity.closed",
}


class RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str, object]] = []

    def send_task(
        self,
        role_name: str,
        briefing_path: Path,
        prompt: str,
        *,
        context: object = None,
    ) -> None:
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name: str) -> bool:
        return True

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""

    def poll_events(self) -> list[ZfEvent]:
        return []


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True, choices=("claude-code", "codex"))
    parser.add_argument("--flow-kind", required=True, choices=tuple(FLOW_CLOSURE_EVENTS))
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--confirm-real", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    return parser


def _run(command: list[str], *, cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-4000:]}"
        )
    return result


def _init_project(root: Path, goal_id: str) -> tuple[str, str]:
    root.mkdir(parents=True)
    _run(["git", "init", "-b", "main"], cwd=root)
    _run(["git", "config", "user.name", "ZaoFu E2E"], cwd=root)
    _run(["git", "config", "user.email", "zaofu-e2e@example.invalid"], cwd=root)
    (root / "README.md").write_text("# Thin Judge provider drill\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=root)
    _run(["git", "commit", "-m", "chore: initialize provider drill"], cwd=root)
    candidate_ref = f"candidate/{goal_id}"
    _run(["git", "checkout", "-b", candidate_ref], cwd=root)
    (root / "delivery.txt").write_text(
        f"delivered by {goal_id}\n",
        encoding="utf-8",
    )
    _run(["git", "add", "delivery.txt"], cwd=root)
    _run(["git", "commit", "-m", "feat: add drill delivery"], cwd=root)
    target_commit = _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    _run(["git", "checkout", "main"], cwd=root)
    return candidate_ref, target_commit


def _config(
    *,
    state_dir: Path,
    project_root: Path,
    backend: str,
    flow_kind: str,
    objective_ref: str,
) -> ZfConfig:
    role = f"judge-{flow_kind}"
    closure_event = FLOW_CLOSURE_EVENTS[flow_kind]
    return ZfConfig(
        project=ProjectConfig(
            name=f"thin-judge-{flow_kind}-{backend}",
            workspace=str(project_root),
            state_dir=str(state_dir),
        ),
        roles=[RoleConfig(
            name=role,
            instance_id=role,
            backend=backend,
            role_kind="reader",
            permission_mode="bypass",
            skills=["zf-goal-closure-judge-contract"],
        )],
        workflow=WorkflowConfig(
            stages=[WorkflowStageConfig(
                id=f"{flow_kind}-thin-judge",
                trigger=closure_event,
                topology="fanout_reader",
                roles=[role],
                target_ref="${candidate_ref}",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    child_success_event="judge.child.completed",
                    child_failure_event="judge.child.failed",
                    success_event="goal.closure.synthesized",
                    failure_event="goal.closure.synthesis.failed",
                ),
            )],
            flow_metadata={
                "flow_kind": flow_kind,
                "post_verify_discovery": (
                    "module_parity"
                    if flow_kind == "refactor"
                    else "regression_impact"
                    if flow_kind == "issue"
                    else "product_completeness"
                ),
                "result_protocol_mode": "blocking",
                "delivery_policy": "ship_candidate",
                "objective_ref": objective_ref,
            },
        ),
        runtime=RuntimeConfig(git=GitIsolationConfig(
            candidate_branch_prefix="candidate",
            candidate_base_ref="main",
            ship_target_branch="main",
            ship_candidate_strategy="merge",
        )),
        goal=GoalConfig(enabled=True),
    )


def _goal_schema(expected: dict[str, Any]) -> dict[str, Any]:
    def constant(value: Any) -> dict[str, Any]:
        if isinstance(value, bool):
            value_type = "boolean"
        elif isinstance(value, int):
            value_type = "integer"
        elif isinstance(value, list):
            item_schema = constant(value[0]) if value else {"type": "string"}
            return {
                "type": "array",
                "minItems": len(value),
                "maxItems": len(value),
                "items": item_schema,
            }
        elif isinstance(value, dict):
            value_type = "object"
        else:
            value_type = "string"
        return {"type": value_type, "const": value}

    result = expected["goal_closure_result"]
    coverage = result["goal_coverage"][0]
    properties: dict[str, Any] = {}
    for key, value in result.items():
        if key == "summary":
            properties[key] = {"type": "string", "minLength": 1}
        elif key == "goal_coverage":
            properties[key] = {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(coverage),
                    "properties": {
                        item_key: constant(item_value)
                        for item_key, item_value in coverage.items()
                    },
                },
            }
        else:
            properties[key] = constant(value)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["goal_closure_result"],
        "properties": {
            "goal_closure_result": {
                "type": "object",
                "additionalProperties": False,
                "required": list(result),
                "properties": properties,
            },
        },
    }


def _extract_json(value: str) -> dict[str, Any]:
    text = value.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            parsed, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("goal_closure_result"), dict):
            return parsed
    raise ValueError("provider output did not contain a goal_closure_result object")


def _invoke_claude(
    *,
    project_root: Path,
    state_dir: Path,
    prompt: str,
    schema: dict[str, Any],
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    session_id = str(uuid.uuid4())
    result = _run(
        [
            "claude",
            "-p",
            "--safe-mode",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Read",
            "--allowedTools",
            "Read",
            "--add-dir",
            str(state_dir),
            "--session-id",
            session_id,
            "--json-schema",
            json.dumps(schema, separators=(",", ":")),
            prompt,
        ],
        cwd=project_root,
        timeout=timeout,
    )
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    tool_names: list[str] = []
    final: dict[str, Any] | None = None
    reported_session = session_id
    for row in rows:
        reported_session = str(row.get("session_id") or reported_session)
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        content = message.get("content") if isinstance(message.get("content"), list) else []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                tool_names.append(str(item.get("name") or ""))
        structured = row.get("structured_output")
        if isinstance(structured, dict):
            final = structured
        elif row.get("type") == "result" and isinstance(row.get("result"), str):
            final = _extract_json(str(row["result"]))
    if final is None:
        raise ValueError("Claude did not return structured output")
    if "Read" not in tool_names or set(tool_names) - {"Read", "StructuredOutput"}:
        raise AssertionError(f"Claude Judge tool audit failed: {tool_names}")
    return final, {
        "backend": "claude-code",
        "provider_session_id": reported_session,
        "tool_names": tool_names,
        "result_rows": len(rows),
        "raw_stdout": result.stdout,
        "stderr": result.stderr,
    }


def _invoke_codex(
    *,
    project_root: Path,
    state_dir: Path,
    prompt: str,
    schema: dict[str, Any],
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    schema_path = state_dir / "provider-output-schema.json"
    output_path = state_dir / "provider-last-message.json"
    schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    result = _run(
        [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "-C",
            str(project_root),
            prompt,
        ],
        cwd=project_root,
        timeout=timeout,
    )
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    thread_id = ""
    commands: list[str] = []
    for row in rows:
        if row.get("type") == "thread.started":
            thread_id = str(row.get("thread_id") or "")
        item = row.get("item") if isinstance(row.get("item"), dict) else {}
        if item.get("type") == "command_execution":
            command = str(item.get("command") or "")
            commands.append(command)
            if int(item.get("exit_code") or 0) != 0:
                raise AssertionError(f"Codex Judge read command failed: {command}")
    forbidden = re.compile(
        r"(?:pytest|npm|pnpm|yarn|\bmake\b|git\s+(?:add|commit|checkout|merge|reset)|"
        r"sed\s+-i|\brm\b|\bmv\b|\bcp\b|\btee\b|(?:^|\s)>(?:>|\s))"
    )
    unsafe = [command for command in commands if forbidden.search(command)]
    if not commands or unsafe:
        raise AssertionError(f"Codex Judge tool audit failed: commands={commands}, unsafe={unsafe}")
    final = _extract_json(output_path.read_text(encoding="utf-8"))
    return final, {
        "backend": "codex",
        "provider_session_id": thread_id,
        "commands": commands,
        "result_rows": len(rows),
        "raw_stdout": result.stdout,
        "stderr": result.stderr,
    }


def _latest_manifest(state_dir: Path, stage_id: str) -> dict[str, Any]:
    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    for path in (state_dir / "fanouts").glob("*/manifest.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and str(data.get("stage_id") or "") == stage_id:
            candidates.append((path.stat().st_mtime, path, data))
    if not candidates:
        raise AssertionError(f"no fanout manifest for {stage_id}")
    return sorted(candidates, key=lambda item: item[0])[-1][2]


def _record_required_reads(state_dir: Path, child: dict[str, Any]) -> None:
    descriptor = child.get("attempt_source_manifest")
    if not isinstance(descriptor, dict):
        descriptor = {
            "ref": str(child.get("attempt_source_manifest_ref") or ""),
            "sha256": str(child.get("attempt_source_manifest_digest") or ""),
        }
    manifest = hydrate_sidecar_ref(state_dir, descriptor).payload
    if not isinstance(manifest, dict):
        raise AssertionError("attempt source manifest is not an object")
    sources = manifest.get("sources") if isinstance(manifest.get("sources"), list) else []
    for source in sources:
        if not isinstance(source, dict):
            continue
        read_attempt_artifact(
            state_dir,
            manifest=manifest,
            source_id=str(source.get("source_id") or ""),
            artifact_id=str(source.get("artifact_id") or ""),
        )


def _append_upstream_result(
    *,
    state_dir: Path,
    log: EventLog,
    workflow_run_id: str,
    generation: str,
    target_commit: str,
) -> dict[str, Any]:
    control = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "verification-result.v1",
            "execution_status": "completed",
            "verdict": "passed",
            "verification_owner": "candidate_verify",
            "verification_tier": "product",
            "requirement_results": [{
                "acceptance_id": "GOAL-AC-1",
                "status": "passed",
                "verification_owner": "candidate_verify",
                "verification_tier": "product",
                "evidence_refs": ["provider-drill:candidate-verify"],
                "findings": [],
                "reproduction_commands": ["pre-admitted-provider-drill"],
            }],
        },
        root="call-results/control/verification-result.v1",
        kind="call_control_result",
        schema_version="verification-result.v1",
        created_by="thin-judge-provider-drill",
    )
    envelope = normalize_call_result_envelope(
        source_payload={
            "run_id": f"candidate-verify-{generation}",
            "role_instance": "candidate-verify",
            "task_map_generation": generation,
            "target_commit": target_commit,
        },
        control_result={
            "schema_version": "verification-result.v1",
            "ref": control["ref"],
            "sha256": control["sha256"],
        },
        workflow_run_id=workflow_run_id,
        operation_id=f"candidate-verify-{generation}",
        request_hash=f"candidate-verify-request-{generation}",
        source_event_id=f"candidate-verify-result-{generation}",
        source_event_type="verify.child.completed",
        actor="candidate-verify",
    )
    descriptor = write_immutable_json_sidecar(
        state_dir,
        envelope,
        root="call-results/envelopes",
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="thin-judge-provider-drill",
    )
    log.append(ZfEvent(
        type="workflow.call.result.admitted",
        actor="zf-cli",
        correlation_id=workflow_run_id,
        payload={
            "workflow_run_id": workflow_run_id,
            "envelope_ref": descriptor,
            "control_result_ref": control,
            "control_result_schema": "verification-result.v1",
            "semantic_verdict": "passed",
        },
    ))
    return descriptor


def _trigger_payload(
    *,
    flow_kind: str,
    workflow_run_id: str,
    goal_id: str,
    task_map_ref: str,
    generation: str,
    candidate_ref: str,
    target_commit: str,
) -> tuple[str, dict[str, Any]]:
    common = {
        "workflow_run_id": workflow_run_id,
        "trace_id": workflow_run_id,
        "goal_id": goal_id,
        "pdd_id": goal_id,
        "feature_id": goal_id,
        "flow_kind": flow_kind,
        "task_map_ref": task_map_ref,
        "task_map_generation": generation,
        "candidate_ref": candidate_ref,
        "target_ref": candidate_ref,
        "candidate_head_commit": target_commit,
        "target_commit": target_commit,
        "open_p0_p1_gap_count": 0,
        "evidence_refs": ["provider-drill:candidate-verify"],
        "test_refs": ["provider-drill:pre-admitted"],
    }
    if flow_kind == "refactor":
        return "module.parity.scan.completed", common
    return "flow.discovery.completed", common


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm_real:
        raise SystemExit("pass --confirm-real to invoke a real provider")
    state_dir = args.state_dir.resolve()
    if state_dir.exists():
        raise FileExistsError(f"state dir already exists: {state_dir}")
    state_dir.mkdir(parents=True)
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    project_root = state_dir.parent / "project"
    flow_kind = args.flow_kind
    backend_key = args.backend.replace("-", "_")
    workflow_run_id = f"run-141-{flow_kind}-{backend_key}"
    goal_id = f"GOAL-141-{flow_kind.upper()}-{backend_key.upper()}"
    generation = "generation-1"
    candidate_ref, target_commit = _init_project(project_root, goal_id)
    objective = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "objective.v1",
            "goal_id": goal_id,
            "objective": f"Close the {flow_kind} Goal with admitted evidence",
        },
        root=f"artifacts/{flow_kind}",
        kind="objective",
        schema_version="objective.v1",
        created_by="thin-judge-provider-drill",
    )
    config = _config(
        state_dir=state_dir,
        project_root=project_root,
        backend=args.backend,
        flow_kind=flow_kind,
        objective_ref=objective["ref"],
    )
    transport = RecordingTransport()
    orchestrator = Orchestrator(
        state_dir,
        config,
        transport,  # type: ignore[arg-type]
        project_root=project_root,
    )
    log = EventLog(state_dir / "events.jsonl")
    writer = orchestrator.event_writer
    writer.append(ZfEvent(
        type="run.goal.started",
        actor="test",
        correlation_id=workflow_run_id,
        payload={
            "run_id": workflow_run_id,
            "workflow_run_id": workflow_run_id,
            "goal_id": goal_id,
            "objective": f"Close the {flow_kind} Goal with admitted evidence",
        },
    ))
    task_map = {
        "schema_version": "task-map.v1",
        "feature_id": goal_id,
        "objective": f"Close the {flow_kind} Goal with admitted evidence",
        "goal_claims": [{
            "goal_claim_id": "GOAL-AC-1",
            "text": "The candidate satisfies the requested Goal",
            "mandatory": True,
            "source_ref": objective["ref"],
        }],
        "tasks": [{
            "task_id": f"{goal_id}-TASK-1",
            "title": "Deliver the accepted candidate",
            "owner_role": "dev",
            "acceptance": ["GOAL-AC-1: candidate verification is admitted"],
        }],
    }
    task_map_path = state_dir / "artifacts" / flow_kind / "task-map.json"
    task_map_path.parent.mkdir(parents=True, exist_ok=True)
    task_map_path.write_text(json.dumps(task_map, indent=2), encoding="utf-8")
    task_map_ref = str(task_map_path.relative_to(state_dir))
    task_map_event = ZfEvent(
        id=f"task-map-{flow_kind}-{backend_key}",
        type="task_map.ready",
        actor="planner",
        correlation_id=workflow_run_id,
        payload={
            "workflow_run_id": workflow_run_id,
            "trace_id": workflow_run_id,
            "goal_id": goal_id,
            "pdd_id": goal_id,
            "feature_id": goal_id,
            "task_map_ref": task_map_ref,
            "task_map_generation": generation,
            "objective_ref": objective["ref"],
        },
    )
    log.append(task_map_event)
    orchestrator.run_once(events=[task_map_event])
    upstream = _append_upstream_result(
        state_dir=state_dir,
        log=log,
        workflow_run_id=workflow_run_id,
        generation=generation,
        target_commit=target_commit,
    )

    trigger_type, trigger_body = _trigger_payload(
        flow_kind=flow_kind,
        workflow_run_id=workflow_run_id,
        goal_id=goal_id,
        task_map_ref=task_map_ref,
        generation=generation,
        candidate_ref=candidate_ref,
        target_commit=target_commit,
    )
    trigger = ZfEvent(
        id=f"closure-source-{flow_kind}-{backend_key}",
        type=trigger_type,
        actor="candidate-verify",
        correlation_id=workflow_run_id,
        payload=trigger_body,
    )
    log.append(trigger)
    orchestrator.run_once(events=[trigger])
    if len(transport.sent) != 1:
        raise AssertionError(f"expected one Thin Judge dispatch, got {len(transport.sent)}")

    stage_id = f"{flow_kind}-thin-judge"
    manifest = _latest_manifest(state_dir, stage_id)
    children = [item for item in manifest.get("children", []) if isinstance(item, dict)]
    if len(children) != 1:
        raise AssertionError(f"expected one Thin Judge child, got {len(children)}")
    child = children[0]
    closure = next(
        event
        for event in reversed(log.read_all())
        if event.type == FLOW_CLOSURE_EVENTS[flow_kind]
        and str((event.payload or {}).get("workflow_run_id") or "") == workflow_run_id
    )
    closure_body = closure.payload if isinstance(closure.payload, dict) else {}
    claim_set = hydrate_sidecar_ref(
        state_dir,
        {
            "ref": str(closure_body.get("goal_claim_set_ref") or ""),
            "sha256": str(closure_body.get("goal_claim_set_digest") or ""),
        },
    ).payload
    expected_claims = claim_set.get("claims") if isinstance(claim_set, dict) else []
    if len(expected_claims) != 1:
        raise AssertionError("provider drill expects one canonical Goal claim")
    expected = {
        "goal_closure_result": {
            "schema_version": "goal-closure-result.v1",
            "workflow_run_id": workflow_run_id,
            "goal_id": goal_id,
            "flow_kind": flow_kind,
            "task_map_generation": generation,
            "target_commit": target_commit,
            "objective_ref": str(closure_body.get("objective_ref") or objective["ref"]),
            "goal_claim_set_ref": str(closure_body.get("goal_claim_set_ref") or ""),
            "goal_claim_set_digest": str(closure_body.get("goal_claim_set_digest") or ""),
            "planning_result_ref": str(closure_body.get("planning_result_ref") or task_map_ref),
            "candidate_ref": candidate_ref,
            "closure_fact_ref": str(closure_body.get("closure_fact_ref") or ""),
            "closure_fact_digest": str(closure_body.get("closure_fact_digest") or ""),
            "input_result_refs": [upstream["ref"]],
            "goal_coverage": [{
                "goal_claim_id": str(expected_claims[0].get("goal_claim_id") or ""),
                "status": "closed",
                "supporting_result_refs": [upstream["ref"]],
            }],
            "open_gap_refs": [],
            "verdict": "passed",
            "recommended_action": "complete",
            "summary": "All mandatory Goal claims are closed by admitted evidence.",
        },
    }
    briefing_path = transport.sent[0][1]
    provider_prompt = "\n".join([
        "You are the read-only Thin Judge for a ZaoFu provider drill.",
        f"Read the complete briefing at {briefing_path} and every required source ref listed there.",
        f"Resolve every relative artifact ref under the state directory {state_dir}.",
        "Do not run tests/build/package commands and do not modify any file.",
        "Return only the structured goal_closure_result required by the output schema.",
    ])
    schema = _goal_schema(expected)
    if args.backend == "claude-code":
        provider_result, audit = _invoke_claude(
            project_root=project_root,
            state_dir=state_dir,
            prompt=provider_prompt,
            schema=schema,
            timeout=args.timeout_seconds,
        )
    else:
        provider_result, audit = _invoke_codex(
            project_root=project_root,
            state_dir=state_dir,
            prompt=provider_prompt,
            schema=schema,
            timeout=args.timeout_seconds,
        )
    if provider_result != {
        **expected,
        "goal_closure_result": {
            **expected["goal_closure_result"],
            "summary": provider_result.get("goal_closure_result", {}).get("summary"),
        },
    }:
        raise AssertionError("provider result diverged from pinned Goal closure identity")
    _record_required_reads(state_dir, child)
    audit_path = state_dir / "provider-audit.json"
    safe_audit = {key: value for key, value in audit.items() if key not in {"raw_stdout", "stderr"}}
    audit_path.write_text(json.dumps(safe_audit, indent=2), encoding="utf-8")
    (state_dir / "provider-raw.jsonl").write_text(
        str(audit.get("raw_stdout") or ""),
        encoding="utf-8",
    )

    result_event = ZfEvent(
        id=f"judge-result-{flow_kind}-{backend_key}",
        type="judge.child.completed",
        actor=str(child.get("role_instance") or f"judge-{flow_kind}"),
        correlation_id=workflow_run_id,
        payload={
            **child,
            "fanout_id": str(manifest.get("fanout_id") or ""),
            "stage_id": stage_id,
            "goal_closure_result": provider_result["goal_closure_result"],
            "status": "completed",
            "recommendation": "approve",
            "report": {
                "child_id": str(child.get("child_id") or ""),
                "status": "passed",
                "summary": provider_result["goal_closure_result"]["summary"],
                "findings": [],
                "recommendation": "approve",
            },
        },
    )
    log.append(result_event)
    orchestrator.run_once(events=[result_event])
    synthesized = next(
        event for event in reversed(log.read_all())
        if event.type == "goal.closure.synthesized"
        and str((event.payload or {}).get("workflow_run_id") or "") == workflow_run_id
    )

    rework = writer.append(ZfEvent(
        id=f"open-feedback-{flow_kind}-{backend_key}",
        type="task.rework.requested",
        actor="candidate-verify",
        task_id=f"{goal_id}-TASK-1",
        correlation_id=workflow_run_id,
        payload={
            "workflow_run_id": workflow_run_id,
            "task_id": f"{goal_id}-TASK-1",
            "dispatch_id": "dispatch-feedback-1",
            "finding_ids": ["finding-provider-drill"],
        },
    ))
    orchestrator.run_once(events=[synthesized])
    events = log.read_all()
    claim = next(event for event in events if event.type == "run.goal.completion.claimed")
    if not any(event.type == "run.goal.completion.blocked" for event in events):
        raise AssertionError("open feedback did not block the active completion claim")

    writer.append(ZfEvent(
        type="task.dispatched",
        task_id=f"{goal_id}-TASK-1",
        causation_id=rework.id,
        correlation_id=workflow_run_id,
        payload={
            "workflow_run_id": workflow_run_id,
            "task_id": f"{goal_id}-TASK-1",
            "dispatch_id": "dispatch-feedback-1",
            "rework_request_event_id": rework.id,
        },
    ))
    writer.append(ZfEvent(
        type="dev.build.done",
        task_id=f"{goal_id}-TASK-1",
        correlation_id=workflow_run_id,
        payload={
            "workflow_run_id": workflow_run_id,
            "task_id": f"{goal_id}-TASK-1",
            "dispatch_id": "dispatch-feedback-1",
            "source_commit": target_commit,
        },
    ))
    verified = writer.append(ZfEvent(
        type="verify.passed",
        task_id=f"{goal_id}-TASK-1",
        correlation_id=workflow_run_id,
        payload={
            "workflow_run_id": workflow_run_id,
            "task_id": f"{goal_id}-TASK-1",
            "dispatch_id": "dispatch-feedback-1",
            "target_commit": target_commit,
        },
    ))
    maybe_complete_run_goal(orchestrator, verified)
    failed_delivery = next(
        event for event in reversed(log.read_all())
        if event.type == "run.delivery.failed"
        and str((event.payload or {}).get("claim_id") or "") == str(claim.payload["claim_id"])
    )
    writer.append(ZfEvent(
        type="candidate.integration.completed",
        actor="zf-cli",
        correlation_id=workflow_run_id,
        payload={
            "workflow_run_id": workflow_run_id,
            "pdd_id": goal_id,
            "goal_id": goal_id,
            "candidate_ref": candidate_ref,
            "branch": candidate_ref,
            "candidate_head_commit": target_commit,
            "quality_status": "passed",
        },
    ))
    requested = writer.append(ZfEvent(
        type="run.manager.action.requested",
        actor="run-manager",
        correlation_id=workflow_run_id,
        payload={
            "action": "ship-retry",
            "run_id": workflow_run_id,
            "claim_id": str(claim.payload["claim_id"]),
        },
    ))
    retry = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=project_root,
        actor="run-manager",
        source="thin-judge-provider-drill",
        surface="e2e",
    ).execute(
        action="ship-retry",
        requested_action="ship-retry",
        payload={
            "run_id": workflow_run_id,
            "claim_id": str(claim.payload["claim_id"]),
            "delivery_operation_id": str(failed_delivery.payload["delivery_operation_id"]),
            "target_ref": candidate_ref,
        },
        requested=requested,
    )
    if not retry.get("ok"):
        raise AssertionError(f"scoped delivery retry failed: {retry}")
    settled = next(
        event for event in reversed(log.read_all())
        if event.type == "run.delivery.settled"
        and str((event.payload or {}).get("claim_id") or "") == str(claim.payload["claim_id"])
    )
    maybe_complete_run_goal(orchestrator, settled)

    replay_transport = RecordingTransport()
    restarted = Orchestrator(
        state_dir,
        config,
        replay_transport,  # type: ignore[arg-type]
        project_root=project_root,
    )
    restarted.run_once(events=[closure])
    restarted.run_once(events=[synthesized])
    maybe_complete_run_goal(restarted, settled)

    events = log.read_all()
    counts = Counter(event.type for event in events)
    expected_counts = {
        "workflow.call.result.admitted": 2,
        "goal.closure.synthesized": 1,
        "run.goal.completion.claimed": 1,
        "run.delivery.requested": 1,
        "run.delivery.failed": 1,
        "run.delivery.settled": 1,
        "run.goal.completed": 1,
    }
    bad = {
        event_type: {"expected": expected_count, "actual": counts[event_type]}
        for event_type, expected_count in expected_counts.items()
        if counts[event_type] != expected_count
    }
    if bad:
        raise AssertionError(f"exactly-once event counts failed: {bad}")
    if replay_transport.sent:
        raise AssertionError("restart replay dispatched a second Thin Judge")
    _run(["git", "merge-base", "--is-ancestor", target_commit, "main"], cwd=project_root)
    writer.append(ZfEvent(
        type="simulation.done",
        actor="test",
        correlation_id=workflow_run_id,
        payload={
            "workflow_run_id": workflow_run_id,
            "flow_kind": flow_kind,
            "backend": args.backend,
        },
    ))
    return {
        "schema_version": "thin-judge-goal-closure-provider-drill.v1",
        "flow_kind": flow_kind,
        "backend": args.backend,
        "provider_session_id": safe_audit.get("provider_session_id", ""),
        "workflow_run_id": workflow_run_id,
        "goal_id": goal_id,
        "target_commit": target_commit,
        "candidate_ref": candidate_ref,
        "admitted_call_result_ref": synthesized.payload.get("admitted_call_result_ref"),
        "claim_id": claim.payload.get("claim_id"),
        "delivery_retry": {
            "status": retry.get("status"),
            "operation_id": retry.get("delivery_operation_id"),
        },
        "event_counts": dict(sorted(counts.items())),
        "provider_audit": safe_audit,
    }


def main() -> int:
    result = run(_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
