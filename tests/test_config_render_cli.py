"""zf config inspect/render product surface tests."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import yaml

from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.loader import load_config
from zf.cli.main import main


def _config(tmp_path):
    path = tmp_path / "zf.yaml"
    path.write_text("""\
version: "1.0"
project:
  name: render-demo
  state_dir: .zf-render-demo
roles:
- name: scan
  instance_id: scan
  backend: mock
  role_kind: reader
workflow:
  dag:
    external_triggers: [scan.requested]
  stages:
  - id: scan
    trigger: scan.requested
    topology: fanout_reader
    roles: [scan]
    aggregate:
      mode: wait_for_all
      success_event: scan.completed
      failure_event: scan.failed
""")
    return path


def _write_skill(root, name):
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: Test skill {name}. Use when testing config inspect.\n"
        "---\n\n"
        "# Test\n",
        encoding="utf-8",
    )


def test_config_inspect_json_outputs_summary(tmp_path, capsys):
    path = _config(tmp_path)
    rc = main(["config", "inspect", "--expanded", "--config", str(path), "--format", "json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == "config-inspection.v1"
    assert report["project"]["name"] == "render-demo"
    assert report["summary"]["roles"] == 1
    assert report["summary"]["stages"] == 1
    assert report["source"]["sha256"]


def test_config_render_writes_redacted_config_and_lock(tmp_path, capsys):
    path = _config(tmp_path)
    output = tmp_path / "rendered-zf.yaml"
    lock = tmp_path / "render-lock.json"
    rc = main([
        "config",
        "render",
        "--config",
        str(path),
        "--output",
        str(output),
        "--lock",
        str(lock),
    ])
    assert rc == 0
    rendered = yaml.safe_load(output.read_text())
    assert rendered["project"]["name"] == "render-demo"
    data = json.loads(lock.read_text())
    assert data["schema_version"] == "config-render-lock.v1"
    assert data["input"]["sha256"]
    assert data["output"]["sha256"]
    assert "profile_diagnostics" in data
    assert "flow_policy_diagnostics" in data
    assert "project_semantic_leakage" in data
    assert "coverage" in data
    assert "generated" in data
    assert json.loads(capsys.readouterr().out)["summary"]["stages"] == 1


def test_config_rendered_controller_yaml_is_reloadable(tmp_path, capsys):
    source = "examples/prod/controller/issue-fanout-v3.yaml"
    output = tmp_path / "issue-rendered.yaml"
    lock = tmp_path / "issue-lock.json"

    rc = main([
        "config",
        "render",
        "--config",
        source,
        "--output",
        str(output),
        "--lock",
        str(lock),
    ])

    assert rc == 0
    rendered = yaml.safe_load(output.read_text(encoding="utf-8"))
    workflow = rendered["workflow"]
    assert "plan_approval_enabled" not in workflow
    assert "flow_metadata" not in workflow
    assert "pipelines_role_meta" not in workflow
    assert "pipelines_schema_sources" not in workflow
    assert workflow["_flow_metadata"]["flow_kind"] == "issue"
    reloaded = load_config(output)
    assert reloaded.workflow.flow_metadata["flow_kind"] == "issue"
    assert json.loads(capsys.readouterr().out)["summary"]["pipelines"] == 1


def test_rendered_safety_section_is_nested_and_reloadable(tmp_path):
    # P1-3 follow-up: render must emit safety.tool_closure.enabled (nested), not
    # the flat SafetyConfig.tool_closure_enabled, so the rendered YAML round-trips
    # through the loader's fail-closed key check instead of tripping it.
    p = tmp_path / "zf.yaml"
    p.write_text(
        'version: "1.0"\n'
        "project:\n  name: t\n"
        "safety:\n  tool_closure:\n    enabled: false\n"
    )
    cfg = load_config(p)
    prim = renderable_config_to_primitive(cfg)
    assert prim["safety"] == {"tool_closure": {"enabled": False}}
    out = tmp_path / "rendered.yaml"
    out.write_text(yaml.safe_dump(prim, sort_keys=False), encoding="utf-8")
    reloaded = load_config(out)  # must not trip the unknown-key guard
    assert reloaded.safety.tool_closure_enabled is False


def test_config_rendered_controller_yaml_uses_single_execution_representation(
    tmp_path,
):
    for source in (
        Path("examples/prod/controller/prd-fanout-v3.yaml"),
        Path("examples/prod/controller/issue-fanout-v3.yaml"),
        Path("examples/prod/controller/refactor-lane-v3.yaml"),
    ):
        config = load_config(source)
        rendered = renderable_config_to_primitive(config)
        output = tmp_path / f"{source.name}.rendered.yaml"
        output.write_text(
            yaml.safe_dump(rendered, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

        assert rendered["workflow"]["pipelines"]
        assert rendered["workflow"]["_flow_metadata"]["rendered_pipeline_stages"] is True
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            reloaded = load_config(output)

        assert "dual representation drifts" not in stderr.getvalue()
        assert reloaded.workflow.stages
        assert reloaded.workflow.flow_metadata["flow_kind"] in {
            "prd",
            "issue",
            "refactor",
        }


def test_config_rendered_controller_yaml_preserves_affinity_assignment(
    tmp_path,
    capsys,
):
    cases = [
        (
            "examples/prod/controller/prd-fanout-v3.yaml",
            "prd-lanes-impl",
            "prd-lanes-verify",
        ),
        (
            "examples/prod/controller/issue-fanout-v3.yaml",
            "issue-lanes-impl",
            "issue-lanes-verify",
        ),
        (
            "examples/prod/controller/refactor-lane-v3.yaml",
            "flow-lanes-impl",
            "flow-lanes-verify",
        ),
    ]
    for source, impl_stage_id, verify_stage_id in cases:
        output = tmp_path / (source.rsplit("/", 1)[-1] + ".rendered.yaml")
        lock = tmp_path / (source.rsplit("/", 1)[-1] + ".lock.json")

        rc = main([
            "config",
            "render",
            "--config",
            source,
            "--output",
            str(output),
            "--lock",
            str(lock),
        ])

        assert rc == 0
        rendered = yaml.safe_load(output.read_text(encoding="utf-8"))
        rendered_stages = {
            stage["id"]: stage
            for stage in rendered["workflow"]["stages"]
        }
        for stage_id, slot in (
            (impl_stage_id, "impl"),
            (verify_stage_id, "verify"),
        ):
            stage = rendered_stages[stage_id]
            assert "assignment" not in stage
            assignment = stage["fanout"]["assignment"]
            assert assignment["strategy"] == "affinity_stage_slots"
            assert assignment["stage_slot"] == slot

        reloaded = load_config(output)
        reloaded_stages = {
            stage.id: stage
            for stage in reloaded.workflow.stages
        }
        for stage_id, slot in (
            (impl_stage_id, "impl"),
            (verify_stage_id, "verify"),
        ):
            stage = reloaded_stages[stage_id]
            assert stage.assignment.strategy == "affinity_stage_slots"
            assert stage.assignment.lane_profile
            assert stage.assignment.stage_slot == slot

    capsys.readouterr()


def test_config_inspect_classifies_flow_discovery_bridge_trigger(capsys):
    rc = main([
        "config",
        "inspect",
        "--expanded",
        "--config",
        "examples/prod/controller/prd-fanout-v3.yaml",
        "--format",
        "json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    expected_triggers = [
        item for item in report["diagnostics"]
        if item["kind"] == "expected_trigger_without_producer"
    ]
    assert any(
        item["event"] == "flow.discovery.requested"
        for item in expected_triggers
    )
    assert not [
        item for item in report["diagnostics"]
        if item["kind"] == "trigger_without_producer"
        and item["event"] == "flow.discovery.requested"
    ]


def test_config_render_lock_records_external_profile_sources(tmp_path, capsys):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "runtime.yaml").write_text("""\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: runtime/v1}
spec:
  runtime:
    run_manager:
      backend: codex
