"""Tests for config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import load_config, validate_config, ConfigError

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_valid_config():
    cfg = load_config(FIXTURES / "valid.yaml")
    assert cfg.project.name == "test-project"
    assert cfg.orchestrator.backend == "python"
    assert len(cfg.roles) == 1
    assert cfg.roles[0].name == "dev"


def test_load_minimal_config():
    cfg = load_config(FIXTURES / "minimal.yaml")
    assert cfg.project.name == "minimal"
    assert cfg.session.tmux_session == "zf"  # default
    assert cfg.roles == []


def test_load_nonexistent_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config(Path("/nonexistent/zf.yaml"))


def test_validate_missing_project():
    errors = validate_config(FIXTURES / "invalid_missing_project.yaml")
    assert any("project" in e.lower() for e in errors)


def test_validate_role_missing_name():
    errors = validate_config(FIXTURES / "invalid_bad_role.yaml")
    assert any("name" in e.lower() for e in errors)


def test_validate_valid_config():
    errors = validate_config(FIXTURES / "valid.yaml")
    assert errors == []


def test_validate_rejects_missing_artifact_matrix_gate_config_ref(
    tmp_path: Path,
) -> None:
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "- name: judge\n"
        "  backend: mock\n"
        "workflow:\n"
        "  stages:\n"
        "  - id: final-judge\n"
        "    trigger: verify.passed\n"
        "    topology: fanout_reader\n"
        "    roles: [judge]\n"
        "    aggregate:\n"
        "      success_event: judge.passed\n"
        "      failure_event: judge.failed\n"
        "    criteria:\n"
        "      success_criteria:\n"
        "      - kind: artifact_matrix_gate\n"
        "        config_ref: docs/plans/missing-gate.json\n",
        encoding="utf-8",
    )

    errors = validate_config(p)

    assert errors
    assert "config_ref 'docs/plans/missing-gate.json' does not exist" in errors[0]

    (tmp_path / "docs/plans").mkdir(parents=True)
    (tmp_path / "docs/plans/missing-gate.json").write_text("{}\n", encoding="utf-8")
    assert validate_config(p) == []


def test_loads_stage_criteria_instructions_without_success_gate(
    tmp_path: Path,
) -> None:
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "- name: scan\n"
        "  backend: mock\n"
        "  role_kind: reader\n"
        "workflow:\n"
        "  stages:\n"
        "  - id: scan\n"
        "    trigger: prd.requested\n"
        "    topology: fanout_reader\n"
        "    roles: [scan]\n"
        "    aggregate:\n"
        "      success_event: prd.scan.completed\n"
        "      failure_event: prd.scan.failed\n"
        "    criteria:\n"
        "      instructions:\n"
        "      - Initial scan is not implementation verification.\n",
        encoding="utf-8",
    )

    cfg = load_config(p)

    criteria = cfg.workflow.stages[0].criteria
    assert criteria.instructions == [
        "Initial scan is not implementation verification.",
    ]
    assert criteria.success_criteria == []


def test_load_safety_tool_closure_default_enabled(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text('version: "1.0"\nproject:\n  name: test\n')
    cfg = load_config(p)
    assert cfg.safety.tool_closure_enabled is True


def test_load_safety_tool_closure_can_disable(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "safety:\n"
        "  tool_closure:\n"
        "    enabled: false\n"
    )
    cfg = load_config(p)
    assert cfg.safety.tool_closure_enabled is False


def test_security_event_signing_typo_rejected(tmp_path: Path):
    # P1-3: a typo'd sub-key must not silently fall back to default (which would
    # leave event signing disabled while `zf validate` stays green).
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "security:\n"
        "  event_signing:\n"
        "    enable: true\n"  # typo: should be `enabled`
    )
    with pytest.raises(ConfigError, match="security.event_signing"):
        load_config(p)


def test_security_event_signing_valid_loads(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "security:\n"
        "  event_signing:\n"
        "    enabled: true\n"
    )
    cfg = load_config(p)
    assert cfg.security.event_signing.enabled is True


def test_verification_contract_subkey_typo_rejected(tmp_path: Path):
    # P1-3: a typo'd gate key must surface, not silently disable the gate.
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "verification:\n"
        "  contract:\n"
        "    requird: true\n"  # typo: should be `required`
    )
    with pytest.raises(ConfigError, match="verification.contract"):
        load_config(p)


def test_verification_section_typo_rejected(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "verification:\n"
        "  sceope:\n"  # typo: should be `scope`
        "    fail_closed: true\n"
    )
    with pytest.raises(ConfigError, match="verification"):
        load_config(p)


def test_verification_full_valid_loads(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "verification:\n"
        "  contract:\n"
        "    required: true\n"
        "    quality_required: true\n"
        "  scope:\n"
        "    fail_closed: true\n"
        "  event_schema:\n"
        "    mode: blocking\n"
    )
    cfg = load_config(p)
    assert cfg.verification.contract.required is True
    assert cfg.verification.scope.fail_closed is True
    assert cfg.verification.event_schema.mode == "blocking"


def test_load_autoresearch_trigger_policy_budget(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "autoresearch:\n"
        "  trigger_policy:\n"
        "    enabled: true\n"
        "    mode: continuous\n"
        "    repair_mode: bounded_repair\n"
        "    self_repair_backend: codex\n"
        "    eligible_failure_classes:\n"
        "      - worker_stuck\n"
        "      - deterministic_resume\n"
        "    severity_min: high\n"
        "    cooldown_minutes: 30\n"
        "    max_triggers_per_hour: 5000\n"
        "    max_daily_runs: 5000\n"
    )

    cfg = load_config(p)

    policy = cfg.autoresearch.trigger_policy
    assert policy.mode == "continuous"
    assert policy.repair_mode == "bounded_repair"
    assert policy.self_repair_backend == "codex"
    assert policy.eligible_failure_classes == ["worker_stuck", "deterministic_resume"]
    assert policy.max_triggers_per_hour == 5000
    assert policy.max_daily_runs == 5000


def test_load_runtime_run_manager_resident_agent_config(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  run_manager:\n"
        "    backend: claude-code\n"
        "    reflect:\n"
        "      enabled: true\n"
        "      backend: codex\n"
        "      timeout_seconds: 90\n"
        "    resident_agent:\n"
        "      enabled: true\n"
        "      transport: tmux\n"
        "      prompt_on_start: true\n"
        "      session_mode: dedicated\n"
        "      tmux_session: zf-test-run-manager\n"
        "      instance_id: run-manager\n"
        "    source_repair:\n"
        "      enabled: true\n"
        "      backend: codex\n"
        "      mode: isolated_worktree\n"
        "      apply_policy: proposal_only\n"
        "      restart_policy: never_during_active_run\n"
        "      restart_boundary: terminal_or_operator_approved_checkpoint\n"
        "      replay_before_restart: true\n"
        "      allow_paths: [src/zf/**, tests/**]\n"
        "      deny_paths: [.env, '**/events.jsonl']\n"
    )

    cfg = load_config(p)

    assert cfg.runtime.run_manager.backend == "claude-code"
    assert cfg.runtime.run_manager.reflect.enabled is True
    assert cfg.runtime.run_manager.reflect.backend == "codex"
    assert cfg.runtime.run_manager.reflect.timeout_seconds == 90
    resident = cfg.runtime.run_manager.resident_agent
    assert resident.enabled is True
    assert resident.transport == "tmux"
    assert resident.prompt_on_start is True
    assert resident.session_mode == "dedicated"
    assert resident.tmux_session == "zf-test-run-manager"
    assert resident.instance_id == "run-manager"
    source_repair = cfg.runtime.run_manager.source_repair
    assert source_repair.enabled is True
    assert source_repair.backend == "codex"
    assert source_repair.mode == "isolated_worktree"
    assert source_repair.apply_policy == "proposal_only"
    assert source_repair.restart_policy == "never_during_active_run"
    assert source_repair.restart_boundary == "terminal_or_operator_approved_checkpoint"
    assert source_repair.replay_before_restart is True
    assert source_repair.allow_paths == ["src/zf/**", "tests/**"]
    assert source_repair.deny_paths == [".env", "**/events.jsonl"]


def test_load_runtime_feishu_inbound_config(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  feishu_inbound:\n"
        "    enabled: true\n"
        "    mode: bridge\n"
        "    debounce_ms: 250\n"
        "    require_routing: false\n"
    )

    cfg = load_config(p)

    inbound = cfg.runtime.feishu_inbound
    assert inbound.enabled is True
    assert inbound.mode == "bridge"
    assert inbound.debounce_ms == 250
    assert inbound.require_routing is False


def test_load_runtime_autoresearch_resident_config(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  autoresearch_resident:\n"
        "    enabled: true\n"
        "    interval_seconds: 2.5\n"
        "    max_actions_per_tick: 4\n"
        "    worktree_root: /tmp/ar-worktrees\n"
        "    output_root: /tmp/ar-output\n"
        "    self_repair_consumer: true\n"
        "    self_repair_spawn: true\n"
        "    self_repair_backend: claude-code\n"
    )

    cfg = load_config(p)

    resident = cfg.runtime.autoresearch_resident
    assert resident.enabled is True
    assert resident.interval_seconds == 2.5
    assert resident.max_actions_per_tick == 4
    assert resident.worktree_root == "/tmp/ar-worktrees"
    assert resident.output_root == "/tmp/ar-output"
    assert resident.self_repair_consumer is True
    assert resident.self_repair_spawn is True
    assert resident.self_repair_backend == "claude-code"


def test_load_runtime_feishu_inbound_rejects_bad_mode(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  feishu_inbound:\n"
        "    enabled: true\n"
        "    mode: webhook\n"
    )

    with pytest.raises(ConfigError, match="runtime.feishu_inbound.mode"):
        load_config(p)


def test_load_runtime_run_manager_resident_rejects_bad_session_mode(
    tmp_path: Path,
):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  run_manager:\n"
        "    backend: claude-code\n"
        "    resident_agent:\n"
        "      enabled: true\n"
        "      session_mode: global\n"
    )

    with pytest.raises(ConfigError, match="session_mode"):
        load_config(p)


def test_autoresearch_self_repair_backend_is_not_hardcoded(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text('version: "1.0"\nproject:\n  name: test\n')

    cfg = load_config(p)

    assert cfg.autoresearch.trigger_policy.self_repair_backend == ""
    assert cfg.runtime.run_manager.backend == ""


def test_validate_runs_tool_closure_by_default(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: claude-code\n"
        "    permission_mode: allowlist\n"
        "    allowed_tools:\n"
        "      - '*'\n"
    )
    errors = validate_config(p)
    assert errors
    assert any("wildcard" in e.lower() for e in errors)


def test_validate_respects_disabled_tool_closure(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "safety:\n"
        "  tool_closure:\n"
        "    enabled: false\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: claude-code\n"
        "    permission_mode: allowlist\n"
        "    allowed_tools:\n"
        "      - '*'\n"
    )
    assert validate_config(p) == []


def test_load_empty_file_rejected(tmp_path: Path):
    """P0-VALIDATE-LOADER-01: empty zf.yaml has no project.name and is
    therefore rejected — load_config no longer 'falls back to defaults'."""
    p = tmp_path / "empty.yaml"
    p.write_text("")
    with pytest.raises(ConfigError, match="project"):
        load_config(p)


def test_validate_invalid_yaml(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(": : : invalid yaml [[[")
    errors = validate_config(p)
    assert any("parse" in e.lower() or "yaml" in e.lower() for e in errors)


def test_validate_non_dict_yaml(tmp_path: Path):
    p = tmp_path / "list.yaml"
    p.write_text("- item1\n- item2\n")
    errors = validate_config(p)
    assert any("empty" in e.lower() or "mapping" in e.lower() for e in errors)


def test_validate_role_not_a_dict(tmp_path: Path):
    p = tmp_path / "bad_roles.yaml"
    p.write_text('version: "1.0"\nproject:\n  name: test\nroles:\n  - just_a_string\n')
    errors = validate_config(p)
    assert any("mapping" in e.lower() for e in errors)


def test_load_real_zf_yaml():
    """Loads the actual project zf.yaml."""
    root = Path(__file__).parent.parent / "zf.yaml"
    if root.exists():
        cfg = load_config(root)
        assert cfg.project.name
        assert len(cfg.roles) >= 1


def test_load_version_coerced_to_str(tmp_path: Path):
    """YAML version: 1.0 (float) must become str '1.0'."""
    p = tmp_path / "zf.yaml"
    p.write_text("version: 1.0\nproject:\n  name: test\n")
    cfg = load_config(p)
    assert cfg.version == "1.0"
    assert isinstance(cfg.version, str)


def test_load_preset(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text('version: "1.0"\npreset: safe-local\nproject:\n  name: test\n')
    cfg = load_config(p)
    assert cfg.preset == "safe-local"


def test_load_stage_labels(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        'stage_labels:\n  intake: "Step 1"\n  build: "Step 2"\n'
    )
    cfg = load_config(p)
    assert cfg.stage_labels == {"intake": "Step 1", "build": "Step 2"}


def test_load_quality_gates(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "quality_gates:\n"
        "  static:\n"
        "    enabled: true\n"
        "    required_checks:\n"
        "      - artifacts_present\n"
        "  review:\n"
        "    enabled: false\n"
    )
    cfg = load_config(p)
    assert "static" in cfg.quality_gates
    assert cfg.quality_gates["static"].enabled is True
    assert cfg.quality_gates["static"].required_checks == ["artifacts_present"]
    assert cfg.quality_gates["review"].enabled is False


def test_config_loads_replan_eval_profile_policy(tmp_path: Path) -> None:
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "workflow:\n"
        "  harness_profile: baseline\n"
        "  replan_eval:\n"
        "    enabled: true\n"
        "    profile: release\n"
        "    require_source_coverage: true\n"
        "    strict_requires_independent_review: true\n"
        "    release_requires_e2e: true\n"
        "    release_requires_security: true\n"
        "    release_requires_human_approval: true\n",
        encoding="utf-8",
    )

    cfg = load_config(p)

    assert cfg.workflow.replan_eval.enabled is True
    assert cfg.workflow.replan_eval.profile == "release"
    assert cfg.workflow.replan_eval.require_source_coverage is True
    assert cfg.workflow.replan_eval.strict_requires_independent_review is True
    assert cfg.workflow.replan_eval.release_requires_e2e is True
    assert cfg.workflow.replan_eval.release_requires_security is True
    assert cfg.workflow.replan_eval.release_requires_human_approval is True


def test_replan_eval_profile_defaults_to_harness_profile(tmp_path: Path) -> None:
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "workflow:\n"
        "  harness_profile: strict\n",
        encoding="utf-8",
    )

    cfg = load_config(p)

    assert cfg.workflow.replan_eval.enabled is False
    assert cfg.workflow.replan_eval.profile == "strict"


def test_load_role_triggers_publishes(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "roles:\n"
        "  - name: dev\n"
        "    triggers:\n"
        "      - task.assigned\n"
        "    publishes:\n"
        "      - dev.build.done\n"
    )
    cfg = load_config(p)
    assert cfg.roles[0].triggers == ["task.assigned"]
    assert cfg.roles[0].publishes == ["dev.build.done"]


def test_load_role_runtime_tunables(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    max_rework_attempts: 9\n"
        "    orphan_warning_seconds: 12\n"
        "    orphan_escalate_seconds: 34\n"
        "    drain_hold_seconds: 56\n"
    )
    role = load_config(p).roles[0]
    assert role.max_rework_attempts == 9
    assert role.orphan_warning_seconds == 12.0
    assert role.orphan_escalate_seconds == 34.0
    assert role.drain_hold_seconds == 56.0


def test_load_openclaw_remote_provider_binding(tmp_path: Path) -> None:
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "providers:\n"
        "  openclaw:\n"
        "    default:\n"
        "      mode: remote_gateway\n"
        "      base_url: http://127.0.0.1:18789\n"
        "      token_env: OPENCLAW_GATEWAY_TOKEN\n"
        "      default_workspace_policy: isolated\n"
        "      tool_profile: safe\n"
        "      timeout_seconds: 3\n"
    )

    cfg = load_config(p)
    binding = cfg.providers.openclaw.bindings["default"]

    assert cfg.providers.openclaw.default_binding == "default"
    assert binding.base_url == "http://127.0.0.1:18789"
    assert binding.token_env == "OPENCLAW_GATEWAY_TOKEN"
    assert binding.timeout_seconds == 3.0


def test_openclaw_remote_provider_binding_default_timeout_is_long_enough(
    tmp_path: Path,
) -> None:
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "providers:\n"
        "  openclaw:\n"
        "    default:\n"
        "      mode: remote_gateway\n"
        "      base_url: http://127.0.0.1:18789\n"
    )

    cfg = load_config(p)
    binding = cfg.providers.openclaw.bindings["default"]

    assert binding.timeout_seconds == 120.0


def test_validate_openclaw_binding_rejects_invalid_token_env(tmp_path: Path) -> None:
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "providers:\n"
        "  openclaw:\n"
        "    default:\n"
        "      base_url: http://127.0.0.1:18789\n"
        "      token_env: not-a-valid-env\n"
    )

    errors = validate_config(p)

    assert errors
    assert any("token_env" in error for error in errors)


def test_validate_openclaw_binding_requires_http_base_url(tmp_path: Path) -> None:
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "providers:\n"
        "  openclaw:\n"
        "    default:\n"
        "      base_url: ssh://openclaw.example\n"
    )

    errors = validate_config(p)

    assert errors
    assert any("base_url" in error for error in errors)


def test_load_openclaw_feishu_bridge_integration(tmp_path: Path) -> None:
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "integrations:\n"
        "  openclaw_feishu_bridge:\n"
        "    enabled: true\n"
        "    default_binding: zaofu-main\n"
        "    bindings:\n"
        "      zaofu-main:\n"
        "        zaofu:\n"
        "          channel_id: ch-zaofu\n"
        "          thread_id: main\n"
        "        openclaw:\n"
        "          provider_binding_id: remote\n"
        "          account_id: default\n"
        "          agent_id: zaofu-bridge\n"
        "        feishu:\n"
        "          chat_id: oc_group\n"
        "        outbound:\n"
        "          include_event_types:\n"
        "            - channel.message.posted\n"
        "          exclude_roles:\n"
        "            - system\n"
    )

    cfg = load_config(p)
    bridge = cfg.integrations.openclaw_feishu_bridge
    binding = bridge.bindings["zaofu-main"]

    assert bridge.enabled is True
    assert bridge.default_binding == "zaofu-main"
    assert binding.zaofu.channel_id == "ch-zaofu"
    assert binding.openclaw.provider_binding_id == "remote"
    assert binding.feishu.target == "chat:oc_group"
    assert binding.outbound.include_event_types == ["channel.message.posted"]


def test_validate_openclaw_feishu_bridge_rejects_bad_default(tmp_path: Path) -> None:
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "integrations:\n"
        "  openclaw_feishu_bridge:\n"
        "    enabled: true\n"
        "    default_binding: missing\n"
        "    bindings:\n"
        "      zaofu-main:\n"
        "        zaofu: {channel_id: ch-zaofu}\n"
        "        feishu: {target: chat:oc_group}\n"
    )

    errors = validate_config(p)

    assert errors
    assert any("default_binding" in error for error in errors)


def test_load_role_autoscale_policy(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    replicas: 2\n"
        "    autoscale:\n"
        "      enabled: true\n"
        "      min_replicas: 2\n"
        "      max_replicas: 6\n"
        "      target_ready_tasks_per_worker: 2\n"
        "      scale_up_pending_seconds: 3\n"
        "      scale_down_idle_seconds: 4\n"
        "      cooldown_seconds: 5\n"
        "      drain_before_stop: false\n"
    )

    roles = load_config(p).roles

    assert [role.instance_id for role in roles] == ["dev-1", "dev-2"]
    assert roles[0].autoscale.enabled is True
    assert roles[0].autoscale.min_replicas == 2
    assert roles[0].autoscale.max_replicas == 6
    assert roles[0].autoscale.target_ready_tasks_per_worker == 2
    assert roles[0].autoscale.scale_up_pending_seconds == 3.0
    assert roles[0].autoscale.scale_down_idle_seconds == 4.0
    assert roles[0].autoscale.cooldown_seconds == 5.0
    assert roles[0].autoscale.drain_before_stop is False


def test_load_role_autoscale_caps_max_at_six(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    autoscale:\n"
        "      enabled: true\n"
        "      max_replicas: 7\n"
    )

    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "max_replicas" in str(exc.value)


def test_load_verification_semantic_enabled(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "verification:\n"
        "  semantic:\n"
        "    enabled: true\n"
    )
    cfg = load_config(p)
    assert cfg.verification.semantic.enabled is True


def test_load_verification_strict_runtime_rule_options(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "verification:\n"
        "  scope:\n"
        "    fail_closed: true\n"
        "  architecture:\n"
        "    enabled: true\n"
        "  promoted:\n"
        "    enabled: true\n"
    )
    cfg = load_config(p)
    assert cfg.verification.scope.fail_closed is True
    assert cfg.verification.architecture.enabled is True
    assert cfg.verification.promoted.enabled is True


def test_load_verification_contract_hardening_options(tmp_path: Path):
    path = tmp_path / "zf.yaml"
    path.write_text(
        "project:\n"
        "  name: t\n"
        "verification:\n"
        "  contract:\n"
        "    required: true\n"
        "    quality_required: true\n"
        "    rework_delta_required: true\n"
        "    dispatch_token_required: true\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n"
    )
    cfg = load_config(path)
    assert cfg.verification.contract.required is True
    assert cfg.verification.contract.quality_required is True
    assert cfg.verification.contract.rework_delta_required is True
    assert cfg.verification.contract.dispatch_token_required is True


def test_load_skill_sources_and_runtime_skills(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "skill_sources:\n"
        "  - name: agent-skills\n"
        "    path: /tmp/agent-skills/skills\n"
        "    mode: readonly\n"
        "runtime:\n"
        "  skills:\n"
        "    materialize: copy\n"
        "    strict: true\n"
        "roles:\n"
        "  - name: dev\n"
        "    skills:\n"
        "      - test-driven-development\n"
    )
    cfg = load_config(p)
    assert cfg.skill_sources[0].name == "agent-skills"
    assert cfg.skill_sources[0].path == "/tmp/agent-skills/skills"
    assert cfg.runtime.skills.materialize == "copy"
    assert cfg.runtime.skills.strict is True
    assert cfg.roles[0].skills == ["test-driven-development"]


def test_load_runtime_git_remote_policy(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  git:\n"
        "    remote_policy: required\n"
    )

    cfg = load_config(p)

    assert cfg.runtime.git.remote_policy == "required"


def test_validate_rejects_invalid_runtime_git_remote_policy(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "runtime:\n"
        "  git:\n"
        "    remote_policy: always-push\n"
    )

    errors = validate_config(p)

    assert errors
    assert any("remote_policy" in error for error in errors)


def test_role_name_must_match_safe_pattern(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "roles:\n"
        "  - name: dev:bad\n"
    )
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "role name" in str(exc.value).lower() or "invalid" in str(exc.value).lower()


def test_role_name_rejects_special_characters(tmp_path: Path):
    for bad in ["foo bar", "../etc", "rm -rf", "9start", "-leading", "a" * 33]:
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\nproject:\n  name: test\n'
            "roles:\n"
            f"  - name: {bad!r}\n"
        )
        with pytest.raises(ConfigError):
            load_config(p)


def test_role_name_accepts_normal_names(tmp_path: Path):
    for good in ["dev", "review", "test_role", "arch-1", "judge2"]:
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\nproject:\n  name: test\n'
            "roles:\n"
            f"  - name: {good}\n"
        )
        cfg = load_config(p)
        assert cfg.roles[0].name == good


def test_role_permission_mode_default_is_bypass(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "roles:\n"
        "  - name: dev\n"
    )
    cfg = load_config(p)
    assert cfg.roles[0].permission_mode == "bypass"


def test_role_permission_mode_allowlist(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "roles:\n"
        "  - name: dev\n"
        "    permission_mode: allowlist\n"
    )
    cfg = load_config(p)
    assert cfg.roles[0].permission_mode == "allowlist"


def test_role_transport_default_is_tmux(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "roles:\n"
        "  - name: dev\n"
    )
    cfg = load_config(p)
    assert cfg.roles[0].transport == "tmux"


def test_role_transport_stream_json(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "roles:\n"
        "  - name: dev\n"
        "    transport: stream-json\n"
    )
    cfg = load_config(p)
    assert cfg.roles[0].transport == "stream-json"


def test_role_transport_invalid_value_rejected(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "roles:\n"
        "  - name: dev\n"
        "    transport: carrier-pigeon\n"
    )
    with pytest.raises(ConfigError):
        load_config(p)


def test_role_permission_mode_invalid_value_rejected(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "roles:\n"
        "  - name: dev\n"
        "    permission_mode: yolo\n"
    )
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_real_zf_yaml_expands_thin_prd_controller():
    """The repository config stays thin while profiles provide the runtime contract."""
    root = Path(__file__).parent.parent / "zf.yaml"
    cfg = load_config(root)

    assert len(root.read_text(encoding="utf-8").splitlines()) < 90
    assert cfg.preset == ""
    assert cfg.project.name == "zaofu"
    assert cfg.workflow.harness_profile == "strict"
    assert cfg.workflow.dag.schema_profile == "canonical-dag/v6"
    assert cfg.goal.enabled is True
    assert cfg.verification.event_schema.mode == "blocking"
    assert cfg.verification.report_evidence_gate == "fail_closed"
    assert cfg.workflow.completion_audit.enabled is True
    assert cfg.workflow.resume_packet.enabled is True
    assert cfg.runtime.run_manager.resident_agent.enabled is True
    assert cfg.runtime.run_manager.resident_agent.session_mode == "dedicated"
    assert cfg.runtime.autoresearch_resident.enabled is True
    assert cfg.runtime.workdirs.enabled is True
    assert cfg.runtime.workdirs.mode == "worktree"
    assert cfg.runtime.git.candidate_base_ref == "dev"
    assert cfg.runtime.git.ship_target_branch == "dev"
    assert {role.name for role in cfg.roles} == {
        "product-scan",
        "tech-scan",
        "planner",
        "flow-discovery",
        "judge-prd",
        "dev-lane-0",
        "dev-lane-1",
        "verify-lane-0",
        "verify-lane-1",
        "orchestrator",
    }
    assert [stage.id for stage in cfg.workflow.stages] == [
        "prd-scan",
        "prd-plan",
        "prd-post-verify-discovery",
        "prd-lanes-impl",
        "prd-lanes-verify",
        "prd-lanes-final",
    ]


def test_load_workflow_dag_graph_static_gate_action(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "workflow:\n"
        "  dag:\n"
        "    enabled: true\n"
        "    graph_static_gate_action: true\n"
        "    graph_review_test_judge_reconcile: true\n"
    )
    cfg = load_config(p)

    assert cfg.workflow.dag.enabled is True
    assert cfg.workflow.dag.graph_static_gate_action is True
    assert cfg.workflow.dag.graph_review_test_judge_reconcile is True


def test_load_workflow_fast_path_config(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "workflow:\n"
        "  fast_path:\n"
        "    enabled: true\n"
        "    max_scope_files: 3\n"
        "    skip_stages:\n"
        "    - design\n"
        "    - judge\n"
        "    blocked_file_globs:\n"
        "    - src/zf/runtime/**\n"
        "    blocked_keywords:\n"
        "    - security\n"
        "    verification_required: true\n"
    )
    cfg = load_config(p)

    assert cfg.workflow.fast_path.enabled is True
    assert cfg.workflow.fast_path.max_scope_files == 3
    assert cfg.workflow.fast_path.skip_stages == ["design", "judge"]
    assert cfg.workflow.fast_path.blocked_file_globs == ["src/zf/runtime/**"]
    assert cfg.workflow.fast_path.blocked_keywords == ["security"]
    assert cfg.workflow.fast_path.verification_required is True


def test_load_candidate_quality_and_acceptance_split_policy(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n  name: test\n"
        "workflow:\n"
        "  candidate_quality_source: task_contract_required\n"
        "  work_units:\n"
        "    enabled: true\n"
        "    split_quality:\n"
        "      mode: blocking\n"
        "      max_acceptance_criteria: 7\n",
        encoding="utf-8",
    )

    cfg = load_config(p)

    assert cfg.workflow.candidate_quality_source == "task_contract_required"
    assert cfg.workflow.work_units.split_quality.max_acceptance_criteria == 7


def test_validate_rejects_invalid_fast_path_skip_stage(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "workflow:\n"
        "  fast_path:\n"
        "    enabled: true\n"
        "    skip_stages:\n"
        "    - static_gate\n"
    )
    errors = validate_config(p)

    assert errors
    assert any("workflow.fast_path" in error for error in errors)


# ---------------------------------------------------------------------------
# P0-VALIDATE-LOADER-01: validate_config must route through load_config.
# Pre-fix it did a shallow check that accepted YAMLs which load_config()
# would reject (invalid tmux_layout, mismatched backends, backend/backends
# conflict, ...). Users got a green `zf validate` and a red `zf start`.
# ---------------------------------------------------------------------------


def test_validate_rejects_invalid_tmux_layout(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "session:\n  tmux_layout: impossible-layout\n"
    )
    errors = validate_config(p)
    assert errors, "validate must reject invalid tmux_layout"
    assert any("tmux_layout" in e for e in errors)


def test_validate_rejects_replicas_backends_length_mismatch(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "roles:\n"
        "  - name: dev\n"
        "    replicas: 2\n"
        "    backends:\n"
        "      - claude-code\n"
    )
    errors = validate_config(p)
    assert errors, "validate must reject len(backends) != replicas"
    assert any("replicas" in e and "backends" in e for e in errors)


def test_validate_rejects_backend_backends_conflict(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "roles:\n"
        "  - name: dev\n"
        "    backend: claude-code\n"
        "    backends:\n"
        "      - claude-code\n"
        "      - codex\n"
    )
    errors = validate_config(p)
    assert errors, "validate must reject backend + backends combo"
    assert any("backend" in e for e in errors)


def test_validate_rejects_invalid_transport(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "roles:\n"
        "  - name: dev\n"
        "    transport: carrier-pigeon\n"
    )
    errors = validate_config(p)
    assert errors, "validate must reject invalid transport"
    assert any("transport" in e.lower() for e in errors)


def test_validate_rejects_recycle_ratio_out_of_range(tmp_path: Path):
    """RoleConfig.__post_init__ raises ValueError; validate should
    surface it as a Schema error rather than letting it escape."""
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "roles:\n"
        "  - name: dev\n"
        "    recycle_threshold: 1.5\n"
    )
    errors = validate_config(p)
    assert errors, "validate must reject recycle_threshold not in (0, 1)"


def test_validate_passes_for_dev_mixed_backends_example():
    """The motivating real config — examples/dev-mixed-backends.yaml —
    must remain valid after the loader/validator merge."""
    candidate = Path(__file__).parent.parent / "examples" / "dev-mixed-backends.yaml"
    if not candidate.exists():
        pytest.skip("examples/dev-mixed-backends.yaml not present")
    errors = validate_config(candidate)
    assert errors == [], f"unexpected validate errors: {errors}"


def test_load_autopilot_config(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "autopilot:\n"
        "  enabled: true\n"
        "  mode: proposal_only\n"
        "  stale_after_hours: 12\n"
        "  failed_event_window_hours: 36\n"
        "  schedules:\n"
        "  - id: daily-triage\n"
        "    interval: 24h\n"
        "    action: triage\n"
    )

    cfg = load_config(p)

    assert cfg.autopilot.enabled is True
    assert cfg.verification.contract.quality_required is True
    assert cfg.verification.contract.rework_delta_required is True
    assert cfg.verification.contract.dispatch_token_required is True
    assert cfg.autopilot.mode == "proposal_only"
    assert cfg.autopilot.stale_after_hours == 12
    assert cfg.autopilot.failed_event_window_hours == 36
    assert cfg.autopilot.schedules[0].id == "daily-triage"


def test_validate_rejects_invalid_autopilot_mode(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "autopilot:\n"
        "  enabled: true\n"
        "  mode: free_agent\n"
    )

    errors = validate_config(p)

    assert errors
    assert "autopilot.mode" in errors[0]


def test_validate_passes_for_dev_codex_backends_example():
    """The all-Codex test preset keeps homogeneous Codex pools and splits
    design critique into an explicit critic role."""
    candidate = Path(__file__).parent.parent / "examples" / "dev-codex-backends.yaml"
    if not candidate.exists():
        pytest.skip("examples/dev-codex-backends.yaml not present")
    errors = validate_config(candidate)
    assert errors == [], f"unexpected validate errors: {errors}"
    cfg = load_config(candidate)
    # 11 roles since the example trimmed to orchestrator/arch/critic +
    # 4 dev + review + 2 test + judge (this test sat skipped while the
    # yaml lived in examples/tmp/, so the old 13 never got updated).
    assert len(cfg.roles) == 11
    assert all(role.backend == "codex" for role in cfg.roles)
    assert cfg.skill_sources[0].path == "./skills/external"
    assert cfg.skill_sources[1].path == "skills"
    assert cfg.skill_sources[2].path == "./skills/critic"
    by_instance = {role.instance_id: role for role in cfg.roles}
    assert "using-agent-skills" in by_instance["orchestrator"].skills
    assert "document-review" in by_instance["critic"].skills
    assert "plan-option-scoring" in by_instance["critic"].skills
    assert by_instance["critic"].triggers == ["arch.proposal.done"]
    assert by_instance["critic"].publishes == ["design.critique.done", "gate.failed"]
    assert "incremental-implementation" in by_instance["dev-1"].skills
    assert by_instance["dev-1"].skills == by_instance["dev-2"].skills
    assert by_instance["dev-1"].skills == by_instance["dev-4"].skills
    assert by_instance["dev-1"].triggers == ["task.assigned"]
    assert "code-review-and-quality" in by_instance["review"].skills
    assert by_instance["review"].stages == ["code_review"]
    assert by_instance["review"].triggers == ["static_gate.passed"]
    assert "browser-testing-with-devtools" in by_instance["test-1"].skills
    assert by_instance["test-1"].skills == by_instance["test-2"].skills
    assert "shipping-and-launch" in by_instance["judge"].skills
    assert "final-meta-review" in by_instance["judge"].skills
    assert cfg.autopilot.enabled is True
    assert cfg.autopilot.schedules[0].action == "triage"
    assert cfg.verification.contract.required is True
    assert cfg.quality_gates["static"].required_checks == [
        "python3 -m compileall -q src tests",
        "npm --prefix web run typecheck",
    ]
    assert cfg.quality_gates["test"].required_checks == [
        "PYTHONPATH=src pytest -q",
        "npm --prefix web test",
    ]
    assert cfg.workflow.rework_routing == {
        "gate.failed": "arch",
        "static_gate.failed": "dev",
        # B10(2026-06-12): dev 失败事件缺 route 会让 inspect STOP
        "dev.blocked": "orchestrator",
        "dev.failed": "orchestrator",
    }


def test_stage_backedge_derives_rework_routing(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n"
        "    role_kind: reader\n"
        "  - name: review\n"
        "    backend: mock\n"
        "    role_kind: reader\n"
        "  - name: verify\n"
        "    backend: mock\n"
        "    role_kind: reader\n"
        "workflow:\n"
        "  stages:\n"
        "    - id: impl\n"
        "      trigger: task.created\n"
        "      topology: fanout_reader\n"
        "      roles: [dev]\n"
        "    - id: review\n"
        "      trigger: dev.build.done\n"
        "      topology: fanout_reader\n"
        "      roles: [review]\n"
        "      on_reject:\n"
        "        event: review.rejected\n"
        "        restart_stage: impl\n"
        "        target_affinity: same_lane\n"
        "        max_attempts: 3\n"
        "        feedback_artifact: review-feedback.md\n"
        "    - id: verify\n"
        "      trigger: review.approved\n"
        "      topology: fanout_reader\n"
        "      roles: [verify]\n"
        "      on_fail:\n"
        "        event: verify.failed\n"
        "        restart_role: dev\n",
        encoding="utf-8",
    )

    cfg = load_config(p)
    review_stage = cfg.workflow.stages[1]

    assert cfg.workflow.rework_routing == {
        "review.rejected": "dev",
        "verify.failed": "dev",
    }
    assert review_stage.on_reject.restart_stage == "impl"
    assert review_stage.on_reject.target_affinity == "same_lane"
    assert review_stage.on_reject.max_attempts == 3
    assert review_stage.on_reject.feedback_artifact == "review-feedback.md"


def test_top_level_rework_routing_overrides_stage_backedge(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n"
        "    role_kind: reader\n"
        "  - name: arch\n"
        "    backend: mock\n"
        "    role_kind: reader\n"
        "  - name: review\n"
        "    backend: mock\n"
        "    role_kind: reader\n"
        "workflow:\n"
        "  rework_routing:\n"
        "    review.rejected: arch\n"
        "  stages:\n"
        "    - id: impl\n"
        "      trigger: task.created\n"
        "      topology: fanout_reader\n"
        "      roles: [dev]\n"
        "    - id: review\n"
        "      trigger: dev.build.done\n"
        "      topology: fanout_reader\n"
        "      roles: [review]\n"
        "      on_reject:\n"
        "        event: review.rejected\n"
        "        restart_stage: impl\n",
        encoding="utf-8",
    )

    cfg = load_config(p)

    assert cfg.workflow.rework_routing["review.rejected"] == "arch"


def test_lane_pipeline_runtime_rework_cannot_route_to_design_role(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: arch\n"
        "    backend: mock\n"
        "    role_kind: reader\n"
        "    stages: [design]\n"
        "  - name: dev\n"
        "    instance_id: dev-lane-0\n"
        "    backend: mock\n"
        "    role_kind: writer\n"
        "workflow:\n"
        "  affinity_lanes:\n"
        "    refactor-slot:\n"
        "      lanes:\n"
        "        - id: lane0\n"
        "          impl: dev-lane-0\n"
        "  rework_routing:\n"
        "    dev.failed: arch\n"
        "  stages:\n"
        "    - id: impl\n"
        "      trigger: task_map.ready\n"
        "      topology: fanout_writer_scoped\n"
        "      roles: [dev-lane-0]\n"
        "      source:\n"
        "        task_map: ${task_map_ref}\n"
        "      fanout:\n"
        "        assignment:\n"
        "          strategy: affinity_stage_slots\n"
        "          lane_profile: refactor-slot\n"
        "          stage_slot: impl\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="cannot route lane runtime event"):
        load_config(p)


def test_design_first_rework_can_still_route_to_arch(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: arch\n"
        "    backend: mock\n"
        "    role_kind: reader\n"
        "    stages: [design]\n"
        "workflow:\n"
        "  rework_routing:\n"
        "    gate.failed: arch\n",
        encoding="utf-8",
    )

    cfg = load_config(p)

    assert cfg.workflow.rework_routing["gate.failed"] == "arch"


def test_validate_passes_for_dev_codex_star_example():
    """The all-Codex star preset must declare a real fanout_reader stage."""
    candidate = Path(__file__).parent.parent / "examples" / "dev-codex-star.yaml"
    if not candidate.exists():
        pytest.skip("examples/dev-codex-star.yaml not present")
    errors = validate_config(candidate)
    assert errors == [], f"unexpected validate errors: {errors}"
    cfg = load_config(candidate)
    assert all(role.backend == "codex" for role in cfg.roles)
    assert all(role.role_kind == "reader" for role in cfg.roles)
    assert cfg.runtime.workdirs.enabled is True
    assert cfg.runtime.workdirs.mode == "worktree"
    stage = cfg.workflow.stages[0]
    assert stage.id == "star-review-wave"
    assert stage.trigger == "candidate.ready"
    assert stage.topology == "fanout_reader"
    assert stage.roles == [
        "review-security",
        "review-architecture",
        "review-testing",
    ]
    assert stage.aggregate.mode == "wait_for_all"


def test_validate_passes_for_current_star_mode_examples():
    """Star examples must stay aligned with the runtime loader schema."""
    examples = {
        "star-verifier-reader.yaml": {
            "id": "verify-candidate",
            "topology": "fanout_reader",
            "mode": "any_failed_fail",
            "roles": ["verify-unit", "verify-e2e", "verify-type"],
        },
        "star-critic-review-reader.yaml": {
            "id": "review-wave",
            "topology": "fanout_reader",
            "mode": "wait_for_all",
            "roles": ["review-security", "review-architecture", "review-testing"],
            "synth_role": "review-synth",
        },
        "star-supervisor-worker-writer.yaml": {
            "id": "supervisor-worker-dev-fanout",
            "topology": "fanout_writer_scoped",
            "mode": "candidate_integration",
            "roles": ["dev-auth", "dev-gateway", "dev-web"],
            "task_map": ".zf/artifacts/${pdd_id}/task_map.json",
        },
        "star-refactor-planning-reader.yaml": {
            "id": "refactor-planning-scan",
            "topology": "fanout_reader",
            "mode": "wait_for_all",
            "roles": ["scan-architecture", "scan-tests", "scan-runtime"],
            "synth_role": "refactor-plan-synth",
        },
    }
    root = Path(__file__).parent.parent / "examples"
    for filename, expected in examples.items():
        candidate = root / filename
        errors = validate_config(candidate)
        assert errors == [], f"{filename}: unexpected validate errors: {errors}"
        cfg = load_config(candidate)
        assert len(cfg.workflow.stages) == 1
        stage = cfg.workflow.stages[0]
        assert stage.id == expected["id"]
        assert stage.topology == expected["topology"]
        assert stage.aggregate.mode == expected["mode"]
        assert stage.roles == expected["roles"]
        assert stage.aggregate.synth_role == expected.get("synth_role", "")
        assert stage.task_map == expected.get("task_map", "")


def test_hermes_refactor_example_uses_product_delivery_wave_ready():
    root = Path(__file__).parent.parent / "examples"
    candidate = root / "hermes-refactor-product-delivery-wave.yaml"

    errors = validate_config(candidate)
    assert errors == [], f"unexpected validate errors: {errors}"
    cfg = load_config(candidate)

    reader = next(stage for stage in cfg.workflow.stages if stage.id == "hermes-refactor-scan")
    writer = next(stage for stage in cfg.workflow.stages if stage.id == "hermes-refactor-write")
    assert reader.topology == "fanout_reader"
    assert writer.trigger == "product_delivery.wave.ready"
    assert writer.topology == "fanout_writer_scoped"
    assert writer.task_map == "${task_map_ref}"


def test_loads_affinity_stage_slot_assignment(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    instance_id: dev0\n"
        "    backend: mock\n"
        "  - name: review\n"
        "    instance_id: review0\n"
        "    backend: mock\n"
        "  - name: test\n"
        "    instance_id: test0\n"
        "    backend: mock\n"
        "workflow:\n"
        "  affinity_lanes:\n"
        "    refactor-1:\n"
        "      affinity_key: module\n"
        "      queue:\n"
        "        order: priority_fifo\n"
        "      lanes:\n"
        "        - id: lane0\n"
        "          impl: dev0\n"
        "          review: review0\n"
        "          verify: test0\n"
        "  stages:\n"
        "    - id: dev-wave\n"
        "      trigger: task_map.ready\n"
        "      topology: fanout_writer_scoped\n"
        "      task_map: .zf/artifacts/${pdd_id}/task_map.json\n"
        "      fanout:\n"
        "        assignment:\n"
        "          strategy: affinity_stage_slots\n"
        "          lane_profile: refactor-1\n"
        "          stage_slot: impl\n"
        "      aggregate:\n"
        "        mode: candidate_integration\n"
        "        success_event: candidate.ready\n"
        "        failure_event: integration.failed\n",
        encoding="utf-8",
    )

    cfg = load_config(p)

    assert cfg.workflow.affinity_lanes["refactor-1"].affinity_key == "module"
    stage = cfg.workflow.stages[0]
    assert stage.assignment.strategy == "affinity_stage_slots"
    assert stage.assignment.lane_profile == "refactor-1"
    assert stage.assignment.stage_slot == "impl"
    assert stage.roles == ["dev0"]


def test_affinity_stage_slot_assignment_rejects_unknown_lane_profile(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    instance_id: dev0\n"
        "    backend: mock\n"
        "workflow:\n"
        "  stages:\n"
        "    - id: dev-wave\n"
        "      trigger: task_map.ready\n"
        "      topology: fanout_writer_scoped\n"
        "      task_map: .zf/artifacts/${pdd_id}/task_map.json\n"
        "      fanout:\n"
        "        assignment:\n"
        "          strategy: affinity_stage_slots\n"
        "          lane_profile: missing\n"
        "          stage_slot: impl\n"
        "      aggregate:\n"
        "        mode: candidate_integration\n"
        "        success_event: candidate.ready\n"
        "        failure_event: integration.failed\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="lane_profile"):
        load_config(p)
    assert any("lane_profile" in error for error in validate_config(p))


def test_rework_routing_rejects_combined_event_key(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: arch\n"
        "    backend: mock\n"
        "workflow:\n"
        "  rework_routing:\n"
        "    judge.child.failed,judge.failed: arch\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="exactly one event"):
        load_config(p)


def test_rework_routing_rejects_same_lane_affinity_duplicate(tmp_path: Path):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    instance_id: dev-lane-0\n"
        "    backend: mock\n"
        "  - name: verify\n"
        "    instance_id: verify-lane-0\n"
        "    backend: mock\n"
        "workflow:\n"
        "  affinity_lanes:\n"
        "    refactor-slot:\n"
        "      lanes:\n"
        "        - id: lane0\n"
        "          impl: dev-lane-0\n"
        "          verify: verify-lane-0\n"
        "  rework_routing:\n"
        "    verify.child.failed: dev-lane-0\n"
        "  stages:\n"
        "    - id: impl\n"
        "      trigger: task_map.ready\n"
        "      topology: fanout_writer_scoped\n"
        "      roles: [dev-lane-0]\n"
        "      fanout:\n"
        "        assignment:\n"
        "          strategy: affinity_stage_slots\n"
        "          lane_profile: refactor-slot\n"
        "          stage_slot: impl\n"
        "      source:\n"
        "        task_map: ${task_map_ref}\n"
        "    - id: verify\n"
        "      trigger: candidate.ready\n"
        "      topology: fanout_reader\n"
        "      roles: [verify-lane-0]\n"
        "      fanout:\n"
        "        assignment:\n"
        "          strategy: affinity_stage_slots\n"
        "          lane_profile: refactor-slot\n"
        "          stage_slot: verify\n"
        "      on_fail:\n"
        "        event: verify.child.failed\n"
        "        restart_stage: impl\n"
        "        target_affinity: same_lane\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="duplicates an affinity same-lane"):
        load_config(p)


def test_candidate_level_failure_cannot_use_same_lane_affinity_backedge(
    tmp_path: Path,
):
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    instance_id: dev-lane-0\n"
        "    backend: mock\n"
        "  - name: judge\n"
        "    instance_id: judge-refactor\n"
        "    backend: mock\n"
        "workflow:\n"
        "  affinity_lanes:\n"
        "    refactor-slot:\n"
        "      lanes:\n"
        "        - id: lane0\n"
        "          impl: dev-lane-0\n"
        "  stages:\n"
        "    - id: impl\n"
        "      trigger: task_map.ready\n"
        "      topology: fanout_writer_scoped\n"
        "      roles: [dev-lane-0]\n"
        "      source:\n"
        "        task_map: ${task_map_ref}\n"
        "      fanout:\n"
        "        assignment:\n"
        "          strategy: affinity_stage_slots\n"
        "          lane_profile: refactor-slot\n"
        "          stage_slot: impl\n"
        "    - id: judge\n"
        "      trigger: verify.passed\n"
        "      topology: fanout_reader\n"
        "      roles: [judge-refactor]\n"
        "      on_reject:\n"
        "        event: judge.failed\n"
        "        restart_stage: impl\n"
        "        target_affinity: same_lane\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="candidate-level"):
        load_config(p)


def test_validate_matches_load_config_acceptance(tmp_path: Path):
    """Backlog T1 invariant: any YAML load_config rejects, validate
    must also reject. Cross-check on a known-bad config."""
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\nproject:\n  name: test\n'
        "session:\n  tmux_layout: nope\n"
    )
    with pytest.raises(ConfigError):
        load_config(p)
    errors = validate_config(p)
    assert errors, "validate must agree with load_config rejection"


class TestUnknownKeyRejection:
    """2026-06-10 review P1-6: typo'd keys must fail-closed, not silently
    fall back to defaults (`harnes_profile:` ran the harness in baseline)."""

    def test_top_level_unknown_key_rejected(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\nproject:\n  name: test\n'
            "workfloww:\n  harness_profile: strict\n"
        )
        with pytest.raises(ConfigError, match="workfloww"):
            load_config(p)

    def test_top_level_typo_gets_did_you_mean(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\nproject:\n  name: test\nworkfloww: {}\n'
        )
        with pytest.raises(ConfigError, match="did you mean 'workflow'"):
            load_config(p)

    def test_workflow_unknown_key_rejected(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\nproject:\n  name: test\n'
            "workflow:\n  harnes_profile: strict\n"
        )
        with pytest.raises(ConfigError, match="harnes_profile"):
            load_config(p)

    def test_role_unknown_key_rejected(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\nproject:\n  name: test\n'
            "roles:\n- name: dev\n  stags: [impl]\n"
        )
        with pytest.raises(ConfigError, match="role 'dev'.*stags"):
            load_config(p)

    def test_validate_agrees_with_unknown_key_rejection(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\nproject:\n  name: test\n'
            "workflow:\n  harnes_profile: strict\n"
        )
        errors = validate_config(p)
        assert any("harnes_profile" in e for e in errors)

    def test_all_examples_still_load(self):
        examples = sorted(
            Path(__file__).resolve().parent.parent.glob("examples/*.yaml")
        )
        assert examples, "examples/ should not be empty"
        for example in examples:
            load_config(example)  # must not raise


class TestUnderscoreAnchorConvention:
    """下划线前缀键 = YAML anchor 定义区,loader 豁免(doc 90 实证需求)。"""

    def test_underscore_anchor_host_key_allowed(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            "_role_defaults: &d {backend: mock, role_kind: reader}\n"
            'version: "1.0"\nproject: {name: t}\n'
            "roles:\n- {<<: *d, name: dev, instance_id: dev}\n"
        )
        cfg = load_config(p)
        assert cfg.roles[0].backend == "mock"

    def test_non_underscore_unknown_still_rejected(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\nproject: {name: t}\nrole_defaults: {}\n'
        )
        with pytest.raises(ConfigError, match="role_defaults"):
            load_config(p)


class TestStateDirDerivedPaths:
    """W2:runtime 路径默认从 state_dir 派生(治硬编码 .zf 家族)。"""

    def test_custom_state_dir_derives_runtime_paths(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\n'
            "project: {name: t, state_dir: .zf-custom}\n"
        )
        cfg = load_config(p)
        assert cfg.runtime.workdirs.root == ".zf-custom/workdirs"
        assert cfg.runtime.skills.pool == ".zf-custom/skills"
        assert cfg.runtime.skills.lock_file == ".zf-custom/skills.lock.json"

    def test_default_state_dir_unchanged(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text('version: "1.0"\nproject: {name: t}\n')
        cfg = load_config(p)
        assert cfg.runtime.workdirs.root == ".zf/workdirs"

    def test_explicit_path_wins_over_derivation(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\n'
            "project: {name: t, state_dir: .zf-custom}\n"
            "runtime:\n  workdirs: {root: .zf-custom/my-workdirs}\n"
        )
        cfg = load_config(p)
        assert cfg.runtime.workdirs.root == ".zf-custom/my-workdirs"


class TestVersionedPreset:
    """V3:preset /vN load 期 merge(裸名保持 init 标记语义)。"""

    def test_versioned_preset_merges_as_base(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\npreset: refactor-strict/v1\n'
            "project: {name: t}\n"
        )
        cfg = load_config(p)
        assert cfg.budget_enforcement_enabled is True
        assert cfg.workflow.harness_profile == "strict"
        # P1-3: the strict preset now actually enforces the contract (required
        # was dead top-level config before).
        assert cfg.verification.contract.required is True

    def test_project_override_wins(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\npreset: refactor-strict/v1\n'
            "project: {name: t}\n"
            "workflow: {harness_profile: baseline}\n"
        )
        cfg = load_config(p)
        assert cfg.workflow.harness_profile == "baseline"  # 项目最高

    def test_unknown_versioned_preset_fails_closed(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\npreset: refactor-strict/v9\n'
            "project: {name: t}\n"
        )
        with pytest.raises(ConfigError, match="unknown versioned preset"):
            load_config(p)

    def test_bare_preset_name_stays_init_marker(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text('version: "1.0"\npreset: ln\nproject: {name: t}\n')
        cfg = load_config(p)  # 不解析不报错(零迁移)
        assert cfg.workflow.harness_profile != "strict"

    def test_registry_immutable_by_deepcopy(self):
        from zf.core.config.presets import (
            VERSIONED_PRESETS,
            resolve_versioned_preset,
        )
        got = resolve_versioned_preset("refactor-strict/v1")
        got["verification"]["contract"]["required"] = False
        assert (
            VERSIONED_PRESETS["refactor-strict/v1"]["verification"]["contract"][
                "required"
            ]
            is True
        )


class TestPublishesDerivation:
    """V1-②:特化 role publishes 从 stage 成员关系派生(填空,显式最高)。"""

    def _body(self, role_extra=""):
        return (
            'version: "1.0"\nproject: {name: t}\n'
            "roles:\n"
            f"- {{name: scanner, backend: mock, instance_id: scanner, role_kind: reader{role_extra}}}\n"
            "workflow:\n  stages:\n"
            "  - id: scan\n    trigger: go\n    topology: fanout_reader\n"
            "    roles: [scanner]\n"
            "    aggregate: {mode: wait_for_all,\n"
            "                success_event: scan.done, failure_event: scan.failed,\n"
            "                child_success_event: scan.child.completed,\n"
            "                child_failure_event: scan.child.failed}\n"
        )

    def test_empty_publishes_derived_from_stage_membership(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(self._body())
        cfg = load_config(p)
        scanner = next(r for r in cfg.roles if r.name == "scanner")
        assert scanner.publishes == ["scan.child.completed", "scan.child.failed"]

    def test_explicit_publishes_never_overridden(self, tmp_path: Path):
        p = tmp_path / "zf.yaml"
        p.write_text(self._body(role_extra=", publishes: [my.custom.done]"))
        cfg = load_config(p)
        scanner = next(r for r in cfg.roles if r.name == "scanner")
        assert scanner.publishes == ["my.custom.done"]


def test_plan_approval_strict_profile_defaults_to_hold(tmp_path: Path):
    """B-93-02 (doc93 §8): strict 缺省人审 hold,baseline 缺省直行,显式覆盖。"""
    def _load(profile: str, extra: str = "") -> bool:
        p = tmp_path / f"zf-{profile}{'x' if extra else ''}.yaml"
        p.write_text(
            'version: "1.0"\nproject:\n  name: test\n'
            f"workflow:\n  harness_profile: {profile}\n{extra}"
        )
        return load_config(p).workflow.plan_approval_enabled

    assert _load("strict") is True          # strict 缺省 → 人审 hold
    assert _load("release") is True         # release 缺省 → 人审 hold
    assert _load("baseline") is False       # baseline 缺省 → 直行
    # 显式声明覆盖 profile 默认
    assert _load("strict", "  plan_approval: false\n") is False
    assert _load("baseline", "  plan_approval: true\n") is True


def test_project_scripts_setup_parsed(tmp_path: Path):
    cfg_path = tmp_path / "zf.yaml"
    cfg_path.write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: demo\n"
        "  scripts:\n"
        "    setup: |\n"
        "      pnpm install\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert cfg.project.setup_script == "pnpm install"


def test_project_scripts_default_empty(tmp_path: Path):
    cfg_path = tmp_path / "zf.yaml"
    cfg_path.write_text(
        "version: '1.0'\nproject:\n  name: demo\n", encoding="utf-8",
    )
    assert load_config(cfg_path).project.setup_script == ""


def test_project_scripts_unknown_key_rejected(tmp_path: Path):
    cfg_path = tmp_path / "zf.yaml"
    cfg_path.write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: demo\n"
        "  scripts:\n"
        "    steup: pnpm install\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="project.scripts"):
        load_config(cfg_path)


def test_project_scripts_setup_must_be_string(tmp_path: Path):
    cfg_path = tmp_path / "zf.yaml"
    cfg_path.write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: demo\n"
        "  scripts:\n"
        "    setup: [pnpm, install]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="must be a string"):
        load_config(cfg_path)
