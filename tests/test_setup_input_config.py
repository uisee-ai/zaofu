import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.render import renderable_config_to_primitive


def test_setup_input_paths_survive_config_load_and_render(tmp_path):
    path = tmp_path / "zf.yaml"
    path.write_text("version: '1.0'\nproject:\n  name: demo\n  scripts: {setup: 'true'}\n"
                    "  setup_inputs: [data/identity.json, tools/versions.txt]\n")
    config = load_config(path)
    assert config.project.setup_inputs == ["data/identity.json", "tools/versions.txt"]
    path.write_text(yaml.safe_dump(renderable_config_to_primitive(config)))
    assert load_config(path).project.setup_inputs == config.project.setup_inputs


@pytest.mark.parametrize("value", ["data.json", [1], [""], ["../outside"], ["/etc/passwd"]])
def test_invalid_setup_input_config_is_rejected(tmp_path, value):
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump({"version": "1.0", "project": {"name": "demo", "setup_inputs": value}}))
    with pytest.raises(ConfigError, match="setup_inputs"):
        load_config(path)
