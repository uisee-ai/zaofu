"""zf flow draft/preflight tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.cli.main import main


def test_flow_draft_issue_outputs_short_issue_flow(tmp_path):
    output = tmp_path / "issue-flow.yaml"

    rc = main([
        "flow",
        "draft",
        "--kind",
        "issue",
        "--from",
        "backlogs/bug.md",
        "--backend",
        "codex",
        "--lanes",
        "2",
        "--project-name",
        "issue-demo",
        "--output",
        str(output),
    ])

    assert rc == 0
    docs = list(yaml.safe_load_all(output.read_text(encoding="utf-8")))
    assert docs[0]["kind"] == "IssueFlow"
    assert docs[0]["spec"]["issueRef"] == "backlogs/bug.md"
    assert "roleSkillBundles" not in docs[0]["spec"]
    profile = next(doc for doc in docs if doc["kind"] == "ConfigProfile")
    bundles = profile["spec"]["flow_defaults"]["issue"]["roleSkillBundles"]
    assert "zf-issue-plan-synth" in bundles["issue-triage"]
    assert "zf-harness-done-contract" in bundles["fix"]
    config_doc = next(doc for doc in docs if doc["kind"] == "ZfConfig")
    assert config_doc["spec"]["project"]["name"] == "issue-demo"
    assert config_doc["spec"]["uses"] == ["flow-draft-runtime/v1"]


def test_flow_draft_prd_embeds_executable_claude_runtime_profile(tmp_path):
    output = tmp_path / "prd-flow.yaml"

    rc = main([
        "flow",
        "draft",
        "--kind",
        "prd",
        "--from",
        "docs/prd/textstat.md",
        "--target",
        "app",
        "--backend",
        "claude-code",
        "--lanes",
        "2",
        "--project-name",
        "prd-demo",
        "--output",
        str(output),
    ])

    assert rc == 0
    docs = list(yaml.safe_load_all(output.read_text(encoding="utf-8")))
    profile = next(doc for doc in docs if doc["kind"] == "ConfigProfile")
    assert profile["metadata"]["name"] == "flow-draft-runtime/v1"
    assert profile["spec"]["runtime"]["workdirs"] == {
        "enabled": True,
        "mode": "worktree",
    }
    assert profile["spec"]["runtime"]["run_manager"]["backend"] == "claude-code"
    assert profile["spec"]["runtime"]["autoresearch_resident"] == {
        "enabled": True,
        "interval_seconds": 10,
        "max_actions_per_tick": 1,
    }
    bundles = profile["spec"]["flow_defaults"]["prd"]["roleSkillBundles"]
    assert "zf-prd-plan-synth" in bundles["planner"]
    assert "zf-harness-done-contract" in bundles["impl"]
    assert "roleSkillBundles" not in docs[0]["spec"]

    config = load_config(output)
    assert config.runtime.workdirs.enabled is True
    assert config.runtime.workdirs.mode == "worktree"
    assert config.runtime.run_manager.backend == "claude-code"
    assert config.runtime.run_manager.resident_agent.enabled is True
    assert config.runtime.autoresearch_resident.enabled is True
    planner = next(role for role in config.roles if role.name == "planner")
    dev = next(role for role in config.roles if role.name == "dev-lane-0")
    assert "zf-prd-plan-synth" in planner.skills
    assert "zf-harness-done-contract" in dev.skills


def test_flow_draft_refactor_outputs_goal_loop_defaults(capsys):
    rc = main([
        "flow",
        "draft",
        "--kind",
        "refactor",
        "--from",
        "docs/plans/refactor.md",
        "--source-root",
        "../old",
        "--target",
        "./new",
        "--backend",
        "claude-code",
        "--lanes",
        "5",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    docs = list(yaml.safe_load_all(output))
    assert docs[0]["kind"] == "RefactorFlow"
    assert docs[0]["spec"]["flowProfile"] == "refactor-flow/v3"
    assert docs[0]["spec"]["parityScope"] == ["core", "cli", "api", "web", "runtime"]
    assert docs[0]["spec"]["verifyRescan"] == "module_parity"
    assert docs[0]["spec"]["environmentPolicy"] == "real_env_required"
    assert "roleSkillBundles" not in docs[0]["spec"]
    profile = next(doc for doc in docs if doc["kind"] == "ConfigProfile")
    bundles = profile["spec"]["flow_defaults"]["refactor"]["roleSkillBundles"]
    assert "zf-verify-rescan-replan" in bundles["verify"]
    assert "zf-refactor-plan-synth" in bundles["refactor-plan-synth"]
    assert "skill_sources" not in profile["spec"]


def test_flow_intake_writes_manifest_and_json(tmp_path, capsys):
    source = tmp_path / "bug.md"
    source.write_text("修复登录按钮点击后没有响应的问题。\n", encoding="utf-8")
    intake = tmp_path / "docs" / "intake" / "bug.md"

    rc = main([
        "flow",
        "intake",
        "--kind",
        "issue",
        "--from",
        str(source),
        "--request-id",
        "wfint-test",
        "--output",
        str(intake),
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "workflow.intake.result.v1"
    assert result["request_id"] == "wfint-test"
    assert result["missing_required_fields"] == []
    manifest = tmp_path / "artifacts" / "workflow" / "wfint-test" / "workflow-input-manifest.json"
    intake_json = tmp_path / "artifacts" / "intake" / "wfint-test.json"
    assert intake.exists()
    assert manifest.exists()
    assert intake_json.exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["schema_version"] == "workflow.input_manifest.v1"
    assert data["kind"] == "issue"
    assert data["intake_ref"] == str(intake)
    assert data["intake_json_ref"] == str(intake_json)
    assert data["intake_markdown_ref"] == str(intake)
    assert (tmp_path / "artifacts" / "workflow" / "wfint-test" / "skill-adapter-plan.json").exists()
    for key in (
        "source_inventory_ref",
        "capability_matrix_ref",
        "acceptance_matrix_ref",
        "test_matrix_ref",
        "task_map_ref",
        "real_e2e_matrix_ref",
    ):
        assert key in data
        assert Path(data[key]).exists()
        matrix = json.loads(Path(data[key]).read_text(encoding="utf-8"))
        enrichment = matrix["metadata"]["enrichment_contract"]
        assert enrichment["schema_version"] == "delivery-matrix-enrichment-contract.v1"
        assert enrichment["owner"] == "project-adapter-skill"
        assert "scan" in enrichment["adapter_skill_phases"]
        assert enrichment["command_policy"]["mode"] == "declared_only"


def test_flow_intake_defaults_backend_from_project_config(tmp_path, capsys):
    (tmp_path / "zf.yaml").write_text(
        """\
