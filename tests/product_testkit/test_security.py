from pathlib import Path

import pytest

from .security import assert_no_secret_leaks, assert_path_inside


def test_security_assertions_reject_escape_and_secret(tmp_path: Path):
    root = tmp_path / "run"; root.mkdir()
    safe = root / "log.txt"; safe.write_text("status=ok", encoding="utf-8")
    assert_path_inside(safe, root=root)
    assert_no_secret_leaks([safe])
    outside = tmp_path / "outside.txt"; outside.write_text("token=supersecretvalue", encoding="utf-8")
    with pytest.raises(AssertionError):
        assert_path_inside(outside, root=root)
    with pytest.raises(AssertionError):
        assert_no_secret_leaks([outside])
