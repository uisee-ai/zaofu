"""Safe YAML acceleration preserves config values and rejection boundaries."""

from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config


def test_config_is_equal_with_and_without_c_yaml(monkeypatch):
    fixture = Path(__file__).parent / "fixtures" / "valid.yaml"
    accelerated = asdict(load_config(fixture))
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    assert asdict(load_config(fixture)) == accelerated


@pytest.mark.parametrize("fallback", [False, True])
def test_config_loader_rejects_python_object_tags(tmp_path, monkeypatch, fallback):
    if fallback:
        monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    config = tmp_path / "zf.yaml"
    marker = tmp_path / "must-not-exist"
    config.write_text(
        f"!!python/object/apply:os.system ['touch {marker}']\n", encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="YAML parse error"):
        load_config(config)
    assert not marker.exists()
