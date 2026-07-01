"""Tests for G-RECYCLE-1: BackendSessionReader + Claude/Codex implementations.

Uses pre-captured fixture files (tests/fixtures/{claude,codex}_session_sample.jsonl)
to exercise the parsing logic without requiring live CLI runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.runtime.backend_session_reader import (
    BackendSessionReader,
    ClaudeSessionReader,
    CodexSessionReader,
    UsageReport,
    get_reader_for_backend,
)


FIXTURES = Path(__file__).parent / "fixtures"
CLAUDE_SAMPLE = FIXTURES / "claude_session_sample.jsonl"
CODEX_SAMPLE = FIXTURES / "codex_session_sample.jsonl"


class TestClaudeReader:
    def test_parses_latest_assistant_usage_from_real_sample(self):
        reader = ClaudeSessionReader()
        report = reader.read_latest_usage(
            CLAUDE_SAMPLE, fallback_window=200_000,
        )
        assert report is not None
        # Last assistant usage: input=5, cache_read=5050, cache_creation=200
        assert report.effective_input_tokens == 5 + 5050 + 200
        assert report.output_tokens == 30
        assert report.model_context_window == 200_000
        expected_ratio = (5 + 5050 + 200) / 200_000
        assert abs(report.ratio - expected_ratio) < 1e-9

    def test_uses_fallback_window_when_provided(self):
        reader = ClaudeSessionReader()
        report = reader.read_latest_usage(
            CLAUDE_SAMPLE, fallback_window=1_000_000,
        )
        assert report.model_context_window == 1_000_000
        expected = (5 + 5050 + 200) / 1_000_000
        assert abs(report.ratio - expected) < 1e-9

    def test_returns_none_for_missing_file(self, tmp_path):
        reader = ClaudeSessionReader()
        report = reader.read_latest_usage(
            tmp_path / "ghost.jsonl", fallback_window=200_000,
        )
        assert report is None

    def test_handles_tail_partial_last_line(self):
        """The fixture has a malformed last line — reader must not
        crash; it should skip the bad line and return the valid
        previous usage."""
        reader = ClaudeSessionReader()
        report = reader.read_latest_usage(
            CLAUDE_SAMPLE, fallback_window=200_000,
        )
        assert report is not None  # last valid assistant usage still readable

    def test_session_path_constructs_cwd_escaped_name(self, tmp_path):
        reader = ClaudeSessionReader(projects_root=tmp_path / "projects")
        uuid_str = "44444444-4444-4444-4444-444444444444"
        # No file exists yet
        path = reader.session_path(
            "/path/to/zaofu", uuid_str,
        )
        assert path is None or path.name == f"{uuid_str}.jsonl"

    def test_session_path_uses_cached_path_when_provided(self, tmp_path):
        reader = ClaudeSessionReader()
        cached = tmp_path / "cached" / "abc.jsonl"
        cached.parent.mkdir()
        cached.write_text("{}")
        uuid_str = "44444444-4444-4444-4444-444444444444"
        path = reader.session_path("/any", uuid_str, cached_path=cached)
        assert path == cached

    def test_session_path_escapes_dotted_worktree_workdir(self, tmp_path):
        """GUARD (B-COST-02): a per-task worktree workdir contains a
        leading-dot dir (e.g. ``.zf-cj-min-refactor``). Claude escapes
        BOTH ``/`` AND ``.`` to ``-`` when naming the project dir. If a
        regression drops the ``.``→``-`` step, the escaped name no longer
        matches Claude's on-disk dir → the reader silently finds nothing
        → claude-code roles report ZERO usage to the cost tracker (a
        false-cheap, not a crash). Pin the exact transformation."""
        projects = tmp_path / "projects"
        reader = ClaudeSessionReader(projects_root=projects)
        workdir = "/path/to/hermes-agent/.zf-cj-min-refactor/proj"
        uuid_str = "44444444-4444-4444-4444-444444444444"
        # Claude's on-disk dir: cwd with "/" AND "." replaced by "-",
        # leading "-" for the leading slash. The "/." boundary collapses
        # to "--" (slash→- then dot→-).
        expected_dir = "-home-min-workspace-hermes-agent--zf-cj-min-refactor-proj"
        disk = projects / expected_dir / f"{uuid_str}.jsonl"
        disk.parent.mkdir(parents=True)
        disk.write_text("{}")

        path = reader.session_path(workdir, uuid_str)

        assert path is not None, "dotted worktree workdir → session not found (silent 0 usage)"
        assert path == disk
        assert path.parent.name == expected_dir

    def test_dotted_worktree_workdir_usage_is_captured_end_to_end(self, tmp_path):
        """GUARD (B-COST-02): the path-escape regression manifests as
        cost undercount, so prove the whole chain (escape → locate →
        read usage) survives a dotted worktree workdir, not just the
        string transform."""
        projects = tmp_path / "projects"
        reader = ClaudeSessionReader(projects_root=projects)
        workdir = "/srv/run/.zf-worktree-A/repo"
        uuid_str = "55555555-5555-5555-5555-555555555555"
        escaped = "-srv-run--zf-worktree-A-repo"
        disk = projects / escaped / f"{uuid_str}.jsonl"
        disk.parent.mkdir(parents=True)
        disk.write_text(
            '{"type":"assistant","timestamp":"2026-06-17T00:00:00Z",'
            '"message":{"model":"claude-opus-4-8","usage":'
            '{"input_tokens":7,"cache_read_input_tokens":1000,'
            '"cache_creation_input_tokens":300,"output_tokens":42}}}\n'
        )

        path = reader.session_path(workdir, uuid_str)
        assert path is not None
        report = reader.read_latest_usage(path, fallback_window=200_000)

        assert report is not None
        assert report.effective_input_tokens == 7 + 1000 + 300
        assert report.output_tokens == 42
        assert report.model == "claude-opus-4-8"

    def test_session_path_glob_fallback_recovers_on_escape_drift(self, tmp_path):
        """B-COST-02 step3: if the escaped-dir derivation ever misses (the
        on-disk dir differs from what we derive), a uuid glob must still
        recover the session so cost capture self-heals instead of going to
        zero. Simulate drift by placing the file under a DIFFERENT dir than
        the cwd derivation would produce."""
        projects = tmp_path / "projects"
        reader = ClaudeSessionReader(projects_root=projects)
        uuid_str = "66666666-6666-6666-6666-666666666666"
        # File lives under an unrelated/wrong project dir name — the cwd
        # passed below would NOT derive this dir, so only the glob can find it.
        disk = projects / "-some-other-derivation" / f"{uuid_str}.jsonl"
        disk.parent.mkdir(parents=True)
        disk.write_text("{}")

        path = reader.session_path("/cwd/that/derives/elsewhere", uuid_str)

        assert path == disk  # uuid glob recovered it

    def test_session_path_glob_fallback_needs_uuid(self, tmp_path):
        """Empty session_id (early boot, uuid not yet cached) must NOT glob —
        it's the legitimate not-found-yet window, returns None."""
        projects = tmp_path / "projects"
        (projects / "-x").mkdir(parents=True)
        (projects / "-x" / "stray.jsonl").write_text("{}")
        reader = ClaudeSessionReader(projects_root=projects)
        assert reader.session_path("/any", "") is None


