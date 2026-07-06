"""ConfigProfile / RoleSet composition tests."""

from __future__ import annotations

import pytest

from zf.core.config.loader import ConfigError, load_config


def test_config_profile_uses_merges_before_project_override(tmp_path):
    text = """\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: prod-runtime/v1}
spec:
  workflow:
    harness_profile: strict
  runtime:
    run_manager:
      backend: codex
      resident_agent:
        enabled: true
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  uses: [prod-runtime/v1]
  version: "1.0"
  project: {name: demo}
  workflow:
    harness_profile: baseline
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)
    cfg = load_config(path)
    assert cfg.workflow.harness_profile == "baseline"
    assert cfg.runtime.run_manager.backend == "codex"
    assert cfg.runtime.run_manager.resident_agent.enabled is True
    assert getattr(cfg, "config_sources")[0]["name"] == "prod-runtime/v1"


def test_unknown_config_profile_fails_closed(tmp_path):
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  uses: [missing/v1]
  version: "1.0"
  project: {name: demo}
""")
    with pytest.raises(ConfigError, match="unknown profile"):
        load_config(path)


def test_conflicting_profiles_fail_closed(tmp_path):
    text = """\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: a/v1}
spec: {workflow: {harness_profile: baseline}}
---
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: b/v1}
spec: {workflow: {harness_profile: strict}}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  uses: [a/v1, b/v1]
  project: {name: demo}
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError, match="conflicting profile value"):
        load_config(path)


def test_roleset_uses_generates_lane_roles(tmp_path):
    text = """\
apiVersion: zaofu.dev/v1
kind: RoleSet
metadata: {name: codex-lanes/v1}
spec:
  backend: codex
  lanes: 2
  stages:
    impl:
      role_pattern: dev-lane-{lane}
      skills: [implementation]
    verify:
      role_pattern: verify-lane-{lane}
      skills: [verification]
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  uses: [codex-lanes/v1]
  version: "1.0"
  project: {name: demo}
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)
    cfg = load_config(path)
    names = {role.name for role in cfg.roles}
    assert {"dev-lane-0", "dev-lane-1", "verify-lane-0", "verify-lane-1"} <= names
    dev0 = next(role for role in cfg.roles if role.name == "dev-lane-0")
    verify0 = next(role for role in cfg.roles if role.name == "verify-lane-0")
    assert dev0.role_kind == "writer"
    assert verify0.role_kind == "reader"
    assert dev0.backend == "codex"


def test_config_profile_can_include_profiles_and_rolesets(tmp_path):
    text = """\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: runtime/v1}
spec:
  runtime:
    run_manager:
      backend: claude-code
      resident_agent: {enabled: true, session_mode: dedicated}
---
apiVersion: zaofu.dev/v1
kind: RoleSet
metadata: {name: lanes/v1}
spec:
  backend: claude-code
  lanes: 1
  stages:
    impl:
      role_pattern: dev-lane-{lane}
    verify:
      role_pattern: verify-lane-{lane}
---
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: bundle/v1}
spec:
  uses: [runtime/v1, lanes/v1]
  workflow:
    plan_approval: false
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  uses: [bundle/v1]
  version: "1.0"
  project: {name: demo}
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)

    cfg = load_config(path)

    assert cfg.runtime.run_manager.backend == "claude-code"
    assert cfg.runtime.run_manager.resident_agent.enabled is True
    assert cfg.workflow.plan_approval_enabled is False
    assert {role.name for role in cfg.roles} == {"dev-lane-0", "verify-lane-0"}
    assert [source["name"] for source in getattr(cfg, "config_sources")] == [
        "runtime/v1",
        "lanes/v1",
        "bundle/v1",
    ]


def test_config_profile_include_cycle_fails_closed(tmp_path):
    text = """\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: a/v1}
spec: {uses: [b/v1]}
---
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: b/v1}
spec: {uses: [a/v1]}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  uses: [a/v1]
  project: {name: demo}
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)

    with pytest.raises(ConfigError, match="uses cycle"):
        load_config(path)


def test_config_profile_unknown_include_fails_closed(tmp_path):
    text = """\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: bundle/v1}
spec: {uses: [missing/v1]}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  uses: [bundle/v1]
  project: {name: demo}
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)

    with pytest.raises(ConfigError, match="unknown profile"):
        load_config(path)


def test_external_profile_sources_merge_and_record_sources(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "common.yaml").write_text("""\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: prod-runtime/v1}
spec:
  runtime:
    run_manager:
      backend: codex
      resident_agent: {enabled: true}
---
apiVersion: zaofu.dev/v1
kind: RoleSet
metadata: {name: codex-lanes/v1}
spec:
  backend: codex
  lanes: 1
  stages:
    impl:
      role_pattern: dev-lane-{lane}
      skills: [implementation]
---
apiVersion: zaofu.dev/v1
kind: SchemaProfile
metadata: {name: local-schema/v1}
spec:
  events:
    local.done: {required: [task_id, status]}
""")
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  profile_sources: [profiles/*.yaml]
  uses: [prod-runtime/v1, codex-lanes/v1]
  version: "1.0"
  project: {name: demo}
  workflow:
    dag:
      schema_profile: local-schema/v1
""")

    cfg = load_config(path)

    assert cfg.runtime.run_manager.backend == "codex"
    assert cfg.runtime.run_manager.resident_agent.enabled is True
    assert {role.name for role in cfg.roles} == {"dev-lane-0"}
    assert "local.done" in cfg.workflow.dag.event_schemas
    sources = getattr(cfg, "config_sources")
    assert any(source["kind"] == "ProfileSource" for source in sources)
    profile_source = next(source for source in sources if source["kind"] == "ProfileSource")
    assert profile_source["path"].endswith("profiles/common.yaml")
    assert profile_source["sha256"]


def test_missing_external_profile_source_fails_closed(tmp_path):
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  profile_sources: [profiles/missing.yaml]
  uses: [prod-runtime/v1]
  version: "1.0"
  project: {name: demo}
""")

    with pytest.raises(ConfigError, match="did not match any files"):
        load_config(path)
