"""A deterministic first-attempt failure for rerun semantics."""

from pathlib import Path
import os


def test_fails_once_then_passes(tmp_path: Path) -> None:
    marker = Path(os.environ.get("ZF_RERUN_MARKER", str(tmp_path / "attempt.marker")))
    if not marker.exists():
        marker.write_text("first", encoding="utf-8")
        raise AssertionError("first attempt failure")
