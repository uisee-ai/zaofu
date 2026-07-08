"""feishu.yaml adapter config merges into the single ZfConfig (load-time, one
validation, one truth). Backward compatible with inline integrations.feishu_*."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.integrations.feishu.routing import resolve_feishu_route

_REPO_ROOT = Path(__file__).resolve().parents[1]

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


def test_shipped_feishu_yaml_validates_green_without_secrets(tmp_path, monkeypatch):
    # Regression (autoresearch controlled-stuck-recovery, validate_failed rc=1):
    # every env-var reference in the shipped feishu.yaml must carry a
    # `:-__..._unset__` sentinel default so `zf validate` stays green in
    # sandboxes / CI / autoresearch worktrees that run without the Feishu
    # secrets. A bare `${FEISHU_OPENID}` fail-closed the entire config load
    # even for projects that never opted into Feishu.
    for var in ("FEISHU_OPENID", "FEISHU_KANBAN", "FEISHU_RUNM",
                "ZF_OWNER_VISIBLE_CHAT"):
        monkeypatch.delenv(var, raising=False)
    shipped = (_REPO_ROOT / "feishu.yaml").read_text(encoding="utf-8")
    # no bare `${VAR}` without a `:-` default may survive in the shipped file
    import re
    bare = re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", shipped)
    assert not bare, f"shipped feishu.yaml has default-less env refs: {bare}"
    # and it must load+validate cleanly when merged into a minimal project
    (tmp_path / "feishu.yaml").write_text(shipped)
    cfg = _write(tmp_path)
    # the sentinel open_id is inert (never matches a real Feishu principal)
    assert "__zf_feishu_openid_unset__" in cfg.integrations.feishu_identity.users


def test_feishu_yaml_invalid_target_still_validated(tmp_path):
    # the merged feishu.yaml goes through the same _build_feishu_routing validation
    with pytest.raises(ConfigError, match="target must be one of"):
        _write(tmp_path, feishu="""\
feishu_routing:
  oc_x: { target: bogus }
""")
