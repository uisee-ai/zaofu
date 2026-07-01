"""Tests for Feature schema + FeatureStore (E0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore


def test_create_and_list(tmp_path: Path):
    store = FeatureStore(tmp_path / "feature_list.json")
    f = store.add(Feature(title="OAuth login", user_message="implement OAuth login"))
    features = store.list_all()
    assert len(features) == 1
    assert features[0].id == f.id
    assert features[0].title == "OAuth login"


def test_id_auto_assigned(tmp_path: Path):
    store = FeatureStore(tmp_path / "feature_list.json")
    f1 = store.add(Feature(title="A"))
    f2 = store.add(Feature(title="B"))
    assert f1.id != f2.id
    assert f1.id.startswith("F-")
    assert f2.id.startswith("F-")


def test_default_status_is_planning(tmp_path: Path):
    store = FeatureStore(tmp_path / "feature_list.json")
    f = store.add(Feature(title="X"))
    assert f.status == "planning"


def test_default_priority_is_3(tmp_path: Path):
    store = FeatureStore(tmp_path / "feature_list.json")
    f = store.add(Feature(title="X"))
    assert f.priority == 3


def test_get_by_id(tmp_path: Path):
    store = FeatureStore(tmp_path / "feature_list.json")
    f = store.add(Feature(title="A"))
    fetched = store.get(f.id)
    assert fetched is not None
    assert fetched.title == "A"


def test_get_missing_returns_none(tmp_path: Path):
    store = FeatureStore(tmp_path / "feature_list.json")
    assert store.get("F-doesnt-exist") is None


def test_update_status(tmp_path: Path):
    store = FeatureStore(tmp_path / "feature_list.json")
    f = store.add(Feature(title="A"))
    store.update(f.id, status="active")
    assert store.get(f.id).status == "active"


def test_update_completed_at_set_when_done(tmp_path: Path):
    store = FeatureStore(tmp_path / "feature_list.json")
    f = store.add(Feature(title="A"))
    assert f.completed_at == ""
    store.update(f.id, status="done")
    fetched = store.get(f.id)
    assert fetched.status == "done"
    assert fetched.completed_at  # populated automatically


def test_filter_by_status(tmp_path: Path):
    store = FeatureStore(tmp_path / "feature_list.json")
    store.add(Feature(title="a", status="planning"))
    store.add(Feature(title="b", status="active"))
    store.add(Feature(title="c", status="active"))
    assert len(store.filter(status="active")) == 2
    assert len(store.filter(status="planning")) == 1


def test_persists_across_instances(tmp_path: Path):
    path = tmp_path / "feature_list.json"
    store1 = FeatureStore(path)
    store1.add(Feature(title="A"))
    store2 = FeatureStore(path)
    assert len(store2.list_all()) == 1


def test_save_is_atomic(tmp_path: Path, monkeypatch):
    """Atomic write: simulated mid-write crash leaves file intact."""
    store = FeatureStore(tmp_path / "feature_list.json")
    store.add(Feature(title="A"))
    snapshot_before = (tmp_path / "feature_list.json").read_text()

    def fail_replace(src, dst):
        raise OSError("simulated crash")

    monkeypatch.setattr("os.replace", fail_replace)
    with pytest.raises(OSError):
        store.add(Feature(title="B"))
    monkeypatch.undo()
    snapshot_after = (tmp_path / "feature_list.json").read_text()
    assert snapshot_after == snapshot_before


def test_user_message_preserved(tmp_path: Path):
    store = FeatureStore(tmp_path / "feature_list.json")
    f = store.add(Feature(
        title="OAuth login",
        user_message="please add OAuth so users can sign in with Google",
        priority=5,
    ))
    fetched = store.get(f.id)
    assert "Google" in fetched.user_message
    assert fetched.priority == 5


def test_invalid_status_rejected(tmp_path: Path):
    store = FeatureStore(tmp_path / "feature_list.json")
    f = store.add(Feature(title="A"))
    with pytest.raises(ValueError):
        store.update(f.id, status="bogus")
