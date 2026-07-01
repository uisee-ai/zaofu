"""Opt-in resident consumer for autoresearch loop requests.

The resident is intentionally not wired into ``zf start`` by default. It is a
thin consumer that can be run by an operator/automation process to convert
proposal-only loop requests into bounded CLI executions.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from zf.autoresearch.loop_requests import (
    LOOP_ACCEPTED,
    LOOP_COMPLETED,
    LOOP_FAILED,
    LOOP_REQUESTED,
    LOOP_SKIPPED,
    LOOP_STARTED,
    build_research_mode_artifact_envelope,
    loop_request_id_from_payload,
    normalize_research_mode,
    research_mode_contract,
)
from zf.core.events.factory import event_log_from_project
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.repair_dispatch import pending_repair_dispatches


REVIEW_GATE_REQUESTED = "autoresearch.review_gate.requested"
REVIEW_GATE_ACCEPTED = "autoresearch.review_gate.accepted"
REVIEW_GATE_STARTED = "autoresearch.review_gate.started"
REVIEW_GATE_COMPLETED = "autoresearch.review_gate.completed"
REVIEW_GATE_FAILED = "autoresearch.review_gate.failed"
REVIEW_GATE_SKIPPED = "autoresearch.review_gate.skipped"


@dataclass(frozen=True)
class ResidentAction:
    loop_request_id: str
    action: str
    reason: str
    command: list[str]
    kind: str = "loop_request"
    fingerprint: str = ""
    attempt: int = 0
    research_mode: str = "debug"
    artifact_kind: str = ""
    output_kind: str = ""
    budget_cap: dict[str, Any] | None = None
    expected_output: list[str] | None = None
    artifact_envelope: dict[str, Any] | None = None
    review_gate_mode: str = ""
    run_dir: str = ""
    source_root: str = ""
    state_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "loop_request_id": self.loop_request_id,
            "kind": self.kind,
            "action": self.action,
            "reason": self.reason,
            "command": list(self.command),
            "fingerprint": self.fingerprint,
            "attempt": self.attempt,
            "research_mode": self.research_mode,
            "artifact_kind": self.artifact_kind,
            "output_kind": self.output_kind,
            "budget_cap": dict(self.budget_cap or {}),
            "expected_output": list(self.expected_output or []),
            "artifact_envelope": dict(self.artifact_envelope or {}),
            "review_gate_mode": self.review_gate_mode,
            "run_dir": self.run_dir,
            "source_root": self.source_root,
            "state_dir": self.state_dir,
        }


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _stable_repair_id(fingerprint: str, attempt: int) -> str:
    seed = f"{fingerprint}|{attempt}"
    return "arrp-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _review_gate_request_id_from_payload(
    payload: dict[str, Any],
    *,
    fallback: str,
) -> str:
    for key in ("request_id", "review_gate_id", "run_id", "failure_fingerprint"):
        text = str(payload.get(key) or "").strip()
        if text:
            return text
    return fallback


def _handled_ids(events: list[ZfEvent]) -> set[str]:
    terminal = {LOOP_ACCEPTED, LOOP_STARTED, LOOP_COMPLETED, LOOP_FAILED, LOOP_SKIPPED}
    out: set[str] = set()
    for event in events:
        if event.type not in terminal:
            continue
        out.add(loop_request_id_from_payload(_payload(event), fallback=event.id))
    return out


def _handled_review_gate_ids(events: list[ZfEvent]) -> set[str]:
    terminal = {
        REVIEW_GATE_ACCEPTED,
        REVIEW_GATE_STARTED,
        REVIEW_GATE_COMPLETED,
        REVIEW_GATE_FAILED,
        REVIEW_GATE_SKIPPED,
    }
    out: set[str] = set()
    for event in events:
        if event.type not in terminal:
            continue
        out.add(_review_gate_request_id_from_payload(_payload(event), fallback=event.id))
    return out


def pending_loop_requests(events: list[ZfEvent]) -> list[ZfEvent]:
    handled = _handled_ids(events)
    pending: list[ZfEvent] = []
    for event in events:
        if event.type != LOOP_REQUESTED:
            continue
        request_id = loop_request_id_from_payload(_payload(event), fallback=event.id)
        if request_id not in handled:
            pending.append(event)
    return pending


def pending_review_gate_requests(events: list[ZfEvent]) -> list[ZfEvent]:
    handled = _handled_review_gate_ids(events)
    pending: list[ZfEvent] = []
    for event in events:
        if event.type != REVIEW_GATE_REQUESTED:
            continue
        request_id = _review_gate_request_id_from_payload(_payload(event), fallback=event.id)
        if request_id not in handled:
            pending.append(event)
    return pending


def _command_for_request(
    payload: dict[str, Any],
    *,
    worktree: Path,
    state_dir: Path,
    output_root: Path,
) -> list[str]:
    request_id = loop_request_id_from_payload(payload, fallback="request")
    scenarios = _string_list(payload.get("scenarios")) or ["controlled-stuck-recovery"]
    fix_wait = str(payload.get("fix_wait_strategy") or "none")
    return [
        sys.executable,
        "-m",
        "zf.cli.main",
        "autoresearch",
        "loop",
        "--scenarios",
        *scenarios,
        "--worktree",
        str(worktree),
        "--parent-state-dir",
        str(state_dir),
        "--max-iterations",
        "1",
        "--fix-wait-strategy",
        fix_wait,
        "--output-dir",
        str(output_root / request_id),
    ]


def _path_from_payload(
    payload: dict[str, Any],
    key: str,
    *,
    fallback: Path,
) -> Path:
    raw = str(payload.get(key) or "").strip()
    return Path(raw) if raw else fallback


def _command_for_review_gate_request(
    payload: dict[str, Any],
    *,
    state_dir: Path,
) -> list[str]:
    request_id = _review_gate_request_id_from_payload(payload, fallback="request")
    run_dir = _path_from_payload(
        payload,
        "run_dir",
        fallback=state_dir / "autoresearch" / "resident" / request_id,
    )
    request_state_dir = _path_from_payload(payload, "state_dir", fallback=state_dir)
    source_root = _path_from_payload(payload, "source_root", fallback=state_dir.parent)
    return [
        sys.executable,
        "-m",
        "zf.cli.main",
        "autoresearch",
        "review-gate",
        "prepare",
        "--run-dir",
        str(run_dir),
        "--state-dir",
        str(request_state_dir),
        "--source-root",
        str(source_root),
    ]


def _command_for_repair_request(
    *,
    state_dir: Path,
    spawn: bool,
    backend: str,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "zf.cli.main",
        "self-repair",
        "run",
        "--state-dir",
        str(state_dir),
    ]
    if spawn:
        command.extend(["--spawn", "--backend", backend])
    return command


def plan_resident_actions(
    *,
    state_dir: Path,
    worktree_root: Path,
    output_root: Path,
    self_repair_consumer: bool = False,
    self_repair_spawn: bool = False,
    self_repair_backend: str = "",
) -> list[ResidentAction]:
    log = EventLog(Path(state_dir) / "events.jsonl")
    try:
        events = log.read_all()
    finally:
        log.close()
    actions: list[ResidentAction] = []
    for event in pending_loop_requests(events):
        payload = _payload(event)
        request_id = loop_request_id_from_payload(payload, fallback=event.id)
        if str(payload.get("apply_policy") or "proposal_only") != "proposal_only":
            actions.append(ResidentAction(
                loop_request_id=request_id,
                action="skip",
                reason="only proposal_only loop requests are accepted",
                command=[],
            ))
            continue
        command = _command_for_request(
            payload,
            worktree=worktree_root / request_id,
            state_dir=state_dir,
            output_root=output_root,
        )
        mode = normalize_research_mode(payload.get("mode") or payload.get("research_mode"))
        contract = research_mode_contract(mode)
        envelope = build_research_mode_artifact_envelope(payload)
        actions.append(ResidentAction(
            loop_request_id=request_id,
            action="run_loop",
            reason=str(payload.get("reason") or "autoresearch loop requested"),
            command=command,
            research_mode=mode,
            artifact_kind=str(contract.get("artifact_kind") or ""),
            output_kind=str(contract.get("output_kind") or ""),
            budget_cap=dict(contract.get("budget_cap") or {}),
            expected_output=list(payload.get("expected_output") or contract.get("expected_output") or []),
            artifact_envelope=envelope,
        ))
    for event in pending_review_gate_requests(events):
        payload = _payload(event)
        request_id = _review_gate_request_id_from_payload(payload, fallback=event.id)
        try:
            attempt = int(payload.get("attempt") or 1)
        except (TypeError, ValueError):
            attempt = 1
        try:
            attempt_cap = int(payload.get("attempt_cap") or 2)
        except (TypeError, ValueError):
            attempt_cap = 2
        budget_cap = (
            payload.get("budget_cap")
            if isinstance(payload.get("budget_cap"), dict) else {}
        )
        run_dir = _path_from_payload(
            payload,
            "run_dir",
            fallback=state_dir / "autoresearch" / "resident" / request_id,
        )
        request_state_dir = _path_from_payload(payload, "state_dir", fallback=state_dir)
        source_root = _path_from_payload(payload, "source_root", fallback=state_dir.parent)
        if attempt > attempt_cap:
            actions.append(ResidentAction(
                loop_request_id=request_id,
                kind="review_gate",
                action="skip",
                reason=f"review gate attempt cap reached ({attempt}>{attempt_cap})",
                command=[],
                fingerprint=str(payload.get("failure_fingerprint") or ""),
                attempt=attempt,
                budget_cap=budget_cap,
                review_gate_mode=str(payload.get("mode") or "auto"),
                run_dir=str(run_dir),
                source_root=str(source_root),
                state_dir=str(request_state_dir),
            ))
            continue
        actions.append(ResidentAction(
            loop_request_id=request_id,
            kind="review_gate",
            action="run_review_gate_prepare",
            reason=str(payload.get("reason") or "autoresearch review gate requested"),
            command=_command_for_review_gate_request(payload, state_dir=state_dir),
            fingerprint=str(payload.get("failure_fingerprint") or ""),
            attempt=attempt,
            budget_cap=budget_cap,
            review_gate_mode=str(payload.get("mode") or "auto"),
            run_dir=str(run_dir),
            source_root=str(source_root),
            state_dir=str(request_state_dir),
        ))
    if self_repair_consumer:
        repair_command = _command_for_repair_request(
            state_dir=state_dir,
            spawn=self_repair_spawn,
            backend=self_repair_backend,
        )
        for request in pending_repair_dispatches(events):
            actions.append(ResidentAction(
                loop_request_id=_stable_repair_id(request.fingerprint, request.attempt),
                kind="repair_dispatch",
                action="run_self_repair",
                reason=(
                    request.repair_task_payload.get("title")
                    or "autoresearch repair dispatch requested"
                ),
                command=repair_command,
                fingerprint=request.fingerprint,
                attempt=request.attempt,
            ))
    return actions


def run_resident_once(
    *,
    state_dir: Path,
    worktree_root: Path,
    output_root: Path,
    execute: bool = False,
    self_repair_consumer: bool = False,
    self_repair_spawn: bool = False,
    self_repair_backend: str = "",
    env: dict[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[ResidentAction]:
    actions = plan_resident_actions(
        state_dir=state_dir,
        worktree_root=worktree_root,
        output_root=output_root,
        self_repair_consumer=self_repair_consumer,
        self_repair_spawn=self_repair_spawn,
        self_repair_backend=self_repair_backend,
    )
    writer = EventWriter(event_log_from_project(state_dir))
    src_env = os.environ if env is None else env
    authorized = str(src_env.get("ZF_AUTORESEARCH_RESIDENT") or "").lower() == "authorized"
    for action in actions:
        if action.action == "skip":
            if execute and authorized:
                writer.append(ZfEvent(
                    type=(
                        REVIEW_GATE_SKIPPED
                        if action.kind == "review_gate" else LOOP_SKIPPED
                    ),
                    actor="zf-autoresearch-resident",
                    payload=_action_event_payload(action),
                ))
            continue
        if action.action == "run_self_repair":
            if execute and authorized:
                runner(action.command, capture_output=True, text=True, check=False)
            continue
        if action.action != "run_loop":
            if action.action == "run_review_gate_prepare":
                if execute and authorized:
                    _run_review_gate_action(
                        writer=writer,
                        action=action,
                        runner=runner,
                    )
                continue
            if execute and authorized:
                writer.append(ZfEvent(
                    type=LOOP_SKIPPED,
                    actor="zf-autoresearch-resident",
                    payload={
                        "loop_request_id": action.loop_request_id,
                        "reason": action.reason,
                    },
                ))
            continue
        if not execute or not authorized:
            continue
        writer.append(ZfEvent(
            type=LOOP_ACCEPTED,
            actor="zf-autoresearch-resident",
            payload={"loop_request_id": action.loop_request_id, "command": action.command},
        ))
        writer.append(ZfEvent(
            type=LOOP_STARTED,
            actor="zf-autoresearch-resident",
            payload={"loop_request_id": action.loop_request_id, "command": action.command},
        ))
        proc = runner(action.command, capture_output=True, text=True, check=False)
        event_type = LOOP_COMPLETED if proc.returncode == 0 else LOOP_FAILED
        writer.append(ZfEvent(
            type=event_type,
            actor="zf-autoresearch-resident",
            payload={
                "loop_request_id": action.loop_request_id,
                "mode": action.research_mode,
                "artifact_envelope": action.artifact_envelope or {},
                "returncode": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-2000:],
                "stderr_tail": (proc.stderr or "")[-2000:],
            },
        ))
    return actions


def _action_event_payload(action: ResidentAction) -> dict[str, Any]:
    payload = {
        "loop_request_id": action.loop_request_id,
        "request_id": action.loop_request_id,
        "kind": action.kind,
        "reason": action.reason,
        "command": list(action.command),
        "fingerprint": action.fingerprint,
        "attempt": action.attempt,
        "mode": action.review_gate_mode,
        "budget_cap": dict(action.budget_cap or {}),
        "run_dir": action.run_dir,
        "state_dir": action.state_dir,
        "source_root": action.source_root,
    }
    return {key: value for key, value in payload.items() if value not in ("", [], {})}


def _run_review_gate_action(
    *,
    writer: EventWriter,
    action: ResidentAction,
    runner: Callable[..., subprocess.CompletedProcess],
) -> None:
    base_payload = _action_event_payload(action)
    writer.append(ZfEvent(
        type=REVIEW_GATE_ACCEPTED,
        actor="zf-autoresearch-resident",
        payload=base_payload,
    ))
    writer.append(ZfEvent(
        type=REVIEW_GATE_STARTED,
        actor="zf-autoresearch-resident",
        payload=base_payload,
    ))
    proc = runner(action.command, capture_output=True, text=True, check=False)
    event_type = REVIEW_GATE_COMPLETED if proc.returncode == 0 else REVIEW_GATE_FAILED
    payload = {
        **base_payload,
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
    }
    parsed = _json_object(proc.stdout or "")
    if parsed:
        payload["summary"] = parsed
        refs = {
            "codebase_context_pack": parsed.get("codebase_context_pack"),
            "failure_evidence_pack": parsed.get("failure_evidence_pack"),
            "events_summary": parsed.get("events_summary"),
        }
        payload["artifact_refs"] = {
            key: str(value) for key, value in refs.items() if str(value or "")
        }
        policy = parsed.get("policy")
        if isinstance(policy, dict):
            payload["policy"] = policy
            payload["route"] = str(policy.get("route") or "")
            payload["severity"] = str(policy.get("severity") or "")
    writer.append(ZfEvent(
        type=event_type,
        actor="zf-autoresearch-resident",
        payload=payload,
    ))


def _json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def actions_json(actions: list[ResidentAction]) -> str:
    return json.dumps([action.to_dict() for action in actions], ensure_ascii=False, indent=2)


__all__ = [
    "ResidentAction",
    "REVIEW_GATE_REQUESTED",
    "REVIEW_GATE_ACCEPTED",
    "REVIEW_GATE_STARTED",
    "REVIEW_GATE_COMPLETED",
    "REVIEW_GATE_FAILED",
    "REVIEW_GATE_SKIPPED",
    "actions_json",
    "pending_review_gate_requests",
    "pending_loop_requests",
    "plan_resident_actions",
    "run_resident_once",
]
