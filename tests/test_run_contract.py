from __future__ import annotations

import json

from zf.core.config.loader import load_config
from zf.runtime.run_contract import (
    active_run_contract_path,
    bind_run_contract_workflow_artifacts,
    build_run_contract,
    evaluate_run_contract_resume_policy,
    hydrate_run_effective_config,
    evaluate_run_contract_submit_binding,
    load_run_contract,
    load_run_contract_snapshot,
    run_contract_drift_diagnostics,
    write_run_contract,
    write_run_contract_snapshot,
)
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


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


def test_run_contract_uses_workflow_metadata_for_multi_kind_config(tmp_path):
    config_path = tmp_path / "zf.yaml"
    config_path.write_text("""\
version: "1.0"
project: {name: demo, state_dir: .zf-demo}
workflow:
  _flow_metadata_by_kind:
    issue:
      completion_profile: software_delivery
      flow_kind: issue
    workflow:
      completion_profile: artifact_delivery
      flow_kind: workflow
      workflow_template: evidence-synthesis-v1
      intent: research
""", encoding="utf-8")

    contract = build_run_contract(
        load_config(config_path),
        config_path=config_path,
        project_root=tmp_path,
        state_dir=tmp_path / ".zf-demo",
    )

    assert contract["workflow"]["kind"] == "workflow"
    assert contract["workflow"]["completion_profile"] == "artifact_delivery"
    assert contract["workflow"]["template"] == "evidence-synthesis-v1"


def test_run_contract_snapshots_preserve_history_and_are_idempotent(tmp_path):
    state_dir = tmp_path / ".zf-demo"
    first = {
        "schema_version": "run-contract.v1",
        "created_at": "2026-07-23T00:00:00+00:00",
        "project": {"name": "one"},
        "contract_digest": "",
    }
    from zf.runtime.run_contract import stable_json_sha256

    first["contract_digest"] = stable_json_sha256({
        "schema_version": "run-contract.v1",
        "project": {"name": "one"},
    })
    second = {
        "schema_version": "run-contract.v1",
        "created_at": "2026-07-23T00:01:00+00:00",
        "project": {"name": "two"},
        "contract_digest": "",
    }
    second["contract_digest"] = stable_json_sha256({
        "schema_version": "run-contract.v1",
        "project": {"name": "two"},
    })

    first_snapshot = write_run_contract_snapshot(state_dir, first)
    repeated = write_run_contract_snapshot(
        state_dir,
        {**first, "created_at": "2026-07-23T00:02:00+00:00"},
    )
    second_snapshot = write_run_contract_snapshot(state_dir, second)
    write_run_contract(state_dir, second)

    assert repeated["ref"] == first_snapshot["ref"]
    assert first_snapshot["ref"] != second_snapshot["ref"]
    assert load_run_contract_snapshot(state_dir, first_snapshot)["contract"]["project"]["name"] == "one"
    assert load_run_contract_snapshot(state_dir, second_snapshot)["contract"]["project"]["name"] == "two"
    assert load_run_contract(state_dir)["project"]["name"] == "two"
    assert active_run_contract_path(state_dir).exists()


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


def test_submit_binding_allows_distinct_run_after_prior_terminal() -> None:
    previous = {
        "contract_digest": "old",
        "workflow": {"strictness": "strict"},
        "refs": {"workflow_input_manifest": ["old.json"]},
        "digests": {"workflow_input_manifest[0]": "old-manifest"},
    }
    current = {
        "contract_digest": "new",
        "workflow": {"strictness": "strict"},
        "refs": {"workflow_input_manifest": ["new.json"]},
        "digests": {"workflow_input_manifest[0]": "new-manifest"},
    }

    binding = evaluate_run_contract_submit_binding(
        previous,
        current,
        bootstrap=current,
        strict=True,
        prior_terminal_rotation=True,
    )

    assert binding["status"] == "PASS"
    assert binding["strict"] is True
    assert binding["prior_terminal_rotation"] is True
    assert binding["comparison_basis"] == "prior_terminal_rotation"
    assert binding["diagnostics"] == []


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