class TestCodexReader:
    def test_parses_latest_token_count_from_real_sample(self):
        reader = CodexSessionReader()
        report = reader.read_latest_usage(CODEX_SAMPLE)
        assert report is not None
        # Latest token_count.info.last_token_usage.input_tokens = 33000
        assert report.effective_input_tokens == 33000
        assert report.output_tokens == 400
        # Codex self-reports window = 258400
        assert report.model_context_window == 258400
        expected_ratio = 33000 / 258400
        assert abs(report.ratio - expected_ratio) < 1e-9

    def test_skips_token_count_with_null_info(self):
        """First token_count row has info=null; reader must skip it
        and pick a later populated one."""
        reader = CodexSessionReader()
        report = reader.read_latest_usage(CODEX_SAMPLE)
        assert report is not None
        # Shouldn't be from the early null row
        assert report.effective_input_tokens > 0

    def test_returns_none_for_missing_file(self, tmp_path):
        reader = CodexSessionReader()
        assert reader.read_latest_usage(tmp_path / "ghost.jsonl") is None

    def test_session_path_uses_cached_path(self, tmp_path):
        reader = CodexSessionReader()
        cached = tmp_path / "cached" / "rollout.jsonl"
        cached.parent.mkdir()
        cached.write_text("{}")
        path = reader.session_path("/any", "some-uuid", cached_path=cached)
        assert path == cached

    def test_session_path_globs_by_uuid(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "zf.runtime.backend_session_reader.CODEX_SESSIONS_ROOT",
            tmp_path / "sessions",
        )
        target_uuid = "55555555-5555-5555-5555-555555555555"
        folder = tmp_path / "sessions" / "2026" / "04" / "15"
        folder.mkdir(parents=True)
        path = folder / f"rollout-2026-04-15T10-00-00-{target_uuid}.jsonl"
        path.write_text("{}")

        reader = CodexSessionReader()
        found = reader.session_path("/any", target_uuid)
        assert found == path


class TestGetReaderForBackend:
    def test_claude_code_returns_claude_reader(self):
        r = get_reader_for_backend("claude-code")
        assert isinstance(r, ClaudeSessionReader)

    def test_codex_returns_codex_reader(self):
        r = get_reader_for_backend("codex")
        assert isinstance(r, CodexSessionReader)

    def test_mock_returns_none(self):
        assert get_reader_for_backend("mock") is None

    def test_python_returns_none(self):
        assert get_reader_for_backend("python") is None

    def test_unknown_returns_none(self):
        assert get_reader_for_backend("frobnicator") is None


class TestUsageReport:
    def test_is_immutable_dataclass(self):
        report = UsageReport(
            effective_input_tokens=1000,
            output_tokens=100,
            model_context_window=200_000,
            ratio=0.005,
            timestamp="2026-04-15T10:00:00Z",
            raw={},
        )
        assert report.effective_input_tokens == 1000
        assert report.ratio == 0.005
