"""Provider-backed, proposal-only Optimizer Agent transport."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import stable_digest
from zf.runtime.evolution_skill_optimizer import (
    SkillOptimizationService,
    SkillOptimizerError,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


OPTIMIZER_AGENT_REQUEST_SCHEMA = "skill-optimizer-agent-request.v1"
OPTIMIZER_AGENT_REQUESTED = "evolution.skill_optimizer.proposal.requested"
OPTIMIZER_SELECTION_REQUESTED = "evolution.skill_optimizer.selection.requested"

ProviderRunner = Callable[..., subprocess.CompletedProcess[str]]


def request_skill_optimizer_proposal(
    *,
    state_dir: Path,
    writer: EventWriter,
    state_ref: Mapping[str, Any],
    train_evidence_ref: Mapping[str, Any],
    failure_cluster_refs: Sequence[Mapping[str, Any]],
    backend: str,
    model: str = "",
    reasoning_effort: str = "",
    timeout_seconds: int = 300,
    source_event_id: str = "",
) -> dict[str, Any]:
    """Emit one idempotent request without exposing Selection/Test bodies."""

    service = SkillOptimizationService(
        state_dir,
        event_log=writer.event_log,
        event_writer=writer,
        actor="run-manager-evolution",
    )
    context = service.agent_context(state_ref)
    backend = str(backend or "").strip()
    if backend not in {"codex", "claude-code"}:
        raise SkillOptimizerError(
            "Optimizer Agent backend must be codex or claude-code"
        )
    train_ref = _descriptor(train_evidence_ref, field="train_evidence_ref")
    train_split = context["train_split"]
    if train_ref["ref"] != str(train_split["ref"]) or train_ref["sha256"] != str(
        train_split["digest"]
    ):
        raise SkillOptimizerError("Optimizer Agent Train evidence identity drift")
    failures = [
        _descriptor(item, field="failure_cluster_ref") for item in failure_cluster_refs
    ]
    if not failures or len(failures) > 20:
        raise SkillOptimizerError("Optimizer Agent requires 1..20 failure cluster refs")
    request_key = stable_digest(
        {
            "campaign_id": context["campaign_id"],
            "state_ref": dict(state_ref),
            "train_evidence_ref": train_ref,
            "failure_cluster_refs": failures,
        }
    )
    existing = next(
        (
            event
            for event in writer.event_log.read_all()
            if event.type == OPTIMIZER_AGENT_REQUESTED
            and str(_payload(event).get("request_key") or "") == request_key
        ),
        None,
    )
    if existing is not None:
        return {
            "created": False,
            "request_event_id": existing.id,
            "request_ref": dict(_payload(existing).get("request_ref") or {}),
            "request_key": request_key,
        }
    request = {
        "schema_version": OPTIMIZER_AGENT_REQUEST_SCHEMA,
        "request_key": request_key,
        "campaign_id": context["campaign_id"],
        "state_ref": dict(state_ref),
        "base_content_digest": context["best_content_digest"],
        "epoch": int(context["epoch"]) + 1,
        "max_epochs": int(context["max_epochs"]),
        "max_edits_per_step": int(context["max_edits_per_step"]),
        "train_split": dict(train_split),
        "train_evidence_ref": train_ref,
        "failure_cluster_refs": failures,
        "backend": backend,
        "model": str(model or ""),
        "reasoning_effort": str(reasoning_effort or ""),
        "timeout_seconds": max(1, min(int(timeout_seconds), 1800)),
        "visibility_policy": {
            "train_body": True,
            "selection_body": False,
            "test_body": False,
            "evaluator_prompt": False,
        },
    }
    descriptor = write_immutable_json_sidecar(
        state_dir,
        request,
        root="evolution/skill-optimizer/agent-requests",
        kind="skill_optimizer_agent_request",
        schema_version=OPTIMIZER_AGENT_REQUEST_SCHEMA,
        created_by="run-manager-evolution",
        source_event_id=source_event_id,
    )
    event = writer.append(
        ZfEvent(
            type=OPTIMIZER_AGENT_REQUESTED,
            actor="run-manager",
            causation_id=source_event_id,
            correlation_id=str(context["campaign_id"]),
            payload={
                "schema_version": OPTIMIZER_AGENT_REQUEST_SCHEMA,
                "campaign_id": context["campaign_id"],
                "request_key": request_key,
                "request_ref": descriptor,
                "state_ref": dict(state_ref),
                "epoch": request["epoch"],
                "backend": backend,
                "timeout_seconds": request["timeout_seconds"],
            },
        )
    )
    return {
        "created": True,
        "request_event_id": event.id,
        "request_ref": descriptor,
        "request_key": request_key,
    }


def execute_skill_optimizer_proposal(
    *,
    state_dir: Path,
    request_event_id: str,
    writer: EventWriter,
    runner: ProviderRunner = subprocess.run,
) -> dict[str, Any]:
    """Run one isolated proposal turn and prepare, but never select, its edit."""

    request_event = next(
        (
            event
            for event in writer.event_log.read_all()
            if event.id == request_event_id
        ),
        None,
    )
    if request_event is None or request_event.type != OPTIMIZER_AGENT_REQUESTED:
        raise SkillOptimizerError("Optimizer Agent request event not found")
    request_ref = _payload(request_event).get("request_ref")
    request = _hydrate(
        state_dir,
        request_ref,
        schema_version=OPTIMIZER_AGENT_REQUEST_SCHEMA,
        purpose="skill-optimizer-agent-request",
    )
    service = SkillOptimizationService(
        state_dir,
        event_log=writer.event_log,
        event_writer=writer,
        actor="skill-optimizer-agent",
    )
    context = service.agent_context(request["state_ref"])
    if str(context["best_content_digest"]) != str(request["base_content_digest"]):
        raise SkillOptimizerError("Optimizer Agent request is stale")
    if dict(context["train_split"]) != dict(request["train_split"]):
        raise SkillOptimizerError("Optimizer Agent Train split drift")
    train_evidence = _hydrate(
        state_dir,
        request["train_evidence_ref"],
        purpose="skill-optimizer-train-evidence",
    )
    failure_clusters = [
        _hydrate(
            state_dir,
            descriptor,
            purpose="skill-optimizer-failure-cluster",
        )
        for descriptor in request["failure_cluster_refs"]
    ]
    prompt = _optimizer_prompt(
        context=context,
        train_evidence=train_evidence,
        failure_clusters=failure_clusters,
    )
    work = Path(tempfile.mkdtemp(prefix="zf-skill-optimizer-agent-"))
    try:
        output = work / "proposal.json"
        backend = str(request["backend"])
        if backend == "codex":
            command = [
                "codex",
                "exec",
                "--json",
                "--ephemeral",
                "--ignore-user-config",
                "--sandbox",
                "danger-full-access",
                "--skip-git-repo-check",
                "--output-last-message",
                str(output),
                "--config",
                "skills.bundled.enabled=false",
                *(["--model", str(request["model"])] if request.get("model") else []),
                *(
                    [
                        "--config",
                        f'model_reasoning_effort="{request["reasoning_effort"]}"',
                    ]
                    if request.get("reasoning_effort")
                    else []
                ),
                "-C",
                str(work),
                prompt,
            ]
        else:
            command = [
                "claude", "-p", "--permission-mode", "bypassPermissions",
                "--output-format",
                "json",
                *(["--model", str(request["model"])] if request.get("model") else []),
                prompt,
            ]
        from zf.integrations.controller.helper import run_provider
        proc = run_provider(
            command, backend=backend, state_dir=state_dir, model=str(request.get("model") or ""), runner=runner,
            cwd=work,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
            timeout=int(request["timeout_seconds"]),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Optimizer Agent {backend} exited {proc.returncode}: "
                f"{(proc.stderr or '')[-1000:]}"
            )
        if backend == "codex":
            raw = output.read_text(encoding="utf-8") if output.exists() else ""
        else:
            raw = _claude_final(proc.stdout or "")
        proposal = _parse_json_object(raw)
        prepared = service.prepare_step(request["state_ref"], proposal=proposal)
        selection = service.selection_context(request["state_ref"])
        event = writer.append(
            ZfEvent(
                type=OPTIMIZER_SELECTION_REQUESTED,
                actor="skill-optimizer-agent",
                causation_id=request_event.id,
                correlation_id=str(selection["campaign_id"]),
                payload={
                    "schema_version": "skill-optimizer-selection-request.v1",
                    "campaign_id": selection["campaign_id"],
                    "request_event_id": request_event.id,
                    "state_ref": dict(request["state_ref"]),
                    "step_ref": prepared["step_ref"],
                    "candidate_content_digest": prepared["candidate"]["content_digest"],
                    "selection_split": dict(selection["selection_split"]),
                },
            )
        )
        return {
            "ok": True,
            "proposal": proposal,
            "step_ref": prepared["step_ref"],
            "selection_request_event_id": event.id,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _optimizer_prompt(
    *,
    context: Mapping[str, Any],
    train_evidence: Mapping[str, Any],
    failure_clusters: Sequence[Mapping[str, Any]],
) -> str:
    visible = {
        "campaign_id": context["campaign_id"],
        "skill_name": context["skill_name"],
        "epoch": context["epoch"],
        "max_epochs": context["max_epochs"],
        "max_edits_per_step": context["max_edits_per_step"],
        "base_digest": context["best_content_digest"],
        "current_skill": context["best_content"],
        "train_split": context["train_split"],
        "train_evidence": dict(train_evidence),
        "failure_clusters": [dict(item) for item in failure_clusters],
        "rejection_buffer": context["rejection_buffer"],
        "slow_meta_state": context["slow_meta_state"],
        "slow_meta_revision": context["slow_meta_revision"],
    }
    encoded = json.dumps(visible, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > 262144:
        raise SkillOptimizerError(
            "Optimizer Agent visible Train context exceeds 256 KiB"
        )
    return (
        "You are a proposal-only Skill optimizer. Use only the supplied Train evidence. "
        "Do not infer Selection or Test cases. Propose at most the declared edit budget. "
        "Return one JSON object with schema_version=skill-edit-proposal.v1, campaign_id, "
        "base_digest, edits (add/delete/replace), and optional slow_meta_update. "
        "Do not modify files and do not add prose.\n\n" + encoded
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SkillOptimizerError("Optimizer Agent returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise SkillOptimizerError("Optimizer Agent proposal must be a JSON object")
    return value


def _claude_final(stdout: str) -> str:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    if isinstance(value, Mapping):
        return str(value.get("result") or "")
    return stdout


def _hydrate(
    state_dir: Path,
    descriptor: object,
    *,
    purpose: str,
    schema_version: str = "",
) -> dict[str, Any]:
    normalized = _descriptor(descriptor, field=purpose)
    hydrated = hydrate_sidecar_ref(
        state_dir,
        normalized,
        purpose=purpose,
        actor="skill-optimizer-agent",
    )
    if not isinstance(hydrated.payload, Mapping):
        raise SkillOptimizerError(f"{purpose} payload is invalid")
    body = dict(hydrated.payload)
    if schema_version and body.get("schema_version") != schema_version:
        raise SkillOptimizerError(f"{purpose} schema drift")
    return body


def _descriptor(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SkillOptimizerError(f"{field} must be an immutable sidecar descriptor")
    ref = str(value.get("ref") or "").strip()
    sha256 = str(value.get("sha256") or "").removeprefix("sha256:")
    if not ref or len(sha256) != 64:
        raise SkillOptimizerError(f"{field} descriptor identity is invalid")
    return {**dict(value), "ref": ref, "sha256": sha256}


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = [
    "OPTIMIZER_AGENT_REQUESTED",
    "OPTIMIZER_AGENT_REQUEST_SCHEMA",
    "OPTIMIZER_SELECTION_REQUESTED",
    "execute_skill_optimizer_proposal",
    "request_skill_optimizer_proposal",
]
