"""Register source dependencies before creating transport processes in them."""
import yaml

from tests.test_cli_start import project_dir  # noqa: F401
from zf.cli.main import main
from zf.runtime.environment_resources import read_resource_paths


def test_start_declares_dependencies_before_transport(project_dir, monkeypatch):
    import zf.cli.start as start

    path = project_dir / "zf.yaml"
    data = yaml.safe_load(path.read_text())
    data["runtime"] = {"workdirs": {"enabled": True, "provision_paths": ["node_modules"]}}
    path.write_text(yaml.safe_dump(data))
    (project_dir / "node_modules").mkdir()
    observed = []

    def initialize(*args, **kwargs):
        observed.append(read_resource_paths(project_dir))
        return False  # Stop before launching any provider.

    monkeypatch.setattr(start, "_initialize_start_transport", initialize)
    monkeypatch.setattr(start, "_refresh_pricing_catalog", lambda *args: None)
    assert main(["start", "--no-watch", "--skip-workflow-inspect"]) == 1
    assert observed == [["node_modules"]]