version: "1.0"
project:
  name: backend-demo
roles:
  - name: dev
    backend: claude-code
""",
        encoding="utf-8",
    )
    source = tmp_path / "bug.md"
    source.write_text("修复列表命令。\n", encoding="utf-8")
    intake = tmp_path / "docs" / "intake" / "bug.md"

    rc = main([
        "flow",
        "intake",
        "--kind",
        "issue",
        "--from",
        str(source),
        "--request-id",
        "wfint-backend",
        "--output",
        str(intake),
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    manifest = json.loads(Path(result["workflow_input_manifest_ref"]).read_text(encoding="utf-8"))
    intake_payload = json.loads(Path(result["intake_json_ref"]).read_text(encoding="utf-8"))
    assert manifest["requested_backend"] == "claude-code"
    assert intake_payload["requested_backend"] == "claude-code"


def test_flow_intake_json_output_writes_canonical_json_and_display_md(tmp_path, capsys):
    source = tmp_path / "prd.md"
    source.write_text("Build a tiny CLI.\n", encoding="utf-8")
    intake = tmp_path / "artifacts" / "intake" / "tiny.json"

    rc = main([
        "flow", "intake",
        "--kind", "prd",
        "--from", str(source),
        "--request-id", "wfint-json-output",
        "--output", str(intake),
        "--target-root", "app",
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert json.loads(intake.read_text(encoding="utf-8"))["schema_version"] == "workflow.intake.v1"
    display = intake.with_suffix(".md")
    assert display.exists()
    assert display.read_text(encoding="utf-8").startswith("# Workflow Intake:")
    manifest = json.loads(Path(result["workflow_input_manifest_ref"]).read_text(encoding="utf-8"))
    assert manifest["intake_ref"] == str(intake)
    assert manifest["intake_json_ref"] == str(intake)
    assert manifest["intake_markdown_ref"] == str(display)


def test_flow_intake_prd_extracts_cli_surface_and_commands(tmp_path, capsys):
    source = tmp_path / "tiny-notes.md"
    source.write_text(
        """# Tiny Notes PRD

Build a dependency-free Node.js CLI under app/.

## Acceptance

