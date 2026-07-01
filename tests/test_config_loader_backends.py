"""Tests for B-MIXEDBACKEND-01: loader parses `backends: [...]` list.

Loader enforces:
  - `backend` and `backends` are mutually exclusive (can't set both)
  - `len(backends)` must equal `replicas`
  - each entry non-empty string
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "zf.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


def _minimal_config(roles: list[dict]) -> dict:
    return {
        "version": "1.0",
        "project": {"name": "t"},
        "session": {"tmux_session": "t"},
        "roles": roles,
    }


class TestLoaderBackendsList:
    def test_backends_list_expands_per_replica(self, tmp_path):
        p = _write_yaml(
            tmp_path,
            _minimal_config(
                [
                    {
                        "name": "dev",
                        "backends": ["claude-code", "codex"],
                        "replicas": 2,
                        "permission_mode": "bypass",
                    }
                ]
            ),
        )
        cfg = load_config(p)
        assert len(cfg.roles) == 2
        assert cfg.roles[0].instance_id == "dev-1"
        assert cfg.roles[0].backend == "claude-code"
        assert cfg.roles[1].instance_id == "dev-2"
        assert cfg.roles[1].backend == "codex"

    def test_both_backend_and_backends_rejected(self, tmp_path):
        p = _write_yaml(
            tmp_path,
            _minimal_config(
                [
                    {
                        "name": "dev",
                        "backend": "claude-code",
                        "backends": ["claude-code", "codex"],
                        "replicas": 2,
                    }
                ]
            ),
        )
        with pytest.raises(ConfigError, match="either .backend. .*or .backends."):
            load_config(p)

    def test_backends_length_mismatch_rejected(self, tmp_path):
        p = _write_yaml(
            tmp_path,
            _minimal_config(
                [
                    {
                        "name": "dev",
                        "backends": ["claude-code"],
                        "replicas": 2,
                    }
                ]
            ),
        )
        with pytest.raises(ConfigError, match="len.backends"):
            load_config(p)

    def test_backends_entry_empty_rejected(self, tmp_path):
        p = _write_yaml(
            tmp_path,
            _minimal_config(
                [
                    {
                        "name": "dev",
                        "backends": ["claude-code", ""],
                        "replicas": 2,
                    }
                ]
            ),
        )
        with pytest.raises(ConfigError, match="non-empty"):
            load_config(p)

    def test_backends_must_be_list(self, tmp_path):
        p = _write_yaml(
            tmp_path,
            _minimal_config(
                [
                    {
                        "name": "dev",
                        "backends": "claude-code",  # scalar, not list
                        "replicas": 1,
                    }
                ]
            ),
        )
        with pytest.raises(ConfigError, match="list of non-empty strings"):
            load_config(p)

    def test_legacy_singular_backend_still_works(self, tmp_path):
        """Configs written before B-MIXEDBACKEND-01 must keep loading."""
        p = _write_yaml(
            tmp_path,
            _minimal_config(
                [
                    {
                        "name": "dev",
                        "backend": "claude-code",
                        "replicas": 2,
                        "permission_mode": "bypass",
                    }
                ]
            ),
        )
        cfg = load_config(p)
        assert len(cfg.roles) == 2
        assert all(r.backend == "claude-code" for r in cfg.roles)
