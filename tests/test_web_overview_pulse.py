"""overview-pulse.v1 projection + route (Overview RUN PULSE / TASK FLOW bands)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.web.overview_pulse import build_overview_pulse
from zf.web.server import create_app

NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def _ts(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]")
    (sd / "feature_list.json").write_text("[]")
    return sd


def _seed(sd: Path, events: list[ZfEvent]) -> None:
    log = EventLog(sd / "events.jsonl")
    for event in events:
        log.append(event)


class TestRunPulse:
    def test_last_event_age_and_buckets(self, state_dir: Path):
        _seed(state_dir, [
            ZfEvent(type="loop.started", actor="zf-cli", ts=_ts(50)),
            ZfEvent(type="task.dispatched", actor="zf-cli", task_id="T1", ts=_ts(7)),
            ZfEvent(type="dev.build.done", actor="dev", task_id="T1", ts=_ts(2)),
        ])
        pulse = build_overview_pulse(state_dir, now=NOW)["run_pulse"]
        assert pulse["last_event_age_seconds"] == pytest.approx(120, abs=1)
        assert sum(pulse["events_per_bucket"]) == 3
        assert len(pulse["events_per_bucket"]) == 12
        # newest event lands in the right-most bucket
        assert pulse["events_per_bucket"][-1] == 1

    def test_sessions_summary_active_and_stale(self, state_dir: Path):
        import yaml
        fresh = datetime.now(timezone.utc).isoformat()
        (state_dir / "role_sessions.yaml").write_text(
            yaml.safe_dump({
                "roles": {"dev-1": "u1", "dev-2": "u2"},
                "instance_meta": {
                    "dev-1": {
                        "backend": "codex",
                        "last_heartbeat_at": fresh,
                        "last_heartbeat_payload": {"state": "busy"},
                    },
                    "dev-2": {
                        "backend": "claude",
                        "last_heartbeat_at": "2020-01-01T00:00:00+00:00",
                    },
                },
            }),
            encoding="utf-8",
        )
        _seed(state_dir, [ZfEvent(type="loop.started", actor="zf-cli", ts=_ts(5))])
        sessions = build_overview_pulse(state_dir, now=NOW)["run_pulse"]["sessions"]
        assert sessions["total"] == 2
        assert sessions["active"] == 1
        assert sessions["stale"] == 1
        assert sessions["by_backend"] == {"codex": 1}  # stale dev-2 excluded
        assert sessions["by_state"] == {"busy": 1}

    def test_respawn_failed_streak_counts_trailing_failures(self, state_dir: Path):
        _seed(state_dir, [
            ZfEvent(type="worker.spawned", actor="zf-cli", ts=_ts(30)),
            ZfEvent(type="worker.respawn.failed", actor="judge", ts=_ts(9)),
            ZfEvent(type="worker.respawn.failed", actor="judge", ts=_ts(6)),
            ZfEvent(type="worker.respawn.failed", actor="judge", ts=_ts(3)),
        ])
        pulse = build_overview_pulse(state_dir, now=NOW)["run_pulse"]
        assert pulse["respawn_failed_streak"] == 3

    def test_respawn_streak_resets_after_successful_spawn(self, state_dir: Path):
        _seed(state_dir, [
            ZfEvent(type="worker.respawn.failed", actor="judge", ts=_ts(10)),
            ZfEvent(type="worker.spawned", actor="zf-cli", ts=_ts(5)),
        ])
        pulse = build_overview_pulse(state_dir, now=NOW)["run_pulse"]
        assert pulse["respawn_failed_streak"] == 0

    def test_cooldown_instances_listed(self, state_dir: Path):
        _seed(state_dir, [
            ZfEvent(
                type="worker.respawn.cooldown", actor="zf-cli", ts=_ts(5),
                payload={"instance_id": "dev-lane-2"},
            ),
        ])
        pulse = build_overview_pulse(state_dir, now=NOW)["run_pulse"]
        assert pulse["respawn_cooldown_instances"] == ["dev-lane-2"]


class TestTaskFlow:
    def test_columns_rates_and_blocked_side_pocket(self, state_dir: Path):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="a", status="ready"))
        store.add(Task(id="T2", title="b", status="in_progress"))
        store.add(Task(id="T3", title="c", status="blocked", blocked_by=["T1"]))
        store.add(Task(id="T4", title="d", status="done"))
        _seed(state_dir, [
            ZfEvent(type="task.dispatched", actor="zf-cli", task_id="T2", ts=_ts(120)),
            ZfEvent(type="dev.build.done", actor="dev", task_id="T2", ts=_ts(60)),
            ZfEvent(type="task.done", actor="zf-cli", task_id="T4", ts=_ts(30)),
            ZfEvent(
                type="task.dispatched", actor="zf-cli", task_id="T2", ts=_ts(10),
                payload={"rework_kind": "workflow_stage_backedge"},
            ),
        ])
        flow = build_overview_pulse(state_dir, now=NOW)["task_flow"]
        assert flow["columns"]["todo"] == 1
        assert flow["columns"]["in_progress"] == 1
        assert flow["columns"]["blocked"] == 1
        assert flow["columns"]["done"] == 1
        assert flow["transitions_per_hour"]["todo_to_in_progress"] == pytest.approx(2 / 24, abs=1e-3)
        assert flow["transitions_per_hour"]["in_progress_to_verify"] == pytest.approx(1 / 24, abs=1e-3)
        assert flow["transitions_per_hour"]["verify_to_done"] == pytest.approx(1 / 24, abs=1e-3)
        assert flow["rework_backedge_per_hour"] == pytest.approx(1 / 24, abs=1e-3)
        assert flow["wip"]["used"] == 1
        pocket = flow["blocked_side_pocket"]
        assert pocket and pocket[0]["task_id"] == "T3"
        assert pocket[0]["reason"] == "blocked by T1"
        assert flow["done_gate"] == "judge AND-closure"

    def test_oldest_age_uses_latest_task_event(self, state_dir: Path):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="a", status="in_progress"))
        _seed(state_dir, [
            ZfEvent(type="task.dispatched", actor="zf-cli", task_id="T1", ts=_ts(41)),
        ])
        flow = build_overview_pulse(state_dir, now=NOW)["task_flow"]
        assert flow["oldest_age_seconds"]["in_progress"] == pytest.approx(41 * 60, abs=1)

    def test_fanout_queue_wait_counts_as_todo_not_blocked(self, state_dir: Path):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="TQ",
            title="queued",
            status="blocked",
            blocked_reason="fanout_queue:F1:queued-TQ-1",
        ))

        flow = build_overview_pulse(state_dir, now=NOW)["task_flow"]

        assert flow["columns"]["todo"] == 1
        assert flow["columns"]["blocked"] == 0
        assert flow["blocked_side_pocket"] == []


class TestAttention:
    def test_unacked_escalation_age_and_ack_clears(self, state_dir: Path):
        _seed(state_dir, [
            ZfEvent(type="human.escalate", actor="zf-cli", task_id="T1", ts=_ts(12)),
            ZfEvent(type="human.escalate", actor="zf-cli", task_id="T2", ts=_ts(50)),
            ZfEvent(type="human.resolved", actor="operator", task_id="T2", ts=_ts(40)),
        ])
        attention = build_overview_pulse(state_dir, now=NOW)["attention"]
        assert attention["unacked_escalations"] == 1
        assert attention["oldest_unacked_escalation_seconds"] == pytest.approx(12 * 60, abs=1)

    def test_remediation_open_by_tier_and_close(self, state_dir: Path):
        _seed(state_dir, [
            ZfEvent(
                type="remediation.classified", actor="zf-cli", ts=_ts(20),
                payload={"tier": "tier1"}, correlation_id="fp-1",
            ),
            ZfEvent(
                type="remediation.classified", actor="zf-cli", ts=_ts(18),
                payload={"tier": "tier2"}, correlation_id="fp-2",
            ),
            ZfEvent(type="remediation.consumed", actor="zf-cli", ts=_ts(5), correlation_id="fp-2"),
        ])
        attention = build_overview_pulse(state_dir, now=NOW)["attention"]
        assert attention["remediation_open_by_tier"] == {"tier1": 1}

    def test_safe_halt_flag_follows_latest_event(self, state_dir: Path):
        _seed(state_dir, [
            ZfEvent(type="runtime.safe_halted", actor="zf-cli", ts=_ts(30)),
            ZfEvent(type="runtime.resumed", actor="zf-cli", ts=_ts(10)),
        ])
        attention = build_overview_pulse(state_dir, now=NOW)["attention"]
        assert attention["safe_halt_active"] is False


class TestWhyNotAndRoute:
    def test_schema_and_why_not_present(self, state_dir: Path):
        _seed(state_dir, [ZfEvent(type="loop.started", actor="zf-cli", ts=_ts(1))])
        payload = build_overview_pulse(state_dir, now=NOW)
        assert payload["schema_version"] == "overview-pulse.v1"
        assert payload["is_derived_projection"] is True
        assert "summary" in payload["why_not"]

    def test_route_served_for_default_project(self, state_dir: Path):
        _seed(state_dir, [ZfEvent(type="loop.started", actor="zf-cli", ts=_ts(1))])
        client = TestClient(create_app(state_dir))
        response = client.get("/api/projects/default/overview-pulse")
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == "overview-pulse.v1"
        assert body["run_pulse"]["last_event_age_seconds"] is not None
