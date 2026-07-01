"""Tests for zf presets and zf init --preset."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.main import main


class TestPresetsCLI:
    def test_presets_list(self, capsys):
        with pytest.raises(SystemExit):
            main(["presets", "--help"])

    def test_presets_list_shows_all(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        result = main(["presets"])
        assert result == 0
        captured = capsys.readouterr()
        assert "safe-local" in captured.out
        assert "minimal" in captured.out
        assert "code-assist" in captured.out
        assert "design-first" in captured.out

    def test_safe_local_preset_has_intake_and_static_gate(self):
        from zf.core.config.presets import get_preset
        preset = get_preset("safe-local")
        assert preset["preset"] == "safe-local"
        assert "intake" in preset["stage_labels"]
        assert "static" in preset["quality_gates"]
        assert preset["quality_gates"]["static"]["enabled"] is True

    def test_safe_local_preset_yaml_loads_with_new_fields(self, tmp_path: Path):
        from zf.core.config.presets import generate_preset_yaml
        from zf.core.config.loader import load_config
        yaml_text = generate_preset_yaml("safe-local", "demo")
        p = tmp_path / "zf.yaml"
        p.write_text(yaml_text)
        cfg = load_config(p)
        assert cfg.preset == "safe-local"
        assert "intake" in cfg.stage_labels
        assert "static" in cfg.quality_gates
        assert cfg.quality_gates["static"].enabled is True

    def test_presets_show(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        result = main(["presets", "show", "minimal"])
        assert result == 0
        captured = capsys.readouterr()
        assert "minimal" in captured.out
        assert "roles" in captured.out.lower()

    def test_presets_show_unknown(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = main(["presets", "show", "nonexistent"])
        assert result != 0


class TestInitPreset:
    def test_init_with_preset(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        result = main(["init", "--preset", "minimal"])
        assert result == 0
        assert (tmp_path / "zf.yaml").exists()
        config = yaml.safe_load((tmp_path / "zf.yaml").read_text())
        assert config["preset"] == "minimal"

    def test_init_preset_creates_state(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        main(["init", "--preset", "code-assist"])
        assert (tmp_path / ".zf").exists()
        assert (tmp_path / ".zf" / "events.jsonl").exists()

    def test_init_unknown_preset(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = main(["init", "--preset", "bad"])
        assert result != 0

    def test_init_preset_does_not_overwrite(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zf.yaml").write_text("existing: true\n")
        main(["init", "--preset", "minimal"])
        # Should not overwrite existing zf.yaml without --force
        content = (tmp_path / "zf.yaml").read_text()
        assert "existing" in content
