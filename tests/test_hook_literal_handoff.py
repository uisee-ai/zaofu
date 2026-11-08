"""Literal handoff contents must not be mistaken for shell write targets."""

import pytest

from tests.test_hook_recv_codex import _invoke
from tests.test_hook_task_workspace import assignment  # noqa: F401


def check(assignment, monkeypatch, command, backend="codex", cwd=None):
    state, _, workspace, _, _, _ = assignment
    return _invoke(
        state,
        event=f"{'codex' if backend == 'codex' else 'claude'}.hook.pre_tool_use",
        backend=backend,
        payload={
            "session_id": "8ec85a68-8d54-4eee-9101-b0669f3ea098",
            "cwd": str(cwd or workspace),
            "tool_name": "Bash",
            "tool_input": {"command": command},
        },
        monkeypatch=monkeypatch,
    )


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
@pytest.mark.parametrize("quote", ["'", '"'])
@pytest.mark.parametrize("runtime_example", [False, True])
def test_literal_evidence_body_passes_real_hook(assignment, monkeypatch, backend, quote, runtime_example):
    state, _, _, _, _, _ = assignment
    example = f"`Path.write_text` and $(touch {state}/kanban.json) are inert examples.\n" if runtime_example else ""
    command = (
        f"cat > src/handoff.md <<{quote}EOF{quote}\n"
        f"Evidence: {state.parent}/docs/records/probe.json\n"
        f"{example}EOF\n"
    )
    assert check(assignment, monkeypatch, command, backend) == 0


def test_append_to_quoted_literal_target(assignment, monkeypatch):
    state, _, _, _, _, _ = assignment
    command = f"cat >> 'src/handoff notes.md' <<'EOF'\n{state.parent}/evidence.json\nEOF"
    assert check(assignment, monkeypatch, command) == 0


@pytest.mark.parametrize("kind", ["root", "scope", "traversal", "symlink", "cwd"])
def test_actual_literal_write_target_remains_guarded(assignment, monkeypatch, kind):
    state, _, workspace, _, _, _ = assignment
    target, cwd = "src/handoff.md", workspace
    if kind == "root":
        target = str(state.parent / "outside.md")
    elif kind == "scope":
        target = "outside.md"
    elif kind == "traversal":
        target = "../../g0/project/src/handoff.md"
    elif kind == "symlink":
        (workspace / "src").symlink_to(state.parent, target_is_directory=True)
    else:
        cwd = state.parent
    assert check(assignment, monkeypatch, f"cat > {target} <<'EOF'\ndata\nEOF\n", cwd=cwd) == 2


@pytest.mark.parametrize("kind", ["unquoted", "tail", "early_end", "missing_end", "interpreter", "substitution"])
def test_executable_or_ambiguous_shell_stays_conservative(assignment, monkeypatch, kind):
    state, _, _, _, _, _ = assignment
    outside = state.parent / "outside.md"
    commands = {
        "unquoted": f"cat > src/handoff.md <<EOF\n$(touch {outside})\nEOF\n",
        "tail": f"cat > src/handoff.md <<'EOF'\ndata\nEOF\nprintf bad > {outside}\n",
        "early_end": f"cat > src/handoff.md <<'EOF'\nEOF\nprintf bad > {outside}\nEOF\n",
        "missing_end": f"cat > src/handoff.md <<'EOF'\n{outside}\n",
        "interpreter": f"bash <<'EOF'\nprintf bad > {outside}\nEOF\n",
        "substitution": f"cat > \"$(touch {outside})\" <<'EOF'\ndata\nEOF\n",
    }
    assert check(assignment, monkeypatch, commands[kind]) == 2
