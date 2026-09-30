from __future__ import annotations

from pathlib import Path

from .environment import ProductEnvironment


def test_product_environment_captures_identity_and_cleans(tmp_path: Path):
    env = ProductEnvironment(output_dir=tmp_path, project_root=Path("."))
    metadata = env.capture()
    assert metadata["product_test_root"] == str(env.root)
    assert (tmp_path / "environment.json").exists()
    env.assert_clean()
    env.cleanup()
    assert not env.root.exists()
