"""P2-2: Layer 2 orchestrator briefing generates workflow description
from WorkflowTopology, not hardcoded 4 stages.

Verifies briefing content adapts to:
- safe-team (6 roles, full chain with judge)
- code-assist (3 roles, no arch/judge)
- design-first (5 roles, has critic)
- custom YAML with unusual role names
"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.workflow.topology import WorkflowTopology
from zf.runtime.orchestrator_briefing import (
    build_orchestrator_briefing,
    _render_workflow_from_topology,
    _render_terminal_rule,
    _initial_intake_role,
    _primary_implementation_role,
)


EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _load(name: str) -> ZfConfig:
    path = EXAMPLES / name
    if not path.exists():
        path = EXAMPLES / "tmp" / name
    return load_config(path)


def test_topology_description_mentions_publishers_and_events():
    topology = WorkflowTopology.from_config(_load("safe-team.yaml"))
    desc = topology.to_workflow_description()
    # Should mention actual role names + actual events
    assert "arch" in desc
    assert "dev" in desc
    assert "review" in desc
    assert "arch.proposal.done" in desc
    assert "review.approved" in desc


def test_topology_description_empty_for_no_edges():
    config = ZfConfig(project=ProjectConfig(name="empty"), roles=[])
    topology = WorkflowTopology.from_config(config)
    assert topology.to_workflow_description() == ""


def test_briefing_workflow_adapts_to_code_assist():
    """code-assist.yaml has no judge — briefing should NOT suggest judge."""
    config = _load("code-assist.yaml")
    desc = _render_workflow_from_topology(config)
    # judge role doesn't exist in code-assist → no judge rules
    assert "judge" not in desc


def test_briefing_workflow_adapts_to_safe_team():
    """safe-team.yaml has judge — briefing MUST list judge transitions."""
    config = _load("safe-team.yaml")
    desc = _render_workflow_from_topology(config)
    assert "judge" in desc
    assert "test.passed" in desc


def test_terminal_rule_picks_judge_when_present():
    """If judge role publishes judge.passed, that's the terminal gate."""
    config = _load("safe-team.yaml")
    rule = _render_terminal_rule(config)
    assert "judge.passed" in rule
    assert "terminal claim" in rule
    assert "不要手动" in rule


def test_terminal_rule_falls_back_to_test_when_no_judge():
    """code-assist.yaml has no judge → test.passed is the terminal gate."""
    config = _load("code-assist.yaml")
    rule = _render_terminal_rule(config)
    # Must NOT mention judge.passed (role doesn't exist)
    assert "judge.passed" not in rule
    # Should pick test.passed since test role publishes it
    assert "test.passed" in rule


def test_terminal_rule_custom_role():
    """Custom role with `doc.approved` publish → that's the terminal."""
    config = ZfConfig(
        project=ProjectConfig(name="custom"),
        roles=[
            RoleConfig(name="writer", publishes=["writer.done"]),
            RoleConfig(name="editor", publishes=["editor.approved"]),
        ],
    )
    rule = _render_terminal_rule(config)
    assert "editor.approved" in rule
    assert "kanban move" in rule


def test_custom_role_in_workflow_description():
    """Arbitrary role names appear in description unchanged."""
    config = ZfConfig(
        project=ProjectConfig(name="custom"),
        roles=[
            RoleConfig(
                name="researcher",
                triggers=["task.assigned"],
                publishes=["research.done"],
            ),
            RoleConfig(
                name="summarizer",
                triggers=["research.done"],
                publishes=["summary.done"],
            ),
        ],
    )
    desc = _render_workflow_from_topology(config)
    assert "researcher" in desc
    assert "summarizer" in desc
    assert "research.done" in desc


def test_codex_briefing_prefers_arch_first_for_user_message(tmp_path: Path):
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state / "events.jsonl").write_text("", encoding="utf-8")
    (state / "memory").mkdir()
    config = _load("dev-codex-backends.yaml")

    briefing = build_orchestrator_briefing(
        state_dir=state,
        config=config,
        trigger_event=ZfEvent(type="user.message", actor="human"),
    )

    assert 'assign "$task_id" arch' in briefing
    assert "不要绕过设计门直接派发下游角色" in briefing
    assert "按 zf.yaml 选择下一步: `dev`" in briefing
    assert "Layer 1 会路由到 dev" not in briefing
    assert "最终实现交付" in briefing
    assert "raw relative paths" in briefing
    assert "design.critique.done" in briefing
    assert "zf feature add \"Feature title\" --id-only" in briefing
    assert "do not parse human-readable output" in briefing


def test_code_assist_briefing_does_not_assign_missing_arch(tmp_path: Path):
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state / "events.jsonl").write_text("", encoding="utf-8")
    (state / "memory").mkdir()
    config = _load("code-assist.yaml")

    briefing = build_orchestrator_briefing(
        state_dir=state,
        config=config,
        trigger_event=ZfEvent(type="user.message", actor="human"),
    )

    assert _initial_intake_role(config) == "dev"
    assert "no `arch` intake role" in briefing
    assert "do not assign `arch`" in briefing
    assert 'assign "$task_id" arch' not in briefing
    assert 'assign "$task_id" dev' in briefing


def test_plan_only_briefing_does_not_invent_dev_stage(tmp_path: Path):
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state / "events.jsonl").write_text("", encoding="utf-8")
    (state / "memory").mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="plan-only"),
        roles=[
            RoleConfig(
                name="orchestrator",
                triggers=["user.message", "design.critique.done"],
                publishes=["task.created", "feature.created"],
            ),
            RoleConfig(
                name="arch",
                triggers=["task.assigned"],
                publishes=["arch.proposal.done", "clarification.needed"],
                stages=["design"],
            ),
            RoleConfig(
                name="critic",
                role_kind="reader",
                triggers=["arch.proposal.done"],
                publishes=["design.critique.done", "gate.failed"],
                stages=["design_critique"],
            ),
        ],
    )

    briefing = build_orchestrator_briefing(
        state_dir=state,
        config=config,
        trigger_event=ZfEvent(type="design.critique.done", actor="critic"),
    )

    assert _primary_implementation_role(config) == ""
    assert "no implementation writer role" in briefing
    assert "Plan-only 合成命令" in briefing
    assert 'zf kanban assign "$task_id" dev' not in briefing
    assert "不要 assign dev/review/test/judge" in briefing


def test_full_briefing_no_hardcoded_four_stage_text(tmp_path):
    """The full briefing should NOT contain the old hardcoded
    'dev → review → test → judge 四阶段' string — that was the bug
    P2-2 fixes."""
    from zf.runtime.orchestrator_briefing import build_orchestrator_briefing

    # Set up a minimal state dir
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").touch()

    config = _load("code-assist.yaml")
    trigger = ZfEvent(type="user.message", actor="human", payload={"text": "hi"})

    briefing = build_orchestrator_briefing(
        state_dir=state_dir, config=config, trigger_event=trigger
    )

    # The exact old hardcoded string must be gone
    assert "dev → review → test → judge 四阶段" not in briefing
    # But the dynamic workflow should still be there
    assert "流程阶段协议" in briefing
    # For code-assist (no judge), judge.passed must not be the terminal
    assert "judge.passed" not in briefing or "test.passed" in briefing
