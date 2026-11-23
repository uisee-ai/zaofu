import subprocess
from types import SimpleNamespace

from zf.core.config.schema import QualityGateConfig
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.milestone_handoff import milestone_readiness
from zf.runtime.stage_actions import (
    StageActionContext,
    StageActionPlan,
    StageActionRunner,
)


def test_bound_static_producer_resolves_exact_milestone_only(tmp_path):
    repo = tmp_path / "product"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True
        ).strip()

    git("init", "-q")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.test",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "base",
    )
    target = git("rev-parse", "HEAD")
    cfg = SimpleNamespace(
        workflow=SimpleNamespace(flow_metadata={"milestone_handoff": True}),
        quality_gates={
            k: QualityGateConfig(enabled=True, required_checks=["true"])
            for k in ("static", "lint")
        },
    )
    log = EventLog(tmp_path / "events.jsonl")
    binding = dict(
        workflow_run_id="run",
        task_map_generation="g",
        target_commit=target,
        contract_snapshot_digest="a" * 64,
        task_id="one",
    )

    def run_gate(origin="kernel"):
        trigger = ZfEvent(
            type="dev.build.done", task_id="one", origin=origin, payload=binding
        )
        StageActionRunner()._run_gate(
            StageActionPlan(
                action_type="run_gate",
                stage_id="static",
                task_id="one",
                payload={"gate": "static"},
            ),
            StageActionContext(
                event_writer=EventWriter(log),
                source_event=trigger,
                config=cfg,
                project_root=str(repo),
            ),
        )
        return log.read_all()[-1]

    h = {
        "consumer": {"operation_id": "op", "attempt_id": "a", "actor": "verify"},
        "next_owner": "verify",
        "binding": binding,
        "remaining_acceptance": [],
        "required_gates": ["lint"],
    }
    assert "gate_id" not in run_gate("agent").payload
    event = run_gate()
    assert milestone_readiness(tmp_path, [event], h)["mandatory_gates"]["lint"] == {
        "status": "passed",
        "event_id": event.id,
    }
    for key in (
        "target_commit",
        "task_map_generation",
        "contract_snapshot_digest",
        "workflow_run_id",
    ):
        changed = {**h, "binding": {**binding, key: "other"}}
        assert (
            milestone_readiness(tmp_path, [event], changed)["mandatory_gates"]["lint"][
                "status"
            ]
            == "pending"
        )
    (repo / "uncommitted").write_text("draft")
    assert "gate_id" not in run_gate().payload
