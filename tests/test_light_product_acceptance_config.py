"""Reject an unproducible blocking plan port before approving a workflow."""

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config


@pytest.mark.parametrize("kind", ["IssueFlow", "PrdFlow"])
@pytest.mark.parametrize("key", ["productAcceptanceMode", "product_acceptance_mode"])
def test_light_blocking_product_acceptance_fails_at_config_load(tmp_path, kind, key):
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump_all([
        {"apiVersion": "zaofu.dev/v1", "kind": kind,
         "spec": {"topology": "light", key: "blocking"}},
        {"apiVersion": "zaofu.dev/v1", "kind": "ZfConfig", "spec": {"project": {"name": "test"}}},
    ]))
    with pytest.raises(ConfigError, match="productAcceptanceMode: blocking requires topology: fanout"):
        load_config(path)


@pytest.mark.parametrize("kind", ["IssueFlow", "PrdFlow"])
@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_light_nonblocking_modes_still_load(tmp_path, kind, mode):
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump_all([
        {"apiVersion": "zaofu.dev/v1", "kind": kind,
         "spec": {"topology": "light", "productAcceptanceMode": mode}},
        {"apiVersion": "zaofu.dev/v1", "kind": "ZfConfig", "spec": {"project": {"name": "test"}}},
    ]))
    assert load_config(path).workflow.pipelines
