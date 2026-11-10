"""Active shell substitution is executable even inside double quotes."""

import pytest

from tests.test_hook_literal_handoff import check
from tests.test_hook_task_workspace import assignment  # noqa: F401


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
@pytest.mark.parametrize("template,expected", [
    ('echo "$(cp {source} {target})"', 2),
    ('printf \'%s\' "$(cp {source} {target})"', 2),
    ('echo $(cp {source} {target})', 2),
    ("echo '$(cp {source} {target})'", 0),
    ("printf '%s' '$(cp {source} {target})'", 0),
    ('echo "\\$(cp {source} {target})"', 0),
    ('echo "`cp {source} {target}`"', 2),
    ('echo "`printf \'%s\' cp`"', 0),
    ('echo "\\`cp {source} {target}\\`"', 0),
    ('echo "prefix $(cp {source} {target}) suffix"', 2),
    ('echo "$(echo "$(cp {source} {target})")"', 2),
    ('echo "$((1 + $(cp {source} {target})))"', 2),
    ('echo "$((cp))"', 0),
    ('echo "$((cp+1))"', 0),
    ('echo $((cp))', 0),
    ('echo "cp $(printf \'%s\' value)"', 0),
    ('echo "$(printf \'%s\' cp)"', 0),
    ('echo "$(printf \'%s\' \'cp {source} {target}\')"', 0),
    ('echo "$(printf \'%s\' \')\'; cp {source} {target})"', 2),
    ('python -c "print(\'$(cp {source} {target})\')"', 2),
    ('echo "\\\\$(cp {source} {target})"', 2),
    ('echo "$(printf ready # )\ncp {source} {target}\n)"', 2),
    ('echo "$(printf ready # cp {source} {target}\n)"', 0),
    ('echo "$(printf \'%s\' foo#bar; cp {source} {target})"', 2),
    ('echo "$(printf \'%s\' \'# )\'; cp {source} {target})"', 2),
    ('echo "$(printf \'%s\' "# )"; cp {source} {target})"', 2),
    ('echo "$(printf \'%s\' \\#; cp {source} {target})"', 2),
    ('echo "$(printf \'%s\' x\\ #; cp {source} {target})"', 2),
    ('echo "$((16#ff + cp))"', 0),
])
def test_copy_substitution_respects_activity_quotes_and_data(assignment, monkeypatch, backend, template, expected):
    state, _, workspace, log, *_ = assignment
    (workspace / "src").mkdir()
    source = workspace / "src/in.txt"
    source.write_text("unchanged source")
    target = state.parent / "outside.txt"
    command = template.format(source=source, target=target)
    assert check(assignment, monkeypatch, command, backend) == expected
    assert source.read_text() == "unchanged source"
    assert not target.exists()
    if expected == 2:
        assert any(e.type == "worker.scope_write.rejected" for e in log.read_all())


@pytest.mark.parametrize("kind", ["nesting", "contexts"])
def test_substitution_analysis_limit_never_exempts_hidden_copy(assignment, monkeypatch, kind):
    from zf.cli.hook_shell_substitution import MAX_SUBSTITUTION_CONTEXTS, MAX_SUBSTITUTION_NESTING

    state, _, workspace, *_ = assignment
    (workspace / "src").mkdir()
    source = workspace / "src/in.txt"
    source.write_text("unchanged")
    target = state.parent / "outside.txt"
    body = f"cp {source} {target}"
    if kind == "nesting":
        for _ in range(MAX_SUBSTITUTION_NESTING + 1):
            body = "$(" + body + ")"
    else:
        body = "$(" + body + ") " + "$(printf value) " * MAX_SUBSTITUTION_CONTEXTS
    assert check(assignment, monkeypatch, f'echo "{body}"') == 2
    assert not target.exists()
