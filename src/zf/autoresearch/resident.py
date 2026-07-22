"""Opt-in resident consumer for autoresearch loop requests.

The resident can run standalone or as a ``zf start`` sidecar when
``runtime.autoresearch_resident.enabled`` is set. It is a thin consumer that
converts proposal-only loop requests into bounded CLI executions.
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
    # ACCEPTED is deliberately NOT here: it is the queue acknowledgement (see
    # run_resident_once) — an accepted-but-not-started request must stay
    # pending so a later tick still executes it. Only execution (started) or a
    # terminal answer removes a request from the pending pool.
    terminal = {LOOP_STARTED, LOOP_COMPLETED, LOOP_FAILED, LOOP_SKIPPED}
    out: set[str] = set()
    for event in events:
        if event.type not in terminal:
            continue
        out.add(loop_request_id_from_payload(_payload(event), fallback=event.id))
    return out


def _accepted_ids(events: list[ZfEvent]) -> set[str]:
    out: set[str] = set()
    for event in events:
        if event.type != LOOP_ACCEPTED:
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


_LOOP_RUNNER_FALLBACK_TIMEOUT_S = 1800


def _loop_runner_timeout_s(budget_cap: dict[str, Any] | None) -> int:
    """Wall-clock bound for one synchronous loop run: the loop's own declared
    budget (max_minutes) with 2x grace, else a 30min fallback."""
    try:
        max_minutes = int((budget_cap or {}).get("max_minutes") or 0)
    except (TypeError, ValueError):
        max_minutes = 0
    if max_minutes > 0:
        return max_minutes * 60 * 2
    return _LOOP_RUNNER_FALLBACK_TIMEOUT_S


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
        "--no-backlog-on-failure",
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
        # The resident is an execution transport, not a second repair owner.
        # It may only consume a request that Run Manager already accepted.
        for request in pending_repair_dispatches(
            events,
            request_types=("run.manager.repair.accepted",),
        ):
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
    max_actions_per_tick: int = 0,
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
    # Queue acknowledgement BEFORE the per-tick cap slices the action list:
    # the resident consumes single-file, so a request can sit queued behind a
    # long (bounded) loop for many minutes. Without any lifecycle event the
    # requester's staleness clock runs against the request itself and queued
    # work is declared stale (2026-07-10 R5: 8 queued rmar-* requests, 0
    # lifecycle, staled at 300s while the running loop was still inside its
    # budget). ACCEPTED here is the "resident has this in queue" anchor;
    # execution still emits STARTED, and _handled_ids keeps accepted-only
    # requests pending so later ticks run them.
    accepted: set[str] = set()
    if execute and authorized:
        log = EventLog(Path(state_dir) / "events.jsonl")
        try:
            accepted = _accepted_ids(log.read_all())
        finally:
            log.close()
        for action in actions:
            if action.action != "run_loop" or action.loop_request_id in accepted:
                continue
            writer.append(ZfEvent(
                type=LOOP_ACCEPTED,
                actor="zf-autoresearch-resident",
                payload={
                    "loop_request_id": action.loop_request_id,
                    "queued": True,
                    "command": action.command,
                },
            ))
            accepted.add(action.loop_request_id)
    if max_actions_per_tick > 0:
        actions = actions[:max_actions_per_tick]
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
        # ACCEPTED was already emitted as the queue acknowledgement above;
        # execution adds the STARTED anchor.
        writer.append(ZfEvent(
            type=LOOP_STARTED,
            actor="zf-autoresearch-resident",
            payload={"loop_request_id": action.loop_request_id, "command": action.command},
        ))
        # Enforce the loop's own declared budget (research_mode_contract
        # budget_cap.max_minutes, 2x grace) on the synchronous runner. The
        # resident consumes requests single-file, so one unbounded loop starves
        # every request behind it: 2026-07-10 R4 PRD, a loop wedged on an inner
        # 2h test runner blocked the resident 80+ min and 11 pending requests
        # got no lifecycle at all (run-manager staled them one by one). A
        # timeout surfaces as LOOP_FAILED (rc=124) and the resident moves on.
        timeout_s = _loop_runner_timeout_s(action.budget_cap)
        try:
            proc = runner(
                action.command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            proc = subprocess.CompletedProcess(
                action.command,
                returncode=124,
                stdout=(exc.stdout or b"").decode("utf-8", "replace")[-2000:]
                if isinstance(exc.stdout, bytes) else str(exc.stdout or "")[-2000:],
                stderr=(
                    f"resident bounded loop at {timeout_s}s "
                    f"(budget_cap={action.budget_cap or {}})"
                ),
            )
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
