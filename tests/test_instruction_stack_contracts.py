"""Cross-provider instruction-stack drift guards."""

from pathlib import Path

from zf.core.skills import read_skill_metadata

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_root_agents_routes_authority_and_scopes_worker_protocol() -> None:
    text = _read("AGENTS.md")

    assert "142-layered-runtime-authority-and-orchestration-modes.md" in text
    assert "Without that marker, do not" in text
    assert "Kernel `Orchestrator`" in text
    assert "configured `orchestrator` role agent" in text
    assert "Skills, workdirs, lockfiles" not in text
    assert "Web, Kanban, Feishu" not in text
    assert "kernel truth" not in text
    assert "truth files" not in text


def test_claude_commands_match_declared_fresh_environment() -> None:
    text = _read("CLAUDE.md")

    assert "uv sync --extra dev --extra web" in text
    assert "scripts/dev-verify.py plan --base dev" in text
    assert "scripts/dev-verify.py run --base dev" in text
    assert "uv run pytest -q --no-cov" in text
    assert "pytest -n" not in text
    assert "runtime state: `.zf/`" not in text


def test_path_rules_do_not_restore_retired_operations() -> None:
    code = _read(".claude/rules/code.md")
    backlogs = _read(".claude/rules/backlogs.md")
    docs = _read(".claude/rules/docs.md")

    assert "caller-level test" in code
    assert "scripts/dev-verify.py plan --base dev" in code
    assert "git mv backlogs/" not in backlogs
    assert "mv backlogs/" in backlogs
    assert "一旦立项,整个文件 `git mv`" not in backlogs
    assert "00..99" not in docs
    assert "2 位数字前缀" not in docs
    assert "docs/design/NN-" not in docs
    assert "canonical-current" in docs


def test_zf_cr_canonical_and_provider_copies_match() -> None:
    canonical = (ROOT / "skills/zf-cr/SKILL.md").read_bytes()
    codex = (ROOT / ".codex/skills/zf-cr/SKILL.md").read_bytes()
    claude = (ROOT / ".claude/skills/zf-cr/SKILL.md").read_bytes()

    assert canonical == codex == claude
    text = canonical.decode("utf-8")
    assert "142-layered-runtime-authority-and-orchestration-modes.md" in text
    assert "doc 44 is only a historical scoring snapshot" in text
    assert "skills, workdirs, lockfiles" not in text


def test_closeout_skill_provider_copies_match_canonical() -> None:
    for skill in (
        "zf-backlog-batch-closeout",
        "zf-harness-commit-push",
    ):
        canonical = (ROOT / "skills" / skill / "SKILL.md").read_bytes()
        claude = (
            ROOT / ".claude" / "skills" / skill / "SKILL.md"
        ).read_bytes()
        codex = (ROOT / ".codex" / "skills" / skill / "SKILL.md").read_bytes()

        assert canonical == claude == codex, skill


def test_plan_task_map_skill_requires_first_pass_mechanical_closure() -> None:
    text = _read("skills/zf-plan-task-map-contract/SKILL.md")

    assert "Mechanical Closure Before Output" in text
    assert "`verification_command_ids` must exactly equal" in text
    assert "same canonical command registry" in text
    assert "compare both directions" in text
    assert "briefing provides a local validator" in text
    assert "the final files" in text
    assert "Do not invent a" in text
    assert "validator command" in text
    assert "Default to exactly one Task Map producer" in text
    assert "Do not add a descriptor" in text
    assert "A `reference_only` row" in text
    assert "`acceptance_ids: []`" in text
    assert "`verification_read_paths` at the **task object level**" in text
    assert "Do not place" in text
    assert "`verification_read_paths` inside `validation`" in text
    assert "rewrite an" in text
    assert "owner-provided verification command" in text


def test_plan_critic_skill_defers_mechanical_port_closure_to_kernel() -> None:
    text = _read("skills/zf-yoke-critic-role-context/SKILL.md")

    assert "pre-submit validator" in text
    assert "Kernel-derived" in text
    assert "goal_claim_set" in text
    assert "planning_result" in text
    assert "Issue Flow 会把 `requirement_spec` 适配为" in text
    assert "`issue_spec`；" in text
    assert "不得要求 Planner 在 `plan_ports` 重复提交" in text
    assert "Plan readiness 与 execution evidence" in text
    assert "zf-browser-e2e-contract" in text
    assert "command registry 的定义 owner" in text
    assert "后层 candidate command 不得被解释为" in text
    assert "不得要求 Planner 删除 authority 明确给出的 command id" in text
    assert "command 定义所在 Task 的 wave 不是该 command 的执行时点" in text
    assert "不得因\n   定义 Task 早于某个 producer Task" in text
    assert "该 owner 是否在 candidate verify\n   前完成" in text
    assert "顶层 `verification_read_paths`" in text
    assert "只有路径无 owner、owner 重叠或依赖顺序不可满足时才 Reject" in text


def test_browser_planning_contract_keeps_future_evidence_out_of_blockers() -> None:
    browser = _read("skills/zf-browser-e2e-contract/SKILL.md")
    planner = _read("skills/zf-yoke-planner-role-context/SKILL.md")
    adapter = _read("skills/zf-project-adapter-matrix-enrichment/SKILL.md")
    synthesizer = _read("skills/zf-channel-discussion-synthesizer/SKILL.md")

    assert "Planning readiness is not execution success" in browser
    assert "future evidence has not been generated" in adapter
    assert "计划态不冒充执行态" in planner
    assert "实施前截图/trace 尚不存在不构成 readiness blocker" in synthesizer
    assert "canonical `acceptance_ids`" in synthesizer
    assert "瞬时状态只写入 `readiness`" in synthesizer
    assert "dependencies: [zf-browser-e2e-contract]" in adapter
    assert "zf-browser-e2e-contract" in read_skill_metadata(
        ROOT / "skills/zf-channel-discussion-synthesizer/SKILL.md",
        expected_name="zf-channel-discussion-synthesizer",
    ).dependencies
    assert "owner-facing origin" in browser
    assert "window.isSecureContext" in browser
    assert "crypto.subtle" in browser
    assert "`skills_required` is the Planner-owned" in _read(
        "skills/zf-plan-task-map-contract/SKILL.md"
    )
