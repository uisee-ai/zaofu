from __future__ import annotations

import json

from zf.core.config.loader import load_config
from zf.runtime.run_contract import (
    build_run_contract,
    evaluate_run_contract_resume_policy,
    evaluate_run_contract_submit_binding,
    load_run_contract,
    run_contract_drift_diagnostics,
    write_run_contract,
)


def test_run_contract_records_config_and_detects_drift(tmp_path):
    config_path = tmp_path / "zf.yaml"
    config_path.write_text("""\
version: "1.0"
project: {name: demo, state_dir: .zf-demo}
roles: []
workflow: {}
""", encoding="utf-8")
    config = load_config(config_path)
    state_dir = tmp_path / ".zf-demo"

    contract = build_run_contract(
        config,
        config_path=config_path,
        project_root=tmp_path,
        state_dir=state_dir,
    )
    path = write_run_contract(state_dir, contract)

    assert path.exists()
    assert load_run_contract(state_dir)["contract_digest"] == contract["contract_digest"]

    config_path.write_text("""\
version: "1.0"
project: {name: demo, state_dir: .zf-demo}
roles: []
workflow:
  dag: {external_triggers: [demo.requested]}
""", encoding="utf-8")
    changed = build_run_contract(
        load_config(config_path),
        config_path=config_path,
        project_root=tmp_path,
        state_dir=state_dir,
    )
    diagnostics = run_contract_drift_diagnostics(contract, changed)

    assert diagnostics
    assert diagnostics[0]["kind"] == "run_contract_drift"
    assert diagnostics[0]["severity"] == "WARN"


def test_run_contract_includes_manifest_skill_digests(tmp_path):
    skill = tmp_path / "artifacts" / "workflow" / "wf" / "skill-adapter-plan.json"
    skill.parent.mkdir(parents=True)
    skill.write_text(json.dumps({"schema_version": "skill.adapter.plan.v2"}), encoding="utf-8")
    manifest = skill.parent / "workflow-input-manifest.json"
    manifest.write_text(
        json.dumps({
            "schema_version": "workflow.input_manifest.v1",
            "kind": "refactor",
            "strictness": "full-parity",
            "skill_adapter_plan_ref": str(skill),
        }),
        encoding="utf-8",
    )
    config_path = tmp_path / "zf.yaml"
    config_path.write_text("""\
apiVersion: zaofu.dev/v1
kind: RefactorFlow
spec:
  flowProfile: refactor-flow/v3
  lanes: 1
  assembly: none
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  version: "1.0"
  project: {name: demo, state_dir: .zf-demo}
""", encoding="utf-8")

    contract = build_run_contract(
        load_config(config_path),
        config_path=config_path,
        project_root=tmp_path,
        workflow_input_manifest_ref=str(manifest),
    )

    assert contract["workflow"]["kind"] == "refactor"
    assert contract["workflow"]["strictness"] == "full-parity"
    assert "workflow_input_manifest[0]" in contract["digests"]
    assert "skill_adapter_plan[0]" in contract["digests"]


def test_run_contract_allows_first_manifest_binding_but_not_config_drift(tmp_path):
    config_path = tmp_path / "zf.yaml"
    config_path.write_text("""\
version: "1.0"
project: {name: demo, state_dir: .zf-demo}
roles: []
workflow: {}
""", encoding="utf-8")
    manifest = tmp_path / "workflow-input-manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "workflow.input_manifest.v1",
        "kind": "prd",
        "strictness": "strict",
    }), encoding="utf-8")
    config = load_config(config_path)
    previous = build_run_contract(config, config_path=config_path, project_root=tmp_path)
    current = build_run_contract(
        config,
        config_path=config_path,
        project_root=tmp_path,
        workflow_input_manifest_ref=str(manifest),
    )
    binding = evaluate_run_contract_submit_binding(
        previous,
        current,
        bootstrap=build_run_contract(config, config_path=config_path, project_root=tmp_path),
        strict=True,
    )

    assert binding["status"] == "PASS"
    assert binding["initial_binding"] is True
    assert binding["comparison_basis"] == "bootstrap"

    config_path.write_text(config_path.read_text(encoding="utf-8") + "\n# drift\n")
    changed = load_config(config_path)
    blocked = evaluate_run_contract_submit_binding(
        previous,
        build_run_contract(
            changed,
            config_path=config_path,
            project_root=tmp_path,
            workflow_input_manifest_ref=str(manifest),
        ),
        bootstrap=build_run_contract(
            changed,
            config_path=config_path,
            project_root=tmp_path,
        ),
        strict=True,
    )

    assert blocked["status"] == "STOP"
    assert blocked["diagnostics"][0]["kind"] == "run_contract_drift"