""")
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  profile_sources: [profiles/runtime.yaml]
  uses: [runtime/v1]
  version: "1.0"
  project: {name: render-demo}
""")
    output = tmp_path / "rendered-zf.yaml"
    lock = tmp_path / "render-lock.json"

    rc = main([
        "config",
        "render",
        "--config",
        str(path),
        "--output",
        str(output),
        "--lock",
        str(lock),
    ])

    assert rc == 0
    data = json.loads(lock.read_text())
    profile_sources = [
        source for source in data["input"]["profiles"]
        if source["kind"] == "ProfileSource"
    ]
    assert len(profile_sources) == 1
    assert profile_sources[0]["path"].endswith("profiles/runtime.yaml")
    assert profile_sources[0]["sha256"]
    assert json.loads(capsys.readouterr().out)["summary"]["profile_sources"] == 2


def test_config_inspect_reports_profile_boundary_violation(tmp_path, capsys):
    profiles = tmp_path / "profiles" / "common"
    profiles.mkdir(parents=True)
    (profiles / "runtime.yaml").write_text("""\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: runtime/v1}
spec:
  skill_sources:
    - {name: hermes-local, path: /home/user/workspace/hermes-refactor/cangjie/skills, mode: readonly}
""")
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  profile_sources: [profiles/common/*.yaml]
  uses: [runtime/v1]
  version: "1.0"
  project: {name: render-demo}
""")

    rc = main(["config", "inspect", "--expanded", "--config", str(path), "--format", "json"])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    kinds = {item["kind"] for item in report["diagnostics"]}
    assert "profile_boundary_violation" in kinds
    leakage = report["project_semantic_leakage"]
    assert leakage["schema_version"] == "project-semantic-leakage.v1"
    assert leakage["counts"]["runtime_contract_violation"] >= 1
    assert leakage["entries"][0]["category"] == "runtime_contract_violation"


def test_config_inspect_flags_current_project_name_in_common_profile(tmp_path, capsys):
    """Boundary lint tokens derive from project.name, not just the builtin
    cangjie/hermes baseline — the *current* project's semantics leaking into a
    common profile must be flagged too."""
    profiles = tmp_path / "profiles" / "common"
    profiles.mkdir(parents=True)
    (profiles / "runtime.yaml").write_text("""\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: runtime/v1}
spec:
  skill_sources:
    - {name: avbs-local, path: skills/avbs-demo-parity, mode: readonly}
""")
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  profile_sources: [profiles/common/*.yaml]
  uses: [runtime/v1]
  version: "1.0"
  project: {name: avbs-demo}
""")

    rc = main(["config", "inspect", "--expanded", "--config", str(path), "--format", "json"])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    offenders = {
        item["detail"]["offender"]
        for item in report["diagnostics"]
        if item["kind"] == "profile_boundary_violation"
    }
    assert any("avbs-demo" in offender for offender in offenders)


def test_config_inspect_ignores_other_project_names_in_common_profile(tmp_path, capsys):
    profiles = tmp_path / "profiles" / "common"
    profiles.mkdir(parents=True)
    (profiles / "runtime.yaml").write_text("""\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: runtime/v1}
spec:
  skill_sources:
    - {name: avbs-local, path: skills/avbs-demo-parity, mode: readonly}
""")
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  profile_sources: [profiles/common/*.yaml]
  uses: [runtime/v1]
  version: "1.0"
  project: {name: render-demo}
""")

    rc = main(["config", "inspect", "--expanded", "--config", str(path), "--format", "json"])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    kinds = {item["kind"] for item in report["diagnostics"]}
    assert "profile_boundary_violation" not in kinds


def test_config_inspect_allows_project_overlay_specific_paths(tmp_path, capsys):
    profiles = tmp_path / "profiles" / "project" / "demo"
    profiles.mkdir(parents=True)
    (profiles / "runtime.yaml").write_text("""\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: runtime/v1}
spec:
  skill_sources:
    - {name: hermes-local, path: /home/user/workspace/hermes-refactor/cangjie/skills, mode: readonly}
""")
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  profile_sources: [profiles/project/demo/*.yaml]
  uses: [runtime/v1]
  version: "1.0"
  project: {name: render-demo}
""")

    rc = main(["config", "inspect", "--expanded", "--config", str(path), "--format", "json"])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    kinds = {item["kind"] for item in report["diagnostics"]}
    assert "profile_boundary_violation" not in kinds
    leakage = report["project_semantic_leakage"]
    assert leakage["counts"]["adapter_skill_owned"] >= 1
    assert all(
        entry["category"] != "runtime_contract_violation"
        for entry in leakage["entries"]
    )


def test_config_inspect_reports_flow_policy_and_skill_coverage(tmp_path, capsys):
    skills = tmp_path / "skills"
    _write_skill(skills, "hermes-provider-adapter-porting")
    _write_skill(skills, "hermes-web-tui-verification")
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: RefactorFlow
metadata: {name: demo-flow}
spec:
  flowProfile: refactor-flow/v3
  lanes: 1
  assembly: none
  parityScope: [provider, webui]
  evidencePolicy: strict_refs
  environmentPolicy: real_env_required
  projectionPolicy: control_room
  roleSkillBundles:
    verify: [hermes-provider-adapter-porting, hermes-web-tui-verification]
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
  skill_sources:
    - {name: test-skills, path: skills, mode: readonly}
""")

    rc = main(["config", "inspect", "--expanded", "--config", str(path), "--format", "json"])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    kinds = {item["kind"] for item in report["diagnostics"]}
    assert "flow_policy_without_consumer" in kinds
    matrix = report["coverage"]["skill_matrix"]
    assert matrix["provider"]["covered"] is True
    assert matrix["webui"]["covered"] is True
    policy = [
        item for item in report["diagnostics"]
        if item["kind"] == "flow_policy_without_consumer"
    ]
    assert policy
    assert all(item["detail"].get("suggested_consumer") for item in policy)
    assert all(item["detail"].get("ownership") == "skill/prompt/agent-artifact" for item in policy)
    assert all(item["detail"].get("consumer_status") == "metadata_only" for item in policy)
    assert all(item.get("fix_it") for item in policy)
    by_field = {item["field"]: item for item in policy}
    assert by_field["quality_floor"]["detail"]["enforcement_status"] == "planned_consumer"
    assert "final judge gate" in by_field["quality_floor"]["detail"]["target_gates"]
    assert "verify env readiness" in by_field["environment_policy"]["detail"]["target_gates"]
    assert "deterministic consumer" in by_field["quality_floor"]["fix_it"]
    # ⑤ 续(2026-07-08)执法化:evidence_policy 已迁入 wired 消费者集
    # (loader 派生 blocking + fail_closed),不再是 metadata_only。
    assert "evidence_policy" not in by_field
    wired = {
        item["field"]: item for item in report["diagnostics"]
        if item["kind"] == "flow_policy_consumer"
    }
    assert wired["evidence_policy"]["detail"]["enforcement_status"] == "wired"
    assert "report_evidence_gate fail_closed" in (
        wired["evidence_policy"]["detail"]["target_gates"]
    )


def test_config_inspect_json_alias_and_humanized_fixit(tmp_path, capsys):
    profiles = tmp_path / "profiles" / "common"
    profiles.mkdir(parents=True)
    (profiles / "runtime.yaml").write_text("""\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: runtime/v1}
spec:
  skill_sources:
    - {name: hermes-local, path: /home/user/workspace/hermes-refactor/cangjie/skills, mode: readonly}
""")
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  profile_sources: [profiles/common/*.yaml]
  uses: [runtime/v1]
  version: "1.0"
  project: {name: render-demo}
""")

    rc = main(["config", "inspect", "--expanded", "--config", str(path), "--json"])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    diag = next(
        item for item in report["diagnostics"]
        if item["kind"] == "profile_boundary_violation"
    )
    assert diag["title"] == "Profile 分层边界不清"
    assert "profiles/project" in diag["fix_it"]


def test_config_inspect_classifies_refactor_flow_expected_event_sinks(capsys):
    rc = main([
        "config",
        "inspect",
        "--expanded",
        "--config",
        "examples/prod/controller/refactor-lane-v3.yaml",
        "--format",
        "json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    expected = [
        item for item in report["diagnostics"]
        if item["kind"] == "expected_event_without_consumer"
    ]
    assert expected
    assert any(item["event"] == "candidate.ready" for item in expected)
    assert all(item["severity"] == "INFO" for item in expected)
    matrix = report["coverage"]["skill_matrix"]
    assert matrix["provider"]["covered"] is True
    assert matrix["webui"]["covered"] is True
    assert matrix["memory"]["covered"] is True


def test_config_inspect_classifies_handwritten_parity_bridge_sinks(tmp_path, capsys):
    path = tmp_path / "zf.yaml"
    path.write_text("""\
version: "1.0"
project: {name: hand-written-refactor}
roles:
- name: verify-lane-0
  backend: mock
  instance_id: verify-lane-0
  role_kind: reader
- name: module-parity-scan
  backend: mock
  instance_id: module-parity-scan
  role_kind: reader
- name: judge-refactor
  backend: mock
  instance_id: judge-refactor
  role_kind: reader
workflow:
  dag:
    external_triggers: [candidate.ready, module.parity.closed]
  stages:
  - id: verify
    trigger: candidate.ready
    topology: fanout_reader
    roles: [verify-lane-0]
    aggregate:
      mode: wait_for_all
      child_success_event: verify.child.completed
      child_failure_event: verify.child.failed
      success_event: verify.passed
      failure_event: verify.failed
  - id: module-parity
    trigger: verify.parity_scan.requested
    topology: fanout_reader
    roles: [module-parity-scan]
    aggregate:
      mode: wait_for_all
      child_success_event: module.parity.child.completed
      child_failure_event: module.parity.child.failed
      success_event: cangjie.module.parity.scan.completed
      failure_event: cangjie.module.parity.scan.failed
  - id: judge
    trigger: module.parity.closed
    topology: fanout_reader
    roles: [judge-refactor]
    aggregate:
      mode: wait_for_all
      child_success_event: judge.child.completed
      child_failure_event: judge.child.failed
      success_event: judge.passed
      failure_event: judge.failed
""")

    rc = main([
        "config",
        "inspect",
        "--expanded",
        "--config",
        str(path),
        "--format",
        "json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    expected = {
        item["event"]
        for item in report["diagnostics"]
        if item["kind"] == "expected_event_without_consumer"
    }
    assert {"verify.passed", "cangjie.module.parity.scan.completed"} <= expected
    assert not [
        item for item in report["diagnostics"]
        if item["kind"] == "event_without_consumer"
        and item["event"] in {"verify.passed", "cangjie.module.parity.scan.completed"}
    ]


def test_config_inspect_markdown_prints_profile_sources(tmp_path, capsys):
    path = _config(tmp_path)
    rc = main(["config", "inspect", "--expanded", "--config", str(path), "--format", "md"])
    assert rc == 0
    output = capsys.readouterr().out
    assert "Profile sources" in output