def test_run_contract_resume_preserves_submitted_workflow_bindings(tmp_path):
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
        "kind": "issue",
        "strictness": "strict",
    }), encoding="utf-8")
    config = load_config(config_path)
    state_dir = tmp_path / ".zf-demo"
    proposal_ref = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "workflow.proposal.v1", "revision": 2},
        root="workflow/proposals/issue",
        kind="workflow_proposal",
        schema_version="workflow.proposal.v1",
        created_by="test",
    )
    effective_ref = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "effective-config-snapshot.v1",
            "config": {"project": {"name": "demo"}},
        },
        root="workflow/proposals/issue/effective-configs",
        kind="effective_config_snapshot",
        schema_version="effective-config-snapshot.v1",
        created_by="test",
    )
    original = bind_run_contract_workflow_artifacts(
        build_run_contract(
            config,
            config_path=config_path,
            project_root=tmp_path,
            state_dir=state_dir,
            workflow_input_manifest_ref=str(manifest),
        ),
        proposal_ref=proposal_ref,
        proposal_digest=str(proposal_ref["sha256"]),
        effective_config_ref=effective_ref,
    )
    write_run_contract(state_dir, original)

    policy = evaluate_run_contract_resume_policy(
        config,
        config_path=config_path,
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert policy["status"] == "PASS"
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


def test_strict_resume_stops_on_config_drift_and_keeps_pinned_effective_config(
    tmp_path,
) -> None:
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        'version: "1.0"\n'
        "project: {name: pinned, state_dir: .zf-demo}\n"
        "roles: []\n"
        "workflow: {}\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "workflow-input-manifest.json"
    manifest.write_text(
        json.dumps({
            "schema_version": "workflow.input_manifest.v1",
            "kind": "prd",
            "strictness": "strict",
        }),
        encoding="utf-8",
    )
    state_dir = tmp_path / ".zf-demo"
    effective_ref = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "effective-config-snapshot.v1",
            "config": {"project": {"name": "pinned"}},
        },
        root="workflow/proposals/run-freeze/effective-configs",
        kind="effective_config_snapshot",
        schema_version="effective-config-snapshot.v1",
        created_by="test",
    )
    original = bind_run_contract_workflow_artifacts(
        build_run_contract(
            load_config(config_path),
            config_path=config_path,
            project_root=tmp_path,
            state_dir=state_dir,
            workflow_input_manifest_ref=str(manifest),
        ),
        effective_config_ref=effective_ref,
    )
    write_run_contract(state_dir, original)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "name: pinned",
            "name: drifted",
        ),
        encoding="utf-8",
    )

    policy = evaluate_run_contract_resume_policy(
        load_config(config_path),
        config_path=config_path,
        project_root=tmp_path,
        state_dir=state_dir,
    )

    assert policy["status"] == "STOP"
    assert policy["strict"] is True
    assert hydrate_run_effective_config(
        state_dir,
        original,
    )["project"]["name"] == "pinned"


def test_effective_config_snapshots_are_isolated_across_runs(tmp_path) -> None:
    state_dir = tmp_path / ".zf"

    def effective(name: str) -> dict:
        return write_immutable_json_sidecar(
            state_dir,
            {
                "schema_version": "effective-config-snapshot.v1",
                "config": {"project": {"name": name}},
            },
            root="workflow/proposals/multi-run/effective-configs",
            kind="effective_config_snapshot",
            schema_version="effective-config-snapshot.v1",
            created_by="test",
        )

    first = bind_run_contract_workflow_artifacts(
        {
            "schema_version": "run-contract.v1",
            "workflow": {"strictness": "strict"},
            "config": {},
            "contract_digest": "",
        },
        proposal_digest="a" * 64,
        effective_config_ref=effective("revision-n"),
    )
    second = bind_run_contract_workflow_artifacts(
        {
            "schema_version": "run-contract.v1",
            "workflow": {"strictness": "strict"},
            "config": {},
            "contract_digest": "",
        },
        proposal_digest="b" * 64,
        effective_config_ref=effective("revision-n-plus-1"),
    )
    first_snapshot = write_run_contract_snapshot(state_dir, first)
    second_snapshot = write_run_contract_snapshot(state_dir, second)

    assert first_snapshot["ref"] != second_snapshot["ref"]
    assert hydrate_run_effective_config(
        state_dir,
        first,
    )["project"]["name"] == "revision-n"
    assert hydrate_run_effective_config(
        state_dir,
        second,
    )["project"]["name"] == "revision-n-plus-1"
