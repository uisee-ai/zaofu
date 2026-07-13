"""FeatureStore — feature_list.json CRUD with terminal-state archival.

Layout:

    .zf/feature_list.json             ← active (planning | active) features
    .zf/feature_list/<YYYY-MM-DD>.json← features that reached done/cancelled that day

Mirrors ``TaskStore``'s archival model: terminal transitions move records
out of the active file on the same ``update()`` call. ``list_all`` returns
active only; use ``list_all_with_archive`` for historical views.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from zf.core.feature.schema import Feature, _VALID_STATUSES
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.state.rotation import list_archives


TERMINAL_STATES = {"done", "cancelled"}


class FeatureStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    # ---- layout helpers ----

    @property
    def _archive_dir(self) -> Path:
        return self.path.parent / self.path.stem

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _archive_file(self, date: str) -> Path:
        return self._archive_dir / f"{date}.json"

    def _locked(self):
        return locked_path(self.path)

    # ---- active file I/O ----

    def _load_raw(self) -> list[dict]:
        if not self.path.exists():
            return []
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        data = json.loads(text)
        return data if isinstance(data, list) else []

    def _save_raw(self, features: list[dict]) -> None:
        atomic_write_text(
            self.path,
            json.dumps(features, ensure_ascii=False, indent=2) + "\n",
        )

    # ---- archive file I/O ----

    def _load_archive_file(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        data = json.loads(text)
        return data if isinstance(data, list) else []

    def _append_archive(self, date: str, record: dict) -> None:
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        path = self._archive_file(date)
        existing = self._load_archive_file(path)
        existing.append(record)
        atomic_write_text(
            path,
            json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
        )

    # ---- public API ----

    def list_all(self) -> list[Feature]:
        """Active (non-terminal) features only."""
        return [Feature(**d) for d in self._load_raw()]

    def list_all_with_archive(
        self, *, last_days: int | None = None
    ) -> list[Feature]:
        """Active features plus archived terminal features."""
        features = [Feature(**d) for d in self._load_raw()]
        if last_days is None:
            archives = list_archives(self._archive_dir, suffix=".json")
        else:
            archives = list_archives(
                self._archive_dir,
                last_days=last_days,
                suffix=".json",
            )
        for f in archives:
            for d in self._load_archive_file(f):
                features.append(Feature(**d))
        return features

    def get(self, feature_id: str) -> Feature | None:
        for d in self._load_raw():
            if d.get("id") == feature_id:
                return Feature(**d)
        for f in list_archives(self._archive_dir, suffix=".json"):
            for d in self._load_archive_file(f):
                if d.get("id") == feature_id:
                    return Feature(**d)
        return None

    def add(self, feature: Feature) -> Feature:
        if feature.status not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid status {feature.status!r}: must be one of {_VALID_STATUSES}"
            )
        with self._locked():
            raw = self._load_raw()
            raw.append(asdict(feature))
            self._save_raw(raw)
        return feature

    def update(self, feature_id: str, **kwargs) -> Feature | None:
        if "status" in kwargs and kwargs["status"] not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid status {kwargs['status']!r}: must be one of {_VALID_STATUSES}"
            )
        with self._locked():
            raw = self._load_raw()
            for i, d in enumerate(raw):
                if d["id"] == feature_id:
                    d.update(kwargs)
                    if kwargs.get("status") == "done" and not d.get("completed_at"):
                        d["completed_at"] = datetime.now(timezone.utc).isoformat()
                    new_status = d.get("status")
                    if new_status in TERMINAL_STATES:
                        today = self._today()
                        # P1-1 (2026-07-09): archive BEFORE removing from active so
                        # a crash mid-terminal cannot vanish the feature (same
                        # inversion as task/store.py). A crash now leaves a harmless
                        # active+archive duplicate, not a lost record.
                        self._append_archive(today, d)
                        raw.pop(i)
                        self._save_raw(raw)
                    else:
                        self._save_raw(raw)
                    return Feature(**d)
        return None

    def filter(self, *, status: str | None = None) -> list[Feature]:
        features = self.list_all()
        if status is not None:
            features = [f for f in features if f.status == status]
        return features
