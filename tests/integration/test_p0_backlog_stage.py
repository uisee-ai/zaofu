"""P0/K1: kernel no longer auto-applies arch.proposal.done into kanban
contract. The synthesis is reserved for stage ④ backlog (orchestrator
decision) per workflow.dag.design_to_backlog_owner=orchestrator.

This integration test exercises the chain:

  user.message → orchestrator creates feature+task
  arch.proposal.done → kernel records event, does NOT modify contract
  design.critique.done verdict=approve → orchestrator would synthesize,
                                          but in this test we mimic that
                                          synthesis with task.contract.update

and asserts:
  1. kanban contract stays empty between arch.proposal.done and the
     explicit task.contract.update (no auto-apply).
  2. After explicit task.contract.update, the contract is populated.
  3. The 6 required_backlog_refs survive into the kanban contract when
     orchestrator includes them in the synthesis payload.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
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
def orch(state_dir: Path) -> Orchestrator:
    config = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="arch", backend="mock"),
            RoleConfig(name="critic", backend="mock"),
            RoleConfig(name="dev", backend="mock"),
        ],
    )
    transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
    return Orchestrator(state_dir, config, transport)


def test_arch_proposal_done_does_not_auto_apply_contract(
    state_dir: Path, orch: Orchestrator
):
    """P0/K1: kernel must NOT write file_plan/test_plan into kanban contract
    when arch.proposal.done fires. That decision belongs to stage ④ backlog
    after critic.approve."""
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(
        id="TASK-K1",
        title="design task",
        contract=TaskContract(),  # empty
        assigned_to="arch",
    ))

    arch_event = ZfEvent(
        type="arch.proposal.done",
        actor="arch",
        task_id="TASK-K1",
        payload={
            "summary": "auto-apply guard test",
            "file_plan": ["src/foo.py", "tests/test_foo.py"],
            "test_plan": "pytest tests/test_foo.py -q",
            "evidence_refs": ["src/foo.py"],
            "dispatch_id": "disp-test",
        },
    )
    orch.event_writer.append(arch_event)
    orch._apply_housekeeping(arch_event)

    # After housekeeping, the task contract should still be empty.
    # No file_plan should have leaked into kanban.contract.scope.
    ts2 = TaskStore(state_dir / "kanban.json")
    task = ts2.get("TASK-K1")
    assert task is not None
    contract = task.contract or TaskContract()
    assert contract.scope == [], (
        f"expected empty scope after arch.proposal.done, got {contract.scope}"
    )
    assert (contract.behavior or "") == "", (
        f"expected empty behavior, got {contract.behavior!r}"
    )

    # Also: no task.contract.update event should have been auto-emitted by
    # the kernel from this arch.proposal.done.
    log = EventLog(state_dir / "events.jsonl")
    contract_updates = [
        e for e in log.read_all()
        if e.type == "task.contract.update" and e.task_id == "TASK-K1"
    ]
    assert contract_updates == [], (
        f"expected no auto-emitted task.contract.update, got {len(contract_updates)}"
    )


def test_explicit_task_contract_update_applies_synthesized_contract(
    state_dir: Path, orch: Orchestrator
):
    """When orchestrator explicitly emits task.contract.update (the stage ④
    synthesis), kernel SHOULD apply it to kanban via apply_task_contract_event.
    That's the legitimate kernel housekeeping path (Layer 2 → Layer 1)."""
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="TASK-K2", title="task", contract=TaskContract(), assigned_to="arch"))

    synth_event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="TASK-K2",
        payload={
            "contract": {
                "behavior": "synthesized after critic approve",
                "scope": ["src/x.py"],
                "verification": "pytest tests/test_x.py",
                "verification_tiers": ["runtime"],
                "owner_role": "dev",
                "spec_ref": "docs/specs/p1.md",
                "plan_ref": "arch.proposal.done:evt-xxx",
                "tdd_ref": "vitest 6 cases",
                "critic_event_id": "evt-critic-yyy",
                "critic_gate_ref": "approve: fixes applied",
                "evidence_contract": {"runtime": "pytest"},
            }
        },
    )
    orch.event_writer.append(synth_event)
    orch._apply_housekeeping(synth_event)

    ts2 = TaskStore(state_dir / "kanban.json")
    task = ts2.get("TASK-K2")
    assert task is not None
    contract = task.contract
    assert contract is not None
    assert contract.behavior == "synthesized after critic approve"
    assert contract.scope == ["src/x.py"]
    assert contract.spec_ref == "docs/specs/p1.md"
    assert contract.plan_ref == "arch.proposal.done:evt-xxx"
    assert contract.tdd_ref == "vitest 6 cases"
    assert contract.critic_event_id == "evt-critic-yyy"
    assert contract.critic_gate_ref == "approve: fixes applied"
    assert contract.evidence_contract == {"runtime": "pytest"}


