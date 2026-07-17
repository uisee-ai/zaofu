from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from zf.core.config.schema import (
    AutoresearchConfig,
    AutoresearchTriggerPolicyConfig,
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.autoresearch.self_repair import (
    candidate_from_trigger_event,
    candidate_with_diagnosis,
    repair_task_payload_from_candidate,
    write_candidate_artifact,
)
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.repair_authorization import (
    auto_repair_consumer_enabled,
    configured_self_repair_backend,
)
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _state_dir(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def _orchestrator(state_dir: Path, *, repair_mode: str) -> Orchestrator:
    config = ZfConfig(
        project=ProjectConfig(name="repair-bridge"),
        session=SessionConfig(tmux_session="repair-bridge"),
        roles=[
            RoleConfig(
                name="dev",
                backend="mock",
                publishes=["dev.build.done"],
            ),
        ],
        autoresearch=AutoresearchConfig(
            trigger_policy=AutoresearchTriggerPolicyConfig(
                repair_mode=repair_mode,
            ),
        ),
    )
    return Orchestrator(
        state_dir,
        config,
        TmuxTransport(TmuxSession(session_name="repair-bridge", dry_run=True)),
    )


def _task_ref_trigger() -> ZfEvent:
    return ZfEvent(
        type="autoresearch.trigger.accepted",
        actor="zf-autoresearch",
        task_id="CJMIN-PROVIDER-001",
        payload={
            "trigger_id": "ar-task-ref",
            "source": "autoresearch.invocation.accepted",
            "severity": "high",
            "reason": "task ref rejected after dev.build.done",
            "fingerprint": (
                "task_ref_rejected:CJMIN-PROVIDER-001:evt-dev-build"
            ),
            "signal_ids": ["sig-task-ref"],
            "evidence_paths": [".zf/events.jsonl"],
            "source_event_id": "evt-ref-rejected",
        },
    )


def _confirmed_task_ref_candidate(state_dir: Path) -> ZfEvent:
    trigger = _task_ref_trigger()
    candidate = candidate_with_diagnosis(
        candidate_from_trigger_event(trigger),
        status="confirmed",
        diagnosis_evidence_paths=["records/task-ref-reproduction.md"],
        repair_scope=[
            "src/zf/runtime/task_refs.py",
            "tests/test_task_refs.py",
        ],
        resolution_reason="focused regression reproduces task-ref handoff rejection",
    )
    path = write_candidate_artifact(state_dir, candidate)
    repair_task_payload = repair_task_payload_from_candidate(candidate, candidate_path=path)
    assert repair_task_payload is not None
    return ZfEvent(
        type="autoresearch.bug_candidate.confirmed",
        actor="run-manager",
        task_id=trigger.task_id,
        payload={
            "candidate": candidate.to_dict(),
            "candidate_id": candidate.candidate_id,
            "candidate_path": str(path),
            "repair_task_payload": repair_task_payload,
            "recovery_case_id": candidate.recovery_case_id,
            "source_event_id": "evt-diagnosis",
        },
    )


def test_bounded_repair_emits_consumable_repair_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ZF_AUTORESEARCH_AUTO_REPAIR", raising=False)
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    event = _task_ref_trigger()
    log.append(event)
    orch = _orchestrator(state_dir, repair_mode="bounded_repair")

    first = orch._on_autoresearch_trigger_accepted(event)
    second = orch._on_autoresearch_trigger_accepted(event)
    confirmed = _confirmed_task_ref_candidate(state_dir)
    log.append(confirmed)
    dispatch = orch._on_autoresearch_bug_candidate_confirmed(confirmed)

    events = log.read_all()
    dispatches = [
        item for item in events
        if item.type == "autoresearch.repair.dispatch_requested"
    ]
    assert first is not None and first.action == "notify"
    assert second is not None and second.action == "skip"
    assert dispatch is not None and dispatch.action == "notify"
    assert len(dispatches) == 1
    payload = dispatches[0].payload
    assert payload["fingerprint"].startswith("task_ref_rejected:")
    assert payload["attempt"] == 1
    assert payload["candidate_id"].startswith("HIC-")
    assert payload["candidate_path"]
    assert payload["repair_task_payload"]["contract"]["phase"] == "zaofu_self_repair"
    assert payload["apply_policy"] == "bounded_repair"
    assert payload["failure_class"] == "task_ref_rejected"
    assert payload["source_event_id"] == confirmed.id
    assert payload["resume_checkpoint_ref"] == "evt-diagnosis"


def test_proposal_only_does_not_dispatch_repair_without_env_authorization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ZF_AUTORESEARCH_AUTO_REPAIR", raising=False)
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    event = _task_ref_trigger()
    log.append(event)
    orch = _orchestrator(state_dir, repair_mode="proposal_only")

    decision = orch._on_autoresearch_trigger_accepted(event)

    events = log.read_all()
    assert decision is not None and decision.action == "notify"
    assert any(item.type == "autoresearch.bug_candidate.created" for item in events)
    assert not [
        item for item in events
        if item.type == "autoresearch.repair.dispatch_requested"
    ]


def test_repair_attempt_exhaustion_returns_request_to_run_manager(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    event = _task_ref_trigger()
    log.append(event)
    orch = _orchestrator(state_dir, repair_mode="bounded_repair")

    with patch(
        "zf.runtime.repair_authorization.decide_repair",
        return_value=SimpleNamespace(
            action="escalate",
            reason="repair attempt cap reached",
            fingerprint="task_ref_rejected:cap",
            attempt=3,
        ),
    ):
        orch._on_autoresearch_trigger_accepted(event)
        confirmed = _confirmed_task_ref_candidate(state_dir)
        log.append(confirmed)
        orch._on_autoresearch_bug_candidate_confirmed(confirmed)

    events = log.read_all()
    requests = [
        item for item in events
        if item.type == "autoresearch.repair.escalation.requested"
    ]
    assert len(requests) == 1
    assert requests[0].payload["owner_route"] == "run_manager"
    assert not any(item.type == "human.escalate" for item in events)


def test_bounded_repair_config_authorizes_consumer_without_env(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ZF_AUTORESEARCH_AUTO_REPAIR", raising=False)
    monkeypatch.delenv("ZF_AUTORESEARCH_SELF_REPAIR_BACKEND", raising=False)
    cfg = ZfConfig(
        autoresearch=AutoresearchConfig(
            trigger_policy=AutoresearchTriggerPolicyConfig(
                repair_mode="bounded_repair",
                self_repair_backend="codex",
            ),
        ),
    )

    assert auto_repair_consumer_enabled(cfg) is True
    assert configured_self_repair_backend(cfg) == "codex"


def test_self_repair_backend_env_overrides_config(monkeypatch) -> None:
    monkeypatch.setenv("ZF_AUTORESEARCH_SELF_REPAIR_BACKEND", "claude")
    cfg = ZfConfig(
        autoresearch=AutoresearchConfig(
            trigger_policy=AutoresearchTriggerPolicyConfig(
                repair_mode="bounded_repair",
                self_repair_backend="codex",
            ),
        ),
    )

    assert configured_self_repair_backend(cfg) == "claude"


def test_self_repair_backend_infers_single_agent_backend(monkeypatch) -> None:
    monkeypatch.delenv("ZF_AUTORESEARCH_SELF_REPAIR_BACKEND", raising=False)
    cfg = ZfConfig(
        roles=[RoleConfig(name="dev", backend="codex")],
    )

    assert configured_self_repair_backend(cfg) == "codex"


def test_self_repair_backend_does_not_guess_mixed_agent_backend(monkeypatch) -> None:
    monkeypatch.delenv("ZF_AUTORESEARCH_SELF_REPAIR_BACKEND", raising=False)
    cfg = ZfConfig(
        roles=[
            RoleConfig(name="dev", backend="codex"),
            RoleConfig(name="verify", backend="claude-code"),
        ],
    )

    assert configured_self_repair_backend(cfg) == ""
