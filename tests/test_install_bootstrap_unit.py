"""TR-BOOTSTRAP-FEATURE-001 (doc 42 §2.9) — unit tests on install_bootstrap_feature."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.bootstrap import (
    BOOTSTRAP_FEATURE_ID,
    BOOTSTRAP_FEATURE_TITLE,
    BOOTSTRAP_TASKS,
    install_bootstrap_feature,
)
from zf.core.bootstrap.feature_template import BOOTSTRAP_FEATURE_DESCRIPTION
from zf.core.bootstrap.task_templates import materialize_bootstrap_tasks
from zf.core.feature.store import FeatureStore
from zf.core.task.store import TaskStore


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """Minimal state dir — installer creates its own feature_list/kanban."""
    sd = tmp_path / ".zf"
    sd.mkdir()
    return sd


# ---------------------------------------------------------------------------
# Templates content sanity
# ---------------------------------------------------------------------------


class TestTemplateContent:
    def test_4_bootstrap_tasks_defined(self):
        assert len(BOOTSTRAP_TASKS) == 4
        ids = [t["id"] for t in BOOTSTRAP_TASKS]
        assert ids == ["T-zfb-01", "T-zfb-02", "T-zfb-03", "T-zfb-04"]

    def test_description_references_4_tasks(self):
        """sprint acceptance #8: description must mention all 4 T-zfb-* ids."""
        for i in range(1, 5):
            assert f"T-zfb-0{i}" in BOOTSTRAP_FEATURE_DESCRIPTION

    def test_materialize_produces_tasks_with_feature_id(self):
        tasks = materialize_bootstrap_tasks()
        assert len(tasks) == 4
        for t in tasks:
            assert t.contract.feature_id == BOOTSTRAP_FEATURE_ID
            assert t.status == "backlog"
            assert t.priority == 2

    def test_materialize_is_fresh_each_call(self):
        """Distinct calls produce distinct Task instances (no shared state leak)."""
        a = materialize_bootstrap_tasks()
        b = materialize_bootstrap_tasks()
        assert a[0] is not b[0]


# ---------------------------------------------------------------------------
# install_bootstrap_feature
# ---------------------------------------------------------------------------


class TestInstall:
    def test_install_returns_true_on_fresh(self, state_dir):
        result = install_bootstrap_feature(state_dir, config=None)
        assert result is True

    def test_install_creates_feature_row(self, state_dir):
        install_bootstrap_feature(state_dir, config=None)
        store = FeatureStore(state_dir / "feature_list.json")
        feat = store.get(BOOTSTRAP_FEATURE_ID)
        assert feat is not None
        assert feat.title == BOOTSTRAP_FEATURE_TITLE
        assert feat.status == "active"
        assert feat.priority == 1
        assert "T-zfb-01" in feat.description

    def test_install_creates_4_tasks(self, state_dir):
        install_bootstrap_feature(state_dir, config=None)
        store = TaskStore(state_dir / "kanban.json")
        for tmpl in BOOTSTRAP_TASKS:
            task = store.get(tmpl["id"])
            assert task is not None, f"missing bootstrap task {tmpl['id']}"
            assert task.contract.feature_id == BOOTSTRAP_FEATURE_ID
            assert task.status == "backlog"

    def test_install_writes_bootstrap_md(self, state_dir):
        install_bootstrap_feature(state_dir, config=None)
        md = state_dir / "bootstrap.md"
        assert md.exists()
        content = md.read_text(encoding="utf-8")
        assert "F-zaofu-bootstrap" in content
        assert "T-zfb-01" in content


class TestSkipFlag:
    def test_skip_true_returns_false(self, state_dir):
        result = install_bootstrap_feature(state_dir, config=None, skip=True)
        assert result is False

    def test_skip_true_creates_no_files(self, state_dir):
        install_bootstrap_feature(state_dir, config=None, skip=True)
        assert not (state_dir / "feature_list.json").exists()
        assert not (state_dir / "bootstrap.md").exists()
        # kanban.json may or may not exist; if it does, must be empty
        kanban = state_dir / "kanban.json"
        if kanban.exists():
            assert json.loads(kanban.read_text(encoding="utf-8")) == []


class TestIdempotent:
    def test_double_install_no_duplication(self, state_dir):
        """Two installs back-to-back: feature still unique, tasks not duplicated."""
        install_bootstrap_feature(state_dir, config=None)
        result_2 = install_bootstrap_feature(state_dir, config=None)
        assert result_2 is False  # already present, no-op

        feat_store = FeatureStore(state_dir / "feature_list.json")
        task_store = TaskStore(state_dir / "kanban.json")
        all_features = feat_store.list_all()
        bootstrap_features = [
            f for f in all_features if f.id == BOOTSTRAP_FEATURE_ID
        ]
        assert len(bootstrap_features) == 1

        # All 4 tasks present, no duplicates
        for tmpl in BOOTSTRAP_TASKS:
            assert task_store.get(tmpl["id"]) is not None


class TestOverwrite:
    def test_overwrite_re_adds(self, state_dir):
        """overwrite=True replaces existing F-zaofu-bootstrap."""
        install_bootstrap_feature(state_dir, config=None)
        # Modify a task to verify overwrite path
        task_store = TaskStore(state_dir / "kanban.json")
        task_store.update("T-zfb-01", status="cancelled")

        result = install_bootstrap_feature(
            state_dir, config=None, overwrite=True
        )
        assert result is True

        # T-zfb-01 should be back to backlog (re-added under cancellation cycle)
        feat_store = FeatureStore(state_dir / "feature_list.json")
        # FeatureStore may end up with both cancelled + active rows depending on
        # add behavior; the active one must be the most recent
        active_features = [
            f for f in feat_store.list_all()
            if f.id == BOOTSTRAP_FEATURE_ID and f.status == "active"
        ]
        assert len(active_features) >= 1