- `npm test` passes from app/.
- `node src/index.js add "buy milk"` prints `Added: buy milk`.
- `node src/index.js list` prints `No notes yet.`.
- `node src/index.js help` mentions `add <text>` and `list`.
""",
        encoding="utf-8",
    )
    intake = tmp_path / "docs" / "intake" / "tiny-notes.md"

    rc = main([
        "flow", "intake",
        "--kind", "prd",
        "--from", str(source),
        "--request-id", "wfint-prd-cli",
        "--output", str(intake),
        "--objective", "Build Tiny Notes CLI",
        "--target-root", "app",
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    manifest = json.loads(Path(result["workflow_input_manifest_ref"]).read_text(encoding="utf-8"))
    capabilities = json.loads(Path(manifest["capability_matrix_ref"]).read_text(encoding="utf-8"))
    acceptance = json.loads(Path(manifest["acceptance_matrix_ref"]).read_text(encoding="utf-8"))
    tests = json.loads(Path(manifest["test_matrix_ref"]).read_text(encoding="utf-8"))
    e2e = json.loads(Path(manifest["real_e2e_matrix_ref"]).read_text(encoding="utf-8"))

    assert {row["surface"] for row in capabilities["capabilities"]} == {"product", "cli"}
    assert "web" not in {row["surface"] for row in capabilities["capabilities"]}
    criteria = [row["criteria"] for row in acceptance["acceptance"]]
    assert len(criteria) == 4
    assert any("npm test" in item for item in criteria)
    assert any("add \"buy milk\"" in item for item in criteria)
    assert any("node src/index.js list" in item for item in criteria)
    assert any("node src/index.js help" in item for item in criteria)
    cli_tests = [row for row in tests["tests"] if row["capability_id"] == "prd-cli"]
    assert cli_tests and "npm test" in cli_tests[0]["commands"]
    assert e2e["rows"]
    assert e2e["rows"][0]["surface"] == "cli"
    assert e2e["rows"][0]["command_source"] == "source_prd"
    assert "npm test" in e2e["rows"][0]["command"]
    assert "node src/index.js list" in e2e["rows"][0]["command"]


def test_flow_intake_delivery_matrix_draft_passes_contract_gate(tmp_path, capsys):
    intake = tmp_path / "docs" / "intake" / "refactor.md"

    rc = main([
        "flow",
        "intake",
        "--kind",
        "refactor",
        "--objective",
        "重构目标项目并保持 CLI 与 Web parity。",
        "--target",
        "target",
        "--parity-scope",
        "cli,web",
        "--request-id",
        "wfint-matrix",
        "--output",
        str(intake),
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    manifest = json.loads(Path(result["workflow_input_manifest_ref"]).read_text(encoding="utf-8"))
    from zf.runtime.delivery_contract_gate import evaluate_delivery_contract

    gate = evaluate_delivery_contract(tmp_path, {
        "capability_matrix_paths": [manifest["capability_matrix_ref"]],
        "acceptance_matrix_paths": [manifest["acceptance_matrix_ref"]],
        "test_matrix_paths": [manifest["test_matrix_ref"]],
        "task_map_paths": [manifest["task_map_ref"]],
    })

    assert gate.passed is True
    real_e2e = json.loads(Path(manifest["real_e2e_matrix_ref"]).read_text(encoding="utf-8"))
    assert {row["surface"] for row in real_e2e["rows"]} == {"cli", "web"}
    assert all(row["command_required"] is True for row in real_e2e["rows"])
    assert {row["command_source"] for row in real_e2e["rows"]} == {"project-adapter-skill"}


def test_flow_intake_refactor_records_adapter_skill_plan(tmp_path, capsys):
    skill = tmp_path / "skills" / "cangjie-hermes-parity-gate" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: cangjie-hermes-parity-gate\n"
        "description: Cangjie parity gate\n"
        "---\n"
        "\n"
        "Verify Cangjie against Hermes.\n",
        encoding="utf-8",
    )
    source = tmp_path / "prompt.md"
    source.write_text("重构 Hermes 为 Cangjie，保持 provider/webui parity。\n", encoding="utf-8")
    intake = tmp_path / "docs" / "intake" / "refactor.md"

    rc = main([
        "flow",
        "intake",
        "--kind",
        "refactor",
        "--from",
        str(source),
        "--source-root",
        str(tmp_path / "hermes"),
        "--target",
        str(tmp_path / "cangjie"),
        "--project-name",
        "cangjie",
        "--strictness",
        "full-parity",
        "--parity-scope",
        "provider,webui",
        "--request-id",
        "wfint-refactor",
        "--output",
        str(intake),
        "--json",
    ])

    assert rc == 0
    capsys.readouterr()
    plan = json.loads(
        (tmp_path / "artifacts" / "workflow" / "wfint-refactor" / "skill-adapter-plan.json")
        .read_text(encoding="utf-8")
    )
    assert plan["schema_version"] == "skill.adapter.plan.v2"
    # 2026-07-08 controller 同步:zf-dynamic-artifact-gate 入驻 canonical 后
    # recommended 全解析——旧断言钉的是幽灵缺失导致的 WARN,现应 PASS。
    assert plan["status"] == "PASS"
    assert plan["missing_required_skills"] == []
    assert plan["missing_recommended_skills"] == []
    assert "cangjie-hermes-parity-gate" in {
        item["name"] for item in plan["discovered_project_skills"]
    }
    assert "cangjie-hermes-parity-gate" in plan["roleSkillBundles"]["verify"]


def test_flow_classify_updates_manifest_and_writes_intent(tmp_path, capsys):
    source = tmp_path / "prd.md"
    source.write_text("构建一个 todo 产品，包含新增和完成任务。\n", encoding="utf-8")
    intake = tmp_path / "docs" / "intake" / "todo.md"

    assert main([
        "flow", "intake",
        "--kind", "auto",
        "--from", str(source),
        "--target", "app",
        "--request-id", "wfint-prd",
        "--output", str(intake),
    ]) == 0
    capsys.readouterr()

    rc = main([
        "flow",
        "classify",
        "--intake",
        str(intake),
        "--json",
    ])

    assert rc == 0
    intent = json.loads(capsys.readouterr().out)
    assert intent["schema_version"] == "workflow.intent.v1"
    assert intent["kind"] == "prd"
    assert intent["next_action"] == "draft"
    intent_path = tmp_path / "artifacts" / "workflow" / "wfint-prd" / "workflow-intent.json"
    manifest_path = tmp_path / "artifacts" / "workflow" / "wfint-prd" / "workflow-input-manifest.json"
    assert intent_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["intent_ref"] == str(intent_path)


def test_flow_preflight_json_reports_config_inspection(tmp_path, capsys):
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-demo}
spec:
  lanes: 1
  backend: mock
  issueRef: backlogs/bug.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
""")

    rc = main([
        "flow",
        "preflight",
        "--config",
        str(path),
        "--kind",
        "issue",
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == "flow-start-readiness.v1"
    assert report["flow_kind"] == "issue"
    assert report["summary"]["roles"] == 5


def test_flow_preflight_with_intake_reports_manifest(tmp_path, capsys):
    source = tmp_path / "bug.md"
    source.write_text("fix checkout regression\n", encoding="utf-8")
    intake = tmp_path / "docs" / "intake" / "bug.md"
    assert main([
        "flow", "intake",
        "--kind", "issue",
        "--from", str(source),
        "--request-id", "wfint-check",
        "--output", str(intake),
    ]) == 0
    capsys.readouterr()
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-demo}
spec:
  lanes: 1
  backend: mock
  issueRef: docs/intake/bug.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
""")

    rc = main([
        "flow",
        "preflight",
        "--config",
        str(path),
        "--kind",
        "issue",
        "--intake",
        str(intake),
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["intake"]["status"] == "PASS"
    assert report["intake"]["request_id"] == "wfint-check"


def test_flow_preflight_respects_skill_adapter_strictness(tmp_path, capsys):
    source = tmp_path / "prompt.md"
    source.write_text("重构 Hermes 为 Cangjie。\n", encoding="utf-8")
    intake = tmp_path / "docs" / "intake" / "refactor.md"
    assert main([
        "flow", "intake",
        "--kind", "refactor",
        "--from", str(source),
        "--source-root", str(tmp_path / "hermes"),
        "--target", str(tmp_path / "cangjie"),
        "--project-name", "cangjie",
        "--strictness", "full-parity",
        "--request-id", "wfint-strict",
        "--output", str(intake),
    ]) == 0
    capsys.readouterr()
    config = tmp_path / "zf.yaml"
    assert main([
        "flow",
        "draft",
        "--kind",
        "refactor",
        "--from",
        str(source),
        "--source-root",
        str(tmp_path / "hermes"),
        "--target",
        str(tmp_path / "cangjie"),
        "--project-name",
        "cangjie",
        "--backend",
        "mock",
        "--output",
        str(config),
    ]) == 0
    capsys.readouterr()

    rc = main([
        "flow",
        "preflight",
        "--config",
        str(config),
        "--kind",
        "refactor",
        "--intake",
        str(intake),
        "--allow-missing-env",
        "--json",
    ])

    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["skill_adapter"]["status"] == "STOP"
    assert any(
        item["kind"] == "project_adapter_skill_missing"
        for item in report["blockers"]
    )


def test_flow_preflight_blocks_missing_delivery_contract(tmp_path, capsys):
    source = tmp_path / "prompt.md"
    source.write_text("重构 source 到 target,保持完整 parity。\n", encoding="utf-8")
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    _git(source_root, "init")
    _git(target_root, "init")
    skill = tmp_path / "skills" / "generic-project-adapter" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: generic-project-adapter\ndescription: generic adapter\n---\n",
        encoding="utf-8",
    )
    intake = target_root / "docs" / "intake" / "refactor.md"
    assert main([
        "flow",
        "intake",
        "--kind",
        "refactor",
        "--from",
        str(source),
        "--source-root",
        str(source_root),
        "--target",
        str(target_root),
        "--project-name",
        "target",
        "--strictness",
        "full-parity",
        "--request-id",
        "wfint-delivery",
        "--output",
        str(intake),
    ]) == 0
    capsys.readouterr()
    config = _draft_refactor_config(tmp_path, source_root, target_root)
    capsys.readouterr()

    initial = _preflight_report(config, capsys, "--intake", str(intake))
    assert initial["delivery_contract"]["status"] == "PASS"
    manifest_path = (
        target_root
        / "artifacts"
        / "workflow"
        / "wfint-delivery"
        / "workflow-input-manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    Path(manifest["source_inventory_ref"]).unlink()

    report = _preflight_report(config, capsys, "--intake", str(intake))

    assert report["delivery_contract"]["status"] == "STOP"
    assert any(
        item["kind"] == "delivery_contract_artifact_missing"
        and item["artifact_name"] == "source_inventory"
        for item in report["blockers"]
    )


def test_flow_start_dry_run_writes_safe_unique_proposal(tmp_path, capsys):
    output = tmp_path / "issue-start.yaml"

    rc = main([
        "flow",
        "start",
        "--dry-run",
        "--kind",
        "issue",
        "--from",
        "backlogs/bug.md",
        "--backend",
        "mock",
        "--project-name",
        "issue-start-demo",
        "--output",
        str(output),
        "--json",
    ])

    assert rc == 0
    proposal = json.loads(capsys.readouterr().out)
    assert proposal["schema_version"] == "flow-start-proposal.v1"
    assert proposal["kind"] == "issue"
    assert proposal["project"]["name"] == "issue-start-demo"
    assert proposal["project"]["state_dir"] == ".zf-issue-start-demo"
    assert proposal["summary"]["roles"] == 7
    assert proposal["policies"]["quality_floor"] == "issue-regression"
    assert output.exists()


def test_flow_submit_dry_run_writes_preview_without_applying(tmp_path, capsys):
    source = tmp_path / "bug.md"
    source.write_text("fix checkout regression\n", encoding="utf-8")
    intake = tmp_path / "docs" / "intake" / "bug.md"
    assert main([
        "flow", "intake",
        "--kind", "issue",
        "--from", str(source),
        "--request-id", "wfint-submit",
        "--output", str(intake),
    ]) == 0
    capsys.readouterr()
    config = tmp_path / "zf.yaml"
    config.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-demo}
spec:
  lanes: 1
  backend: mock
  issueRef: docs/intake/bug.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
""")

    rc = main([
        "flow",
        "submit",
        "--dry-run",
        "--config",
        str(config),
        "--intake",
        str(intake),
        "--json",
    ])

    assert rc == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["schema_version"] == "workflow.submit.preview.v1"
    assert preview["dry_run"] is True
    assert preview["event_type"] == "workflow.submit.requested"
    assert preview["payload"]["workflow_prompt_ref"].endswith("artifacts/intake/wfint-submit.json")
    artifact_refs = preview["payload"]["artifact_refs"]
    assert any(ref.endswith("workflow-input-manifest.json") for ref in artifact_refs)
    assert any(ref.endswith("acceptance-matrix.json") for ref in artifact_refs)
    assert any(ref.endswith("test-matrix.json") for ref in artifact_refs)
    assert any(ref.endswith("task-map.json") for ref in artifact_refs)
    assert preview["payload"]["acceptance_matrix_ref"].endswith("acceptance-matrix.json")
    assert preview["payload"]["source_refs"]["acceptance_matrix_ref"].endswith("acceptance-matrix.json")
    preview_path = tmp_path / "artifacts" / "workflow" / "wfint-submit" / "workflow-submit-preview.json"
    preflight_path = tmp_path / "artifacts" / "workflow" / "wfint-submit" / "workflow-preflight.json"
    assert preview_path.exists()
    assert preflight_path.exists()


