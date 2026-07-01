"""TR-AGENTS-MD-MANAGED-001 (doc 42 §2.5 A) — CLI tests for
`zf update agents-md` subcommand."""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import pytest

from zf.cli.update import run_agents_md, update_agents_md
from zf.core.agents_md import (
    ZF_MARKER_END,
    ZF_MARKER_START,
    extract_managed_block,
    render_canonical_block,
)


def _make_args(path: Path, *, write: bool = False, check: bool = False) -> argparse.Namespace:
    return argparse.Namespace(path=path, write=write, check=check)


# ---------------------------------------------------------------------------
# --write
# ---------------------------------------------------------------------------


class TestWriteInsertsBlock:
    def test_creates_block_in_existing_agents_md(self, tmp_path):
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            "# AGENTS.md\n\n## Working Style\n- be careful\n",
            encoding="utf-8",
        )

        rc = run_agents_md(_make_args(agents_md, write=True))
        assert rc == 0

        updated = agents_md.read_text(encoding="utf-8")
        assert ZF_MARKER_START in updated
        assert ZF_MARKER_END in updated
        inside = extract_managed_block(updated)
        assert inside == render_canonical_block().rstrip("\n")

    def test_preserves_existing_user_content(self, tmp_path):
        agents_md = tmp_path / "AGENTS.md"
        user_content = (
            "# AGENTS.md\n"
            "\n"
            "## Working Style\n"
            "- 谨慎\n"
            "- 凡是测试都要绿\n"
            "\n"
            "## Testing\n"
            "- pytest\n"
        )
        agents_md.write_text(user_content, encoding="utf-8")
        run_agents_md(_make_args(agents_md, write=True))
        updated = agents_md.read_text(encoding="utf-8")
        # All original lines must still appear
        for line in user_content.splitlines():
            if line.strip():
                assert line in updated, f"user line lost: {line!r}"

    def test_creates_file_when_missing(self, tmp_path):
        agents_md = tmp_path / "AGENTS.md"
        assert not agents_md.exists()

        rc = run_agents_md(_make_args(agents_md, write=True))
        assert rc == 0
        assert agents_md.exists()
        assert extract_managed_block(
            agents_md.read_text(encoding="utf-8")
        ) is not None

    def test_idempotent_write(self, tmp_path):
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Header\n", encoding="utf-8")
        run_agents_md(_make_args(agents_md, write=True))
        first = agents_md.read_text(encoding="utf-8")
        run_agents_md(_make_args(agents_md, write=True))
        second = agents_md.read_text(encoding="utf-8")
        assert first == second


# ---------------------------------------------------------------------------
# Dry-run (default)
# ---------------------------------------------------------------------------


class TestDryRunPrintsDiff:
    def test_dry_run_no_write(self, tmp_path, capsys):
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# AGENTS.md\n", encoding="utf-8")
        original = agents_md.read_text(encoding="utf-8")

        rc = run_agents_md(_make_args(agents_md, write=False))
        assert rc == 0
        # File NOT modified
        assert agents_md.read_text(encoding="utf-8") == original
        # Diff printed
        captured = capsys.readouterr()
        assert "+++" in captured.out
        assert "Active task pin" in captured.out  # part of canonical block
        assert "dry-run" in captured.out

    def test_dry_run_up_to_date_prints_no_diff(self, tmp_path, capsys):
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Header\n", encoding="utf-8")
        run_agents_md(_make_args(agents_md, write=True))  # write first
        capsys.readouterr()  # discard write output

        rc = run_agents_md(_make_args(agents_md, write=False))
        assert rc == 0
        captured = capsys.readouterr()
        assert "already up to date" in captured.out


# ---------------------------------------------------------------------------
# --check (CI exit code)
# ---------------------------------------------------------------------------


class TestCheckExitCode:
    def test_check_returns_1_when_out_of_sync(self, tmp_path):
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# AGENTS.md\n", encoding="utf-8")  # no block

        rc = run_agents_md(_make_args(agents_md, check=True))
        assert rc == 1

    def test_check_returns_0_when_in_sync(self, tmp_path):
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# AGENTS.md\n", encoding="utf-8")
        run_agents_md(_make_args(agents_md, write=True))  # sync first

        rc = run_agents_md(_make_args(agents_md, check=True))
        assert rc == 0

    def test_check_returns_1_when_file_missing(self, tmp_path):
        agents_md = tmp_path / "AGENTS.md"
        assert not agents_md.exists()
        rc = run_agents_md(_make_args(agents_md, check=True))
        assert rc == 1


# ---------------------------------------------------------------------------
# Library entrypoint
# ---------------------------------------------------------------------------


class TestLibraryEntrypoint:
    def test_update_agents_md_helper(self, tmp_path):
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# X\n", encoding="utf-8")
        rc = update_agents_md(agents_md, write=True)
        assert rc == 0
        assert extract_managed_block(
            agents_md.read_text(encoding="utf-8")
        ) is not None


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


class TestMalformedInput:
    def test_duplicate_start_marker_errors(self, tmp_path, capsys):
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            f"{ZF_MARKER_START}\nA\n{ZF_MARKER_START}\nB\n{ZF_MARKER_END}\n",
            encoding="utf-8",
        )
        rc = run_agents_md(_make_args(agents_md, write=False))
        assert rc == 1
        captured = capsys.readouterr()
        assert "Error parsing" in captured.err
