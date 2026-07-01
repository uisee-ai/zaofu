"""R13 backlog §B: candidate-rework exhaustion surfaces an owner-visible message.

R13 stalled after the candidate-rework loop fired its 2 bounded attempts on real
reviewer parity findings and then emitted a bare ``human.escalate`` — which
dead-ends unattended (nobody is watching). doc 79 no-dead-end: the exhaustion
must ALSO emit an ``owner.visible_message.requested`` so the operator is actually
notified (via the O-7 autodeliver) with the unresolved findings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def _rej(id_, pdd):
    return ZfEvent(
        id=id_, type="review.rejected", actor="zf-cli",
        payload={"target_ref": f"cand/{pdd}", "trace_id": "tr-1"},
    )


def _rework(pdd, rework_of):
    return ZfEvent(
        type="task_map.ready", actor="zf-cli",
        payload={"pdd_id": pdd, "rework_of": rework_of},
    )


def test_rework_exhaustion_emits_owner_visible_message(state_dir, config, transport):
    pdd = "CJMIN-TEST"
    log = EventLog(state_dir / "events.jsonl")
    # 2 prior bounded rework attempts (handle rej1/rej2), then a 3rd unhandled
    # rejection → attempt >= max_attempts(2) → escalate.
    for e in [
        _rej("r1", pdd), _rework(pdd, "r1"),
        _rej("r2", pdd), _rework(pdd, "r2"),
        _rej("r3", pdd),  # unhandled, exhausted
    ]:
        log.append(e)

    orch = Orchestrator(state_dir, config, transport)
    orch._run_candidate_rework_sweep()
    orch._run_candidate_rework_sweep()

    types = [e.type for e in log.read_all()]
    # no-dead-end: NOT just human.escalate — an owner-visible notification too
    assert "human.escalate" in types
    assert "owner.visible_message.requested" in types
    escalations = [e for e in log.read_all() if e.type == "human.escalate"]
    assert len(escalations) == 1
    assert escalations[0].payload["rework_of"] == "r3"
    owner = next(
        e for e in log.read_all() if e.type == "owner.visible_message.requested"
    )
    assert owner.payload["severity"] == "high"
    assert pdd in owner.payload["summary"]


def test_rework_task_map_ready_carries_task_map_ref(state_dir, config, transport):
    """R18 3a: the rework re-emit of task_map.ready must carry task_map_ref (+
    source_commit) from the original — writer_fanout_admission loads the task_map
    from it. Missing it → event.schema.violated + re-dispatched writers fall back
    to a stale/empty task_map (R18: task_map.ready schema violations)."""
    pdd = "CJMIN-TEST"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="task_map.ready", actor="zf-cli", payload={
        "pdd_id": pdd,
        "source_commit": "abc123",
        "task_map_ref": ".zf/artifacts/CJMIN-TEST/task_map.json",
    }))
    log.append(ZfEvent(  # rejection → bounded rework
        id="r1", type="review.rejected", actor="zf-cli",
        payload={"target_ref": f"cand/{pdd}", "trace_id": "tr-1"},
    ))

    orch = Orchestrator(state_dir, config, transport)
    orch._run_candidate_rework_sweep()

    rework = [
        e for e in log.read_all()
        if e.type == "task_map.ready" and e.payload.get("rework_of")
    ]
    assert rework, "expected a rework task_map.ready"
    assert rework[-1].payload.get("task_map_ref") == ".zf/artifacts/CJMIN-TEST/task_map.json"
    assert rework[-1].payload.get("source_commit") == "abc123"


def test_integration_failed_rework_recovers_task_map_ref_from_fanout_identity(
    state_dir, config, transport
):
    """Regression for restart recovery: integration.failed often has
    target_ref=main, so the sweep must recover the PDD through fanout_id."""
    pdd = "CJMIN-TEST"
    task_map_ref = ".zf/artifacts/CJMIN-TEST/task_map.json"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="task_map.ready", actor="zf-cli", payload={
        "pdd_id": pdd,
        "trace_id": "tr-1",
        "target_ref": "main",
        "source_commit": "abc123",
        "candidate_base_commit": "abc123",
        "task_map_ref": task_map_ref,
    }))
    log.append(ZfEvent(type="fanout.started", actor="zf-cli", payload={
        "fanout_id": "fanout-impl-1",
        "pdd_id": pdd,
        "feature_id": "FEATURE-1",
        "trace_id": "tr-1",
        "target_ref": "main",
        "task_map_ref": task_map_ref,
    }))
    log.append(ZfEvent(  # previous bad recovery should not suppress this fix
        type="task_map.ready", actor="zf-cli",
        payload={"pdd_id": "main", "rework_of": "i1", "rework_attempt": 1},
    ))
    log.append(ZfEvent(
        id="i1", type="integration.failed", actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-1",
            "target_ref": "main",
            "trace_id": "tr-1",
            "reason": "timeout",
        },
        correlation_id="tr-1",
    ))

    orch = Orchestrator(state_dir, config, transport)
    orch._run_candidate_rework_sweep()

    rework = [
        e for e in log.read_all()
        if e.type == "task_map.ready" and e.payload.get("rework_of") == "i1"
    ]
    assert rework, "expected corrected rework task_map.ready"
    assert rework[-1].payload.get("pdd_id") == pdd
    assert rework[-1].payload.get("task_map_ref") == task_map_ref
    assert rework[-1].payload.get("source_commit") == "abc123"
    assert rework[-1].payload.get("target_ref") == "main"
    assert rework[-1].payload.get("feature_id") == "FEATURE-1"


def test_stale_task_map_rework_uses_child_payload_and_tmp_anchor(
    state_dir, config, transport
):
    pdd = "CJMIN-TEST"
    task_map_ref = ".zf/artifacts/CJMIN-TEST/task_map.json"
    source_index_ref = ".zf/artifacts/CJMIN-TEST/source-index.json"
    tmp = state_dir / "tmp"
    tmp.mkdir()
    (tmp / f"task-map-ready-{pdd}.json").write_text(
        json.dumps({
            "pdd_id": pdd,
            "feature_id": "FEATURE-1",
            "trace_id": "tr-1",
            "target_ref": "main",
            "source_commit": "abc123",
            "candidate_base_commit": "abc123",
            "task_map_ref": task_map_ref,
            "source_index_ref": source_index_ref,
        }),
        encoding="utf-8",
    )
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="f1",
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-1",
            "child_id": "dev-lane-0-CJMIN-PI-CORE-001",
            "pdd_id": pdd,
            "feature_id": "FEATURE-1",
            "trace_id": "tr-1",
            "task_map_ref": task_map_ref,
            "source_index_ref": source_index_ref,
            "reason": "stale_task_map",
            "suggested_action": "use_latest_product_delivery_wave_ready",
        },
        correlation_id="tr-1",
    ))

    orch = Orchestrator(state_dir, config, transport)
    orch._run_candidate_rework_sweep()

    rework = [
        e for e in log.read_all()
        if e.type == "task_map.ready" and e.payload.get("rework_of") == "f1"
    ]
    assert rework, "expected stale task-map rework task_map.ready"
    assert rework[-1].payload.get("pdd_id") == pdd
    assert rework[-1].payload.get("task_map_ref") == task_map_ref
    assert rework[-1].payload.get("source_index_ref") == source_index_ref
    assert rework[-1].payload.get("source_commit") == "abc123"
    assert rework[-1].payload.get("candidate_base_commit") == "abc123"