def test_flow_submit_apply_emits_submit_and_invoke_events(tmp_path, capsys):
    source = tmp_path / "bug.md"
    source.write_text("fix checkout regression\n", encoding="utf-8")
    intake = tmp_path / "docs" / "intake" / "bug.md"
    assert main([
        "flow", "intake",
        "--kind", "issue",
        "--from", str(source),
        "--request-id", "wfint-apply",
        "--output", str(intake),
    ]) == 0
    capsys.readouterr()
    config = tmp_path / "zf.yaml"
    config.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-demo}
spec:
  lanes: 1
  backend: mock
  issueRef: docs/intake/bug.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo, state_dir: .zf-apply}
""")

    rc = main([
        "flow",
        "submit",
        "--apply",
        "--config",
        str(config),
        "--intake",
        str(intake),
        "--task-id",
        "TASK-WF",
        "--pattern-id",
        "issue-triage",
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "workflow.submit.apply.v1"
    assert result["status"] == "accepted"
    assert result["workflow_invoke_status"] == "pending_consumer"
    assert "zf start" in result["next_action"]
    events = EventLog(tmp_path / ".zf-apply" / "events.jsonl").read_all()
    types = [event.type for event in events]
    assert "workflow.submit.requested" in types
    assert "workflow.submit.accepted" in types
    assert "workflow.invoke.requested" in types
    invoke = next(event for event in events if event.type == "workflow.invoke.requested")
    assert invoke.payload["workflow_input_manifest_ref"].endswith("workflow-input-manifest.json")
    assert invoke.payload["workflow_prompt_ref"].endswith("artifacts/intake/wfint-apply.json")


def test_flow_intake_feat_aliases_to_prd(tmp_path, capsys):
    source = tmp_path / "feat.md"
    source.write_text("新增导出按钮\n", encoding="utf-8")
    intake = tmp_path / "docs" / "intake" / "feat.md"

    rc = main([
        "flow", "intake", "--kind", "feat", "--from", str(source),
        "--request-id", "wfint-feat", "--output", str(intake), "--json",
        "--objective", "新增导出按钮", "--target-root", "app",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["request_kind"] == "feat"
    assert result["effective_kind"] == "prd"
    manifest = json.loads(
        (tmp_path / "artifacts" / "workflow" / "wfint-feat" / "workflow-input-manifest.json")
        .read_text(encoding="utf-8")
    )
    assert manifest["request_kind"] == "feat"
    assert manifest["kind"] == "prd"


def test_flow_submit_routes_multiple_kinds_in_one_canonical_config(tmp_path, capsys):
    config = _write_multi_kind_route_config(tmp_path, with_routes=True)
    cases = [
        ("issue", "wfint-issue", "issue-triage"),
        ("prd", "wfint-prd", "prd-scan"),
        ("refactor", "wfint-refactor", "refactor-scan"),
        ("feat", "wfint-feat-route", "prd-scan"),
    ]

    for kind, request_id, expected_pattern in cases:
        intake = tmp_path / "docs" / "intake" / f"{request_id}.md"
        args = [
            "flow", "intake", "--kind", kind, "--from", str(tmp_path / f"{kind}.md"),
            "--request-id", request_id, "--output", str(intake),
            "--objective", f"{kind} objective", "--target-root", "app",
        ]
        if kind == "refactor":
            args.extend(["--source-root", "src"])
        assert main(args) == 0
        capsys.readouterr()

        rc = main([
            "flow", "submit", "--dry-run", "--config", str(config),
            "--intake", str(intake), "--kind", kind, "--task-id", f"TASK-{request_id}",
            "--allow-missing-env", "--json",
        ])

        assert rc == 1
        result = json.loads(capsys.readouterr().out)
        assert result["payload"]["pattern_id"] == expected_pattern
        assert result["payload"]["request_id"] == request_id
        assert result["payload"]["run_id"] == request_id
        assert result["payload"]["kind"] == ("prd" if kind == "feat" else kind)
        assert not any(
            item["kind"] == "workflow_route_unresolved"
            for item in result["blockers"]
        )


def test_flow_submit_multi_stage_without_route_fails_closed(tmp_path, capsys):
    source = tmp_path / "bug.md"
    source.write_text("修复登录错误\n", encoding="utf-8")
    intake = tmp_path / "docs" / "intake" / "bug.md"
    assert main([
        "flow", "intake", "--kind", "issue", "--from", str(source),
        "--request-id", "wfint-no-route", "--output", str(intake),
    ]) == 0
    capsys.readouterr()
    config = _write_multi_kind_route_config(tmp_path, with_routes=False)

    rc = main([
        "flow", "submit", "--dry-run", "--config", str(config),
        "--intake", str(intake), "--kind", "issue", "--json",
    ])

    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "STOP"
    assert result["payload"]["pattern_id"] == ""
    assert any(item["kind"] == "workflow_route_unresolved" for item in result["blockers"])


def _write_multi_kind_route_config(tmp_path: Path, *, with_routes: bool) -> Path:
    route_block = """\
  kind_routes:
    issue:
      pattern_id: issue-triage
    prd:
      pattern_id: prd-scan
    refactor:
      pattern_id: refactor-scan
    feat:
      alias: prd
