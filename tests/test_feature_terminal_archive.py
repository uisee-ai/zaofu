"""Tests for feature_list.json terminal-state archival (G-FEAT-1)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class TestFeatureTerminalArchive:
    def test_done_feature_moves_to_archive(self, tmp_path: Path):
        store = FeatureStore(tmp_path / "feature_list.json")
        store.add(Feature(id="F1", title="x", status="active"))
        store.update("F1", status="done")

        active_text = (tmp_path / "feature_list.json").read_text()
        assert "F1" not in active_text

        archive = tmp_path / "feature_list" / f"{_today()}.json"
        assert archive.exists()
        archived = json.loads(archive.read_text())
        assert any(f["id"] == "F1" for f in archived)

    def test_cancelled_feature_moves_to_archive(self, tmp_path: Path):
        store = FeatureStore(tmp_path / "feature_list.json")
        store.add(Feature(id="F1", title="x", status="planning"))
        store.update("F1", status="cancelled")

        archive = tmp_path / "feature_list" / f"{_today()}.json"
        assert archive.exists()

    def test_non_terminal_updates_stay_active(self, tmp_path: Path):
        store = FeatureStore(tmp_path / "feature_list.json")
        store.add(Feature(id="F1", title="x", status="planning"))
        store.update("F1", status="active")

        active = json.loads((tmp_path / "feature_list.json").read_text())
        assert any(f["id"] == "F1" for f in active)
        assert not (tmp_path / "feature_list").exists()

    def test_list_all_excludes_archived(self, tmp_path: Path):
        store = FeatureStore(tmp_path / "feature_list.json")
        store.add(Feature(id="F1", title="done-feat", status="active"))
        store.add(Feature(id="F2", title="active-feat", status="active"))
        store.update("F1", status="done")

        features = store.list_all()
        ids = {f.id for f in features}
        assert ids == {"F2"}

    def test_list_all_with_archive_includes_both(self, tmp_path: Path):
        store = FeatureStore(tmp_path / "feature_list.json")
        store.add(Feature(id="F1", title="a", status="active"))
        store.add(Feature(id="F2", title="b", status="active"))
        store.update("F1", status="done")

        features = store.list_all_with_archive()
        ids = {f.id for f in features}
        assert ids == {"F1", "F2"}

    def test_get_finds_archived(self, tmp_path: Path):
        store = FeatureStore(tmp_path / "feature_list.json")
        store.add(Feature(id="F1", title="x", status="active"))
        store.update("F1", status="done")

        feat = store.get("F1")
        assert feat is not None
        assert feat.id == "F1"
        assert feat.status == "done"

    def test_done_sets_completed_at_even_when_archived(self, tmp_path: Path):
        store = FeatureStore(tmp_path / "feature_list.json")
        store.add(Feature(id="F1", title="x", status="active"))
        store.update("F1", status="done")

        feat = store.get("F1")
        assert feat.completed_at  # non-empty ISO timestamp

    def test_multiple_done_features_same_day_append(self, tmp_path: Path):
        store = FeatureStore(tmp_path / "feature_list.json")
        store.add(Feature(id="F1", title="a", status="active"))
        store.add(Feature(id="F2", title="b", status="active"))
        store.update("F1", status="done")
        store.update("F2", status="done")

        archive = tmp_path / "feature_list" / f"{_today()}.json"
        archived = json.loads(archive.read_text())
        ids = {f["id"] for f in archived}
        assert ids == {"F1", "F2"}

    def test_list_all_with_archive_last_days(self, tmp_path: Path):
        store = FeatureStore(tmp_path / "feature_list.json")
        store.add(Feature(id="F1", title="x", status="active"))
        store.update("F1", status="done")
        features = store.list_all_with_archive(last_days=1)
        ids = {f.id for f in features}
        assert "F1" in ids

    def test_filter_status_done_in_active_returns_empty(self, tmp_path: Path):
        """filter(status='done') on the active store is empty — done
        features always live in the archive. Use list_all_with_archive
        if you need them."""
        store = FeatureStore(tmp_path / "feature_list.json")
        store.add(Feature(id="F1", title="x", status="active"))
        store.update("F1", status="done")

        assert store.filter(status="done") == []
