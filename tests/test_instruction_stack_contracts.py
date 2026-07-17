"""Cross-provider instruction-stack drift guards."""

from pathlib import Path


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
    assert "uv run pytest -q --no-cov" in text
    assert "pytest -n" not in text
    assert "runtime state: `.zf/`" not in text


def test_path_rules_do_not_restore_retired_operations() -> None:
    code = _read(".claude/rules/code.md")
    backlogs = _read(".claude/rules/backlogs.md")
    docs = _read(".claude/rules/docs.md")

    assert "caller-level test" in code
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