"""
    config = tmp_path / ("zf-routes.yaml" if with_routes else "zf-no-routes.yaml")
    config.write_text(f"""\
version: "1.0"
project:
  name: multikind
  state_dir: .zf-multikind
roles:
  - name: issue-reader
    backend: mock
    role_kind: reader
  - name: prd-reader
    backend: mock
    role_kind: reader
  - name: refactor-reader
    backend: mock
    role_kind: reader
workflow:
{route_block if with_routes else ""}
  stages:
    - id: issue-triage
      trigger: issue.requested
      topology: fanout_reader
      roles: [issue-reader]
    - id: prd-scan
      trigger: prd.requested
      topology: fanout_reader
      roles: [prd-reader]
    - id: refactor-scan
      trigger: refactor.requested
      topology: fanout_reader
      roles: [refactor-reader]
""", encoding="utf-8")
    return config


def test_flow_submit_apply_light_topology_skips_invoke(tmp_path, capsys):
    """LB-2: light submit must NOT emit the bootstrap invoke — it would
    direct-dispatch the whole objective to the judge role (dead path)."""
    source = tmp_path / "prd.md"
    source.write_text("deliver mdtoc CLI\n", encoding="utf-8")
    intake = tmp_path / "docs" / "intake" / "prd.md"
    assert main([
        "flow", "intake", "--kind", "prd", "--from", str(source),
        "--request-id", "wfint-light", "--output", str(intake),
        "--objective", "deliver mdtoc CLI",
        "--source-root", "docs", "--target-root", "app",
    ]) == 0
    capsys.readouterr()
    config = tmp_path / "zf.yaml"
    config.write_text("""\
