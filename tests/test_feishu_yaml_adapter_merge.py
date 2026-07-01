"""feishu.yaml adapter config merges into the single ZfConfig (load-time, one
validation, one truth). Backward compatible with inline integrations.feishu_*."""

from __future__ import annotations

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.integrations.feishu.routing import resolve_feishu_route

_ZF = """\
version: "1.0"
project: { name: t }
roles: [{ name: dev, backend: mock }]
"""


def _write(tmp_path, zf_extra="", feishu=None):
    (tmp_path / "zf.yaml").write_text(_ZF + zf_extra)
    if feishu is not None:
        (tmp_path / "feishu.yaml").write_text(feishu)
    return load_config(tmp_path / "zf.yaml")


def test_feishu_yaml_sibling_merges_into_config(tmp_path):
    cfg = _write(tmp_path, feishu="""\
feishu_routing:
  oc_x: { target: agent, backend: codex, cwd: /repo, default_member: zf-coder }
""")
    r = resolve_feishu_route(cfg, "oc_x")
    assert r is not None and r.backend == "codex"


def test_feishu_yaml_nested_under_integrations(tmp_path):
    cfg = _write(tmp_path, feishu="""\
integrations:
  feishu_routing:
    oc_y: { target: agent, backend: claude-code }
""")
    assert resolve_feishu_route(cfg, "oc_y").backend == "claude-code"


def test_inline_in_zf_yaml_still_works_without_feishu_yaml(tmp_path):
    cfg = _write(tmp_path, zf_extra="""\
integrations:
  feishu_routing:
    oc_z: { target: agent, backend: codex }
""")
    assert resolve_feishu_route(cfg, "oc_z").backend == "codex"


def test_no_feishu_yaml_no_routing(tmp_path):
    cfg = _write(tmp_path)
    assert resolve_feishu_route(cfg, "oc_x") is None


def test_conflict_in_both_is_configerror(tmp_path):
    with pytest.raises(ConfigError, match="BOTH zf.yaml and feishu.yaml"):
        _write(tmp_path, zf_extra="""\
integrations:
  feishu_routing:
    oc_a: { target: agent, backend: codex }
""", feishu="""\
feishu_routing:
  oc_b: { target: agent, backend: codex }
""")


def test_feishu_yaml_invalid_target_still_validated(tmp_path):
    # the merged feishu.yaml goes through the same _build_feishu_routing validation
    with pytest.raises(ConfigError, match="target must be one of"):
        _write(tmp_path, feishu="""\
feishu_routing:
  oc_x: { target: bogus }
""")