def test_grep_no_auto_apply_in_arch_proposal_done_branch():
    """Wire-up self-check (CLAUDE.md anti-orphan): the auto-apply functions
    must not be called inside the arch.proposal.done branch of
    _apply_housekeeping."""
    src = Path(__file__).resolve().parents[2] / "src/zf/runtime/orchestrator.py"
    text = src.read_text(encoding="utf-8")

    # Locate the arch.proposal.done branch.
    # Take the substring between this elif and the next elif.
    marker = 'elif event.type == "arch.proposal.done":'
    idx = text.find(marker)
    assert idx >= 0, "arch.proposal.done branch missing"
    after = text[idx:]
    next_elif = after.find("elif event.type", len(marker))
    if next_elif < 0:
        branch = after
    else:
        branch = after[:next_elif]

    assert "apply_task_contract_event" not in branch, (
        "P0/K1: apply_task_contract_event must not be called in "
        "arch.proposal.done branch"
    )
    assert "spec_ingest_suggested_event" not in branch, (
        "P0/K1: spec_ingest_suggested_event must not be called in "
        "arch.proposal.done branch"
    )
    assert "arch_proposal_contract_update_event" not in branch, (
        "P0/K1: arch_proposal_contract_update_event must not be called in "
        "arch.proposal.done branch"
    )


def test_orchestrator_briefing_teaches_backlog_synthesis():
    """K3: orchestrator briefing must contain explicit guidance for stage ④
    backlog synthesis (6 refs + zf emit task.contract.update + configured dispatch).
    """
    src = Path(__file__).resolve().parents[2] / "src/zf/runtime/orchestrator_briefing.py"
    text = src.read_text(encoding="utf-8")

    assert "design.critique.done" in text and "verdict=approve" in text, (
        "K3: briefing must mention design.critique.done verdict=approve"
    )
    assert "required_backlog_refs" in text, (
        "K3: briefing must mention required_backlog_refs"
    )
    assert "critic_event_id" in text, "K3: briefing must list critic_event_id"
    assert "critic_gate_ref" in text, "K3: briefing must list critic_gate_ref"
    assert "evidence_contract" in text, "K3: briefing must list evidence_contract"
    assert "artifact.manifest.published" in text
    assert "不要把 `arch.proposal.done:<event_id>`" in text
    assert "zf-harness-backlog-synthesis" in text, (
        "K3: briefing must reference the backlog-synthesis skill"
    )


def test_orchestrator_role_context_skill_has_stage_routing():
    """S3: the zf-yoke-orchestrator-role-context skill must document stage
    ④ backlog ownership + the no-contract-at-intake rule."""
    src = (
        Path(__file__).resolve().parents[2]
        / "skills/zf-yoke-orchestrator-role-context/SKILL.md"
    )
    text = src.read_text(encoding="utf-8")

    assert "Stage ④ backlog" in text or "stage ④" in text.lower() or "Stage Routing" in text
    assert "backlog-synthesis" in text or "zf-harness-backlog-synthesis" in text
    assert "required_backlog_refs" in text or "6 `required_backlog_refs`" in text
    assert "no contract" in text.lower() or "no contract at intake" in text.lower() or \
           "No contract at intake" in text


def test_backlog_synthesis_skill_exists_and_has_frontmatter():
    """S1: the new backlog-synthesis skill exists with correct frontmatter."""
    src = (
        Path(__file__).resolve().parents[2]
        / "skills/zf-harness-backlog-synthesis/SKILL.md"
    )
    assert src.exists(), "S1: skill file missing"
    text = src.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "S1: must have frontmatter"
    assert "name: zf-harness-backlog-synthesis" in text
    assert "description:" in text
    # Must teach the 6 refs:
    for ref in [
        "spec_ref", "plan_ref", "tdd_ref",
        "critic_event_id", "critic_gate_ref", "evidence_contract",
    ]:
        assert ref in text, f"S1: skill must teach {ref}"
