"""Isolation and environment evidence helpers for product-project runs."""

from __future__ import annotations

from pathlib import Path
import os
import tempfile
from typing import Any

from tests.testkit.cleanup import ResourceSnapshot, assert_no_resource_leaks, resource_snapshot
from tests.testkit.environment import capture_environment, write_environment


class ProductEnvironment:
    """Own a disposable product work area without touching the product tree."""

    def __init__(self, *, output_dir: Path, project_root: Path):
        self.output_dir = output_dir.resolve()
        self.project_root = project_root.resolve()
        self.root = Path(tempfile.mkdtemp(prefix="product-test-", dir=self.output_dir))
        self.state_dir = self.root / "state"
        self.data_dir = self.root / "data"
        self.state_dir.mkdir()
        self.data_dir.mkdir()
        self._before: ResourceSnapshot = resource_snapshot(self.root)

    def capture(self, *, config_path: Path | None = None, lock_path: Path | None = None) -> dict[str, Any]:
        metadata = capture_environment(self.project_root, self.state_dir, config_path=config_path, lock_path=lock_path)
        metadata["product_test_root"] = str(self.root)
        write_environment(self.output_dir / "environment.json", metadata)
        return metadata

    def assert_clean(self) -> None:
        assert_no_resource_leaks(self._before, root=self.root)

    def cleanup(self) -> None:
        # Only remove resources created under our private temporary root.
        for path in sorted(self.root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        self.root.rmdir()