def test_run_contract_resume_preserves_bound_workflow_manifest(tmp_path):
    config_path = tmp_path / "zf.yaml"
    config_path.write_text("""\
version: "1.0"
project: {name: demo, state_dir: .zf-demo}
roles: []
workflow: {}
""", encoding="utf-8")
    manifest = tmp_path / "workflow-input-manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "workflow.input_manifest.v1",
        "kind": "prd",
        "strictness": "strict",
    }), encoding="utf-8")
    config = load_config(config_path)
    state_dir = tmp_path / ".zf-demo"
    original = build_run_contract(
        config,
        config_path=config_path,
        project_root=tmp_path,
        state_dir=state_dir,
        workflow_input_manifest_ref=str(manifest),
    )
    write_run_contract(state_dir, original)

    policy = evaluate_run_contract_resume_policy(
        config,
        config_path=config_path,
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert policy["status"] == "PASS"
    assert policy["previous_digest"] == original["contract_digest"]
    assert policy["current_digest"] == original["contract_digest"]


def test_run_contract_resume_blocks_changed_bound_workflow_manifest(tmp_path):
    config_path = tmp_path / "zf.yaml"
    config_path.write_text("""\
version: "1.0"
project: {name: demo, state_dir: .zf-demo}
roles: []
workflow: {}
""", encoding="utf-8")
    manifest = tmp_path / "workflow-input-manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "workflow.input_manifest.v1",
        "kind": "prd",
        "strictness": "strict",
    }), encoding="utf-8")
    config = load_config(config_path)
    state_dir = tmp_path / ".zf-demo"
    original = build_run_contract(
        config,
        config_path=config_path,
        project_root=tmp_path,
        state_dir=state_dir,
        workflow_input_manifest_ref=str(manifest),
    )
    write_run_contract(state_dir, original)
    manifest.write_text(json.dumps({
        "schema_version": "workflow.input_manifest.v1",
        "kind": "prd",
        "strictness": "strict",
        "prompt_ref": "changed.md",
    }), encoding="utf-8")

    policy = evaluate_run_contract_resume_policy(
        config,
        config_path=config_path,
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert policy["status"] == "STOP"
    assert policy["diagnostics"]


def test_run_contract_pins_durable_result_protocol(tmp_path):
    config_path = tmp_path / "zf.yaml"
    config_path.write_text("""\
version: "1.0"
project: {name: demo, state_dir: .zf-demo}
roles: []
workflow:
  dag:
    schema_profile: canonical-dag/v5
  _flow_metadata:
    result_protocol:
      mode: blocking
      required_operation_ids: [wop-verify-TASK-1]
      read_policy_ref: artifacts/attempts/read-policies/policy.json
      read_policy_digest: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
""", encoding="utf-8")

    contract = build_run_contract(
        load_config(config_path),
        config_path=config_path,
        project_root=tmp_path,
    )

    result_protocol = contract["protocols"]["result_protocol"]
    operation = contract["protocols"]["workflow_operation"]
    read_policy = contract["protocols"]["required_read"]
    assert result_protocol["schema_version"] == "call-result-envelope.v1"
    assert result_protocol["mode"] == "blocking"
    assert result_protocol["adapter_version"]
    assert result_protocol["canonicalization_version"]
    assert operation["canonicalization_version"]
    assert operation["required_operation_ids"] == ["wop-verify-TASK-1"]
    assert read_policy == {
        "schema_version": "input-consumption-policy.v1",
        "policy_ref": "artifacts/attempts/read-policies/policy.json",
        "policy_digest": "a" * 64,
    }
