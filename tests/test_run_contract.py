from __future__ import annotations

import json

from zf.core.config.loader import load_config
from zf.runtime.run_contract import (
    build_run_contract,
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
