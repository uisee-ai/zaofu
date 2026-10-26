import json
from pathlib import Path

from zf.core.config.loader import load_config
from zf.runtime.workflow_synthesis_support import admission_catalog, admission_contract, synthesis_prompt
from tests.test_web_workflow_proposals import _write_config


def test_synthesis_catalog_distinguishes_installed_skills_and_compiled_completion(tmp_path):
    config_path = tmp_path / "zf.yaml"
    _write_config(config_path)
    extra = tmp_path / "skills" / "optional-skill" / "SKILL.md"
    extra.parent.mkdir(parents=True)
    extra.write_text("# Optional method")
    catalog = admission_catalog(load_config(config_path), tmp_path)
    assert "optional-skill" in catalog["skills"]
    contract = admission_contract(catalog)
    assert all("optional-skill" not in bound for bound in contract["configured_role_skills"].values())
    assert contract["configured_completion_by_family"]["issue"]["id"] == "software_delivery"
    assert contract["configured_completion_by_family"]["issue"]["delivery_policy"] == "report_only"
    prompt = synthesis_prompt(request={"request_id": "R", "revision": 1}, requirement={"objective": "Preview before running"}, catalog=catalog)
    embedded = json.loads(prompt.split("Contract:\n", 1)[1])
    assert embedded["configured_completion_by_family"] == contract["configured_completion_by_family"]