apiVersion: zaofu.dev/v1
kind: PrdFlow
metadata: {name: prd-light-demo}
spec:
  topology: light
  lanes: 1
  backend: mock
  prdRef: docs/intake/prd.md
  targetRoot: app
  roleSkillBundles:
    impl: []
    verify: []
    judge-prd: []
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo, state_dir: .zf-light-apply}
""")

    rc = main([
        "flow", "submit", "--apply", "--config", str(config),
        "--intake", str(intake), "--task-id", "TASK-LIGHT",
        "--pattern-id", "prd-lanes-impl", "--json",
    ])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "accepted"
    assert result["workflow_invoke_status"] == "skipped_light"
    assert "prd.requested" in result["next_action"]
    types = [e.type for e in EventLog(tmp_path / ".zf-light-apply" / "events.jsonl").read_all()]
    assert "workflow.submit.accepted" in types
    assert "workflow.invoke.requested" not in types


def test_flow_submit_apply_issue_light_uses_issue_entry_trigger(tmp_path, capsys):
    source = tmp_path / "bug.md"
    source.write_text("登录按钮请求返回 500，需要回归测试。\n", encoding="utf-8")
    intake = tmp_path / "docs" / "intake" / "bug.md"
    assert main([
        "flow", "intake", "--kind", "issue", "--from", str(source),
        "--request-id", "wfint-issue-light", "--output", str(intake),
        "--objective", "修复登录按钮 500",
        "--target-root", "app",
    ]) == 0
    capsys.readouterr()
    config = tmp_path / "zf.yaml"
    config.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-light-demo}
spec:
  topology: light
  lanes: 1
  backend: mock
  issueRef: docs/intake/bug.md
  targetRoot: app
  roleSkillBundles:
    impl: []
    verify: []
    judge-issue: []
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo, state_dir: .zf-issue-light-apply}
""")

    rc = main([
        "flow", "submit", "--apply", "--config", str(config),
        "--intake", str(intake), "--kind", "issue",
        "--task-id", "TASK-ISSUE-LIGHT",
        "--pattern-id", "issue-lanes-impl", "--json",
    ])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "accepted"
    assert result["workflow_invoke_status"] == "skipped_light"
    assert "issue.requested" in result["next_action"]
    assert "prd.requested" not in result["next_action"]
    types = [
        e.type
        for e in EventLog(tmp_path / ".zf-issue-light-apply" / "events.jsonl").read_all()
    ]
    assert "workflow.submit.accepted" in types
    assert "workflow.invoke.requested" not in types


