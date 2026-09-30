"""CLI-01: real parser entrypoints, deliberately no business execution."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from tests.test_manual_docs import _load_manual_docs


cli = importlib.import_module("zf.cli.main")
manual = _load_manual_docs()
COMMANDS = ((), *(path[1:] for path, _ in manual._walk_commands(cli.build_parser())))


@pytest.mark.parametrize("command", COMMANDS, ids=lambda path: " ".join(("zf", *path)))
@pytest.mark.parametrize("mode", ("help", "invalid"))
def test_registered_cli_path_parses_without_business_or_state_effects(
    command: tuple[str, ...], mode: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Help and syntax errors must work even before configuration is usable.
    (tmp_path / "zf.yaml").write_text("project: [unfinished\n", encoding="utf-8")
    state = tmp_path / "custom-state"
    state.mkdir()
    (state / "sentinel").write_bytes(b"parser must not touch existing state\n")
    monkeypatch.chdir(tmp_path)
    for name in ("ZF_PROJECT_ROOT", "ZF_STATE_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace"))

    def snapshot() -> dict[str, bytes | None]:
        return {
            path.relative_to(tmp_path).as_posix(): path.read_bytes() if path.is_file() else None
            for path in tmp_path.rglob("*")
        }

    before = snapshot()
    calls: list[object] = []

    def forbidden_handler(args: object) -> int:
        calls.append(args)
        raise AssertionError("parser-only request reached a business handler")

    original_build = cli.build_parser

    def guarded_parser():
        parser = original_build()

        def guard(node) -> None:
            node.set_defaults(func=forbidden_handler)
            for _, child, _ in manual._command_children(node):
                guard(child)

        guard(parser)
        return parser

    monkeypatch.setattr(cli, "build_parser", guarded_parser)
    option = "--help" if mode == "help" else "--testkit-invalid-option-8f97"
    with pytest.raises(SystemExit) as stopped:
        cli.main([*command, option])
    output = capsys.readouterr()

    assert stopped.value.code == (0 if mode == "help" else 2)
    if mode == "help":
        assert "usage: zf" in output.out
        assert output.err == ""
    else:
        # Some commands first reject missing required arguments; both are
        # syntax errors. Do not claim every path tested unknown-option wording.
        assert "usage: zf" in output.err and "error:" in output.err
        assert output.out == ""
    assert calls == []
    assert snapshot() == before
