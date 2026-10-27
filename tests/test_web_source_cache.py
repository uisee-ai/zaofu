"""Config fingerprints preserve exact values without deep-copying profiles."""
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.web.projections.source_cache import _config_json_default, fingerprint


@dataclass
class NestedConfig:
    values: list = field(default_factory=lambda: [{"nested": ("中文", Path("plan.md"), None)}])
    config: ZfConfig = field(default_factory=ZfConfig)


def test_config_serialization_matches_previous_fingerprint_bytes():
    config = NestedConfig()
    assert json.dumps(config, sort_keys=True, default=_config_json_default) == json.dumps(
        asdict(config), sort_keys=True, default=str)


def test_fingerprint_does_not_copy_config_and_tracks_in_place_changes(tmp_path, monkeypatch):
    config = ZfConfig()
    def reject_copy(*args):
        raise AssertionError("source check deep-copied configuration")
    monkeypatch.setattr("copy.deepcopy", reject_copy)
    first = fingerprint(tmp_path, [], config)
    assert fingerprint(tmp_path, [], config) == first
    config.project.state_dir = ".custom-state"
    assert fingerprint(tmp_path, [], config) != first