def test_project_init_creates_flow_project(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / ".zaofu-workspace"))
    root = tmp_path / "issue-project"

    rc = main([
        "project",
        "init",
        "--kind",
        "issue",
        "--name",
        "issue-project",
        "--root",
        str(root),
        "--from",
        "docs/intake/bug.md",
        "--backend",
        "mock",
        "--create",
        "--force",
        "--no-workspace-register",
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["kind"] == "issue"
    assert (root / "zf.yaml").exists()
    assert (root / ".zf-issue-project" / "session.yaml").exists()
    docs = list(yaml.safe_load_all((root / "zf.yaml").read_text(encoding="utf-8")))
    assert docs[0]["kind"] == "IssueFlow"
    config = load_config(root / "zf.yaml")
    assert config.session.tmux_session == "zf-issue-project"


def test_project_init_prd_git_init_and_greenfield_seed(
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / ".zaofu-workspace"))
    root = tmp_path / "notes-prd"

    rc = main([
        "project",
        "init",
        "--kind",
        "prd",
        "--name",
        "notes-prd",
        "--root",
        str(root),
        "--from",
        "docs/prd/notes.md",
        "--target",
        "app",
        "--backend",
        "mock",
        "--create",
        "--git-init",
        "--force",
        "--no-workspace-register",
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["kind"] == "prd"
    assert (root / ".git").exists()
    assert (root / "README.md").exists()
    assert (root / "src" / ".gitkeep").exists()
    assert (root / "tests" / ".gitkeep").exists()
    assert (root / "app" / ".gitkeep").exists()
    config = load_config(root / "zf.yaml")
    assert config.session.tmux_session == "zf-notes-prd"


def _git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        capture_output=True, text=True, timeout=30, check=True,
    )


def _draft_refactor_config(tmp_path, source, target):
    prompt = tmp_path / "refactor.md"
    prompt.write_text("rebuild target from source\n", encoding="utf-8")
    assert main([
        "flow", "draft",
        "--kind", "refactor",
        "--from", str(prompt),
        "--source-root", str(source),
        "--target", str(target),
        "--backend", "mock",
        "--project-name", "refactor-demo",
        "--output", str(target / "zf.yaml"),
    ]) == 0
    return target / "zf.yaml"


def _preflight_report(config, capsys, *extra):
    assert main([
        "flow", "preflight",
        "--config", str(config),
        "--allow-missing-env",
        "--json",
        *extra,
    ]) in (0, 1)
    return json.loads(capsys.readouterr().out)


def test_flow_preflight_refactor_target_git_and_overlap_guards(tmp_path, capsys):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    _git(source, "init")
    config = _draft_refactor_config(tmp_path, source, target)
    capsys.readouterr()

    report = _preflight_report(config, capsys)
    kinds = {item["kind"] for item in report["blockers"]}
    assert "workflow_target_not_git" in kinds
    assert report["refactor_safety"]["status"] == "STOP"

    _git(target, "init")
    report = _preflight_report(config, capsys)
    kinds = {item["kind"] for item in report["blockers"]}
    assert "workflow_target_not_git" not in kinds
    assert "workflow_source_target_overlap" not in kinds

    nested_config = _draft_refactor_config(tmp_path, target / "old", target)
    capsys.readouterr()
    report = _preflight_report(nested_config, capsys)
    kinds = {item["kind"] for item in report["blockers"]}
    assert "workflow_source_target_overlap" in kinds or (
        "workflow_source_root_not_found" in kinds
    )


def test_flow_preflight_refactor_source_baseline_drift_stops(tmp_path, capsys):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    _git(source, "init")
    (source / "a.txt").write_text("a\n", encoding="utf-8")
    _git(source, "add", "a.txt")
    _git(source, "commit", "-m", "seed")
    _git(target, "init")
    config = _draft_refactor_config(tmp_path, source, target)
    intake = target / "docs" / "intake" / "refactor.md"
    assert main([
        "flow", "intake",
        "--kind", "refactor",
        "--from", str(tmp_path / "refactor.md"),
        "--source-root", str(source),
        "--target", str(target),
        "--request-id", "wfint-drift",
        "--output", str(intake),
    ]) == 0
    capsys.readouterr()

    report = _preflight_report(config, capsys, "--intake", str(intake))
    safety = report["refactor_safety"]
    assert safety["status"] in {"PASS", "WARN"}
    assert Path(safety["source_baseline_ref"]).exists()

    (source / "b.txt").write_text("drift\n", encoding="utf-8")
    report = _preflight_report(config, capsys, "--intake", str(intake))
    kinds = {item["kind"] for item in report["blockers"]}
    assert "workflow_source_root_modified" in kinds


def test_flow_preflight_refactor_uses_intake_roots_when_yaml_is_thin(
    tmp_path,
    capsys,
):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    _git(source, "init")
    (source / "a.txt").write_text("a\n", encoding="utf-8")
    _git(source, "add", "a.txt")
    _git(source, "commit", "-m", "seed")
    _git(target, "init")
    config = _draft_refactor_config(tmp_path, source, target)
    docs = list(yaml.safe_load_all(config.read_text(encoding="utf-8")))
    docs[0]["spec"].pop("sourceRoot", None)
    docs[0]["spec"].pop("targetRoot", None)
    config.write_text(
        yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    capsys.readouterr()
    intake = target / "docs" / "intake" / "refactor.md"
    assert main([
        "flow", "intake",
        "--kind", "refactor",
        "--from", str(tmp_path / "refactor.md"),
        "--source-root", str(source),
        "--target", str(target),
        "--request-id", "wfint-thin-roots",
        "--output", str(intake),
    ]) == 0
    capsys.readouterr()

    without_intake = _preflight_report(config, capsys)
    assert any(
        item["kind"] == "workflow_source_root_undeclared"
        for item in without_intake["blockers"]
    )

    report = _preflight_report(config, capsys, "--intake", str(intake))
    kinds = {item["kind"] for item in report["blockers"]}
    assert "workflow_source_root_undeclared" not in kinds
    assert report["effective_flow_metadata"]["source_root"] == str(source)
    assert report["effective_flow_metadata"]["target_root"] == str(target)
    assert report["refactor_safety"]["source_root"] == str(source)
    assert report["refactor_safety"]["target_root"] == str(target)


def test_flow_preflight_refactor_allows_project_git_target_subdir(
    tmp_path,
    capsys,
):
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    (project / "docs" / "plans").mkdir(parents=True)
    (project / "docs" / "plans" / "refactor.md").write_text(
        "move legacy math into modern while preserving CLI behavior\n",
        encoding="utf-8",
    )
    (project / "legacy" / "src").mkdir(parents=True)
    (project / "legacy" / "src" / "math.js").write_text(
        "exports.add=(a,b)=>a+b\n",
        encoding="utf-8",
    )
    assert main([
        "flow", "draft",
        "--kind", "refactor",
        "--from", "docs/plans/refactor.md",
        "--source-root", "legacy",
        "--target", "modern",
        "--backend", "mock",
        "--project-name", "refactor-subdir",
        "--output", str(project / "zf.yaml"),
    ]) == 0
    capsys.readouterr()
    intake = project / "artifacts" / "intake" / "refactor.json"
    assert main([
        "flow", "intake",
        "--kind", "refactor",
        "--from", str(project / "docs" / "plans" / "refactor.md"),
        "--source-root", "legacy",
        "--target", "modern",
        "--request-id", "wfint-subdir-target",
        "--output", str(intake),
    ]) == 0
    capsys.readouterr()

    report = _preflight_report(
        project / "zf.yaml",
        capsys,
        "--kind", "refactor",
        "--intake", str(intake),
    )
    kinds = {item["kind"] for item in report["blockers"]}
    assert "workflow_target_not_git" not in kinds
    assert report["refactor_safety"]["target_root"] == str(project / "modern")
    assert report["refactor_safety"]["target_git_root"] == str(project)


def test_project_init_refactor_requires_source_and_git(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / ".zaofu-workspace"))
    source = tmp_path / "hermes"
    source.mkdir()
    _git(source, "init")
    root = tmp_path / "cangjie"

    base_args = [
        "project", "init",
        "--kind", "refactor",
        "--name", "cangjie",
        "--root", str(root),
        "--backend", "mock",
        "--create",
        "--no-workspace-register",
        "--json",
    ]

    assert main(base_args) == 1
    assert "source_root" in capsys.readouterr().err

    assert main([*base_args, "--source-root", str(source)]) == 1
    assert "git" in capsys.readouterr().err

    rc = main([*base_args, "--source-root", str(source), "--git-init"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["kind"] == "refactor"
    assert (root / ".git").exists()
    docs = list(yaml.safe_load_all((root / "zf.yaml").read_text(encoding="utf-8")))
    assert docs[0]["kind"] == "RefactorFlow"
