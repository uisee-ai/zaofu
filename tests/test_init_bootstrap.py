"""TR-BOOTSTRAP-FEATURE-001 (doc 42 §2.9) — integration tests for
`zf init` auto-installing F-zaofu-bootstrap."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from zf.cli import init as init_cli
from zf.core.bootstrap import BOOTSTRAP_FEATURE_ID, BOOTSTRAP_TASKS


def _run_init(
    project_dir: Path,
    *,
    force: bool = False,
    with_bootstrap: bool = True,
    preset: str | None = "minimal",
) -> int:
    """Run `zf init` against project_dir as cwd.

    Default ``with_bootstrap=True`` here so the test class
    ``TestDefaultInitInstallsBootstrap`` continues to exercise the
    install path; the CLI default is the opposite (off) for fixture
    compatibility.
    """
    old_cwd = os.getcwd()
    os.chdir(project_dir)
    try:
        args = argparse.Namespace(
            force=force,
            state_dir=None,
            preset=preset,
            with_bootstrap=with_bootstrap,
        )
        return init_cli.run(args)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Default zf init (with bootstrap)
# ---------------------------------------------------------------------------


class TestDefaultInitInstallsBootstrap:
    def test_init_creates_feature_list_with_bootstrap(self, tmp_path):
        rc = _run_init(tmp_path)
        assert rc == 0
        feature_list = tmp_path / ".zf" / "feature_list.json"
        assert feature_list.exists()
        data = json.loads(feature_list.read_text(encoding="utf-8"))
        ids = [f["id"] for f in data]
        assert BOOTSTRAP_FEATURE_ID in ids

    def test_init_creates_4_bootstrap_tasks(self, tmp_path):
        _run_init(tmp_path)
        kanban = tmp_path / ".zf" / "kanban.json"
        assert kanban.exists()
        data = json.loads(kanban.read_text(encoding="utf-8"))
        task_ids = [t["id"] for t in data]
        for tmpl in BOOTSTRAP_TASKS:
            assert tmpl["id"] in task_ids

    def test_init_writes_bootstrap_md(self, tmp_path):
        _run_init(tmp_path)
        bootstrap_md = tmp_path / ".zf" / "bootstrap.md"
        assert bootstrap_md.exists()
        assert "T-zfb-01" in bootstrap_md.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# --skip-bootstrap
# ---------------------------------------------------------------------------


class TestSkipBootstrapFlag:
    def test_skip_bootstrap_no_feature_list(self, tmp_path):
        rc = _run_init(tmp_path, with_bootstrap=False)
        assert rc == 0
        # feature_list.json should not exist (no feature created)
        feature_list = tmp_path / ".zf" / "feature_list.json"
        assert not feature_list.exists()

    def test_skip_bootstrap_kanban_empty(self, tmp_path):
        _run_init(tmp_path, with_bootstrap=False)
        kanban = tmp_path / ".zf" / "kanban.json"
        assert kanban.exists()
        data = json.loads(kanban.read_text(encoding="utf-8"))
        assert data == []

    def test_skip_bootstrap_no_bootstrap_md(self, tmp_path):
        _run_init(tmp_path, with_bootstrap=False)
        assert not (tmp_path / ".zf" / "bootstrap.md").exists()


# ---------------------------------------------------------------------------
# Idempotence via --force re-init
# ---------------------------------------------------------------------------


class TestForceReinit:
    def test_force_reinit_does_not_duplicate_bootstrap(self, tmp_path):
        _run_init(tmp_path)
        # Second init with --force (re-initialize) — bootstrap is already
        # present from first run; install_bootstrap_feature is idempotent
        # so it should not duplicate. But note: --force truncates
        # events.jsonl and recreates .zf/ — let's verify behavior.
        rc = _run_init(tmp_path, force=True)
        assert rc == 0
        feature_list = tmp_path / ".zf" / "feature_list.json"
        data = json.loads(feature_list.read_text(encoding="utf-8"))
        bootstrap_count = sum(
            1 for f in data if f["id"] == BOOTSTRAP_FEATURE_ID
        )
        assert bootstrap_count == 1, "bootstrap feature duplicated after re-init"


# ---------------------------------------------------------------------------
# Regression: existing tests/test_init* path
# ---------------------------------------------------------------------------


class TestRegression:
    def test_init_still_creates_baseline_state_dir(self, tmp_path):
        """Existing zf init contract preserved: .zf/{memory, logs, events.jsonl,
        session.yaml, kanban.json} all created."""
        _run_init(tmp_path)
        sd = tmp_path / ".zf"
        assert (sd / "memory").is_dir()
        assert (sd / "logs").is_dir()
        assert (sd / "events.jsonl").exists()
        assert (sd / "session.yaml").exists()
        assert (sd / "kanban.json").exists()
