from pathlib import Path
import json

from zf.core.config.schema import ZfConfig
from zf.runtime.plan_execution_review import execution_policy, review_execution


def test_reports_execution_mismatch_without_semantic_verdict():
    task_map = {
        "shared_conventions": {"setup_commands": ["make setup"]},
        "tasks": [
            {"task_id": "register", "blocked_by": [], "validation": {
                "commands": [{"owner": "task_verify"}]}},
            {"task_id": "publish", "blocked_by": ["register"]},
        ],
    }
    policy = execution_policy(ZfConfig())
    policy["stages"] = [{"id": "prd-verify", "trigger": "candidate.ready"}]
    result = review_execution(task_map, ports={}, policy=policy,
                              metadata={})
    assert result["graph"]["longest_dependency_chain"] == 2
    assert result["verification"]["independent_task_verify"] is False
    assert "setup_not_wired" in result["review_items"]
    assert "task_verify_after_candidate" in result["review_items"]
    assert "verdict" not in result
    assert "passed" not in result


def test_task_pipeline_and_cycles_are_reported_as_facts():
    policy = execution_policy(ZfConfig())
    policy["setup_declared"] = True
    policy["impl_self_check_required"] = True
    result = review_execution(
        {"tasks": [{"task_id": "a", "blocked_by": ["b"]},
                   {"task_id": "b", "blocked_by": ["a"]}]},
        ports={}, policy=policy,
        metadata={"task_pipeline": {"mode": "blocking"}},
    )
    assert result["verification"]["independent_task_verify"] is True
    assert result["graph"]["longest_dependency_chain"] is None
    assert result["graph"]["unresolved_task_ids"] == ["a", "b"]


def test_review_binds_actual_task_map_policy_and_generation_without_selecting_route():
    from copy import deepcopy
    from zf.core.config.schema import RoleConfig

    config = ZfConfig(roles=[RoleConfig(name="worker", role_kind="writer", flow_kind="prd")])
    task_map = {"tasks": [
        {"task_id": "a", "allowed_paths": ["app/**"]},
        {"task_id": "b", "allowed_paths": ["app/index.py"]},
    ]}
    identity = {"workflow_run_id": "run", "task_map_generation": "g2"}
    policy = execution_policy(config)
    report = review_execution(task_map, ports={}, policy=policy,
                              metadata={"flow_kind": "prd"}, identity=identity)
    assert report["input_binding"]["task_map_generation"] == "g2"
    assert report["capacity"]["configured_writer_count"] == 1
    assert report["capacity"]["dependency_level_peak_width"] == 2
    assert report["capacity"]["write_scope_validation"]["status"] == "failed"
    changed = deepcopy(task_map)
    changed["tasks"][1]["allowed_paths"] = ["tests/test_app.py"]
    next_report = review_execution(changed, ports={}, policy=policy, metadata={})
    assert next_report["input_binding"]["task_map_digest"] != report["input_binding"]["task_map_digest"]
    assert next_report["capacity"]["write_scope_validation"]["status"] == "passed"
    config.workflow.impl_self_check_required = not config.workflow.impl_self_check_required
    assert execution_policy(config)["effective_policy_digest"] != policy["effective_policy_digest"]
    assert "recommended_route" not in report
    assert "verdict" not in report


def test_cli_inspection_is_read_only(tmp_path: Path, capsys):
    from zf.cli.main import main
    (tmp_path / "zf.yaml").write_text("project:\n  name: sample\n  state_dir: state\n")
    plan = tmp_path / "task_map.json"
    plan.write_text('{"tasks": [{"task_id":"a","blocked_by":[]}]}')
    assert main(["plan", "inspect-execution", "--config", str(tmp_path / "zf.yaml"),
                 "--task-map", str(plan), "--flow-kind", "prd"]) == 0
    assert '"longest_dependency_chain": 1' in capsys.readouterr().out
    assert not (tmp_path / "state").exists()


def test_cli_package_inspection_preserves_generation_and_rejects_other_map(tmp_path, capsys):
    from zf.cli.main import main
    from zf.runtime.plan_artifact_package import write_plan_artifact_package
    from tests.test_plan_artifact_package import _package

    state = tmp_path / "state"
    state.mkdir()
    config = tmp_path / "zf.yaml"
    config.write_text("project:\n  name: sample\n  state_dir: state\n")
    package = _package(state, revision="plan-r2", generation="generation-2")
    descriptor = write_plan_artifact_package(state, package)
    task_map = next(port for port in package["produced"] if port["logical_name"] == "task_map")
    plan_path = tmp_path / "task_map.json"
    plan_path.write_bytes((state / task_map["ref"]).read_bytes())
    args = ["plan", "inspect-execution", "--config", str(config), "--task-map", str(plan_path),
            "--flow-kind", "prd", "--plan-package", str(state / descriptor["ref"])]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["input_binding"]["workflow_run_id"] == "run-1"
    assert report["input_binding"]["task_map_generation"] == "generation-2"
    assert report["input_binding"]["plan_revision"] == "plan-r2"
    assert report["config_scope"] == "current_file_not_frozen_run"
    plan_path.write_text('{"tasks": []}')
    assert main(args) == 1
    assert "task map differs" in capsys.readouterr().out


def test_new_drafts_do_not_force_ac_chunking_but_require_self_check():
    from zf.cli.flow_draft_support import draft_runtime_profile_doc
    from zf.runtime.writer_fanout_admission import writer_task_map_policy_errors

    items = [{"task_id": "atomic-transfer", "allowed_paths": ["app/transfer/**"],
              "verification": "make test-transfer", "acceptance_criteria": list(range(9))}]
    for kind in ("issue", "prd", "refactor", ""):
        workflow = draft_runtime_profile_doc(name="test", backend="codex", kind=kind)["spec"]["workflow"]
        assert workflow["work_units"]["split_quality"]["max_acceptance_criteria"] == 0
        assert workflow["impl_self_check_required"] is True
        assert writer_task_map_policy_errors(
            items, candidate_quality_source=workflow["candidate_quality_source"],
            work_units_config=workflow["work_units"],
        ) == []
        workflow["work_units"]["split_quality"]["max_acceptance_criteria"] = 8
        assert "max is 8" in writer_task_map_policy_errors(
            items, work_units_config=workflow["work_units"],
        )[0]


def test_readiness_evidence_is_a_required_critic_input(tmp_path):
    from zf.runtime.plan_execution_review import attach_execution_review
    from zf.runtime.artifact_read_ledger import source_manifest_from_payload
    from zf.runtime.artifact_read_requirements import build_canonical_required_reads
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    payload = {}
    review = review_execution({"tasks": []}, ports={}, policy={}, metadata={})
    attach_execution_review(state_dir=tmp_path, payload=payload, review=review, source_event_id="evt")
    descriptor = payload["input_refs"][0]
    assert hydrate_sidecar_ref(tmp_path, descriptor).payload == review
    manifest, _ = source_manifest_from_payload(state_dir=tmp_path, project_root=tmp_path,
                                            payload=payload, workflow_run_id="run", task_id="T",
                                            attempt_id="attempt", dispatch_id="dispatch")
    reads = build_canonical_required_reads(manifest, output_profile_id="plan-synth")
    assert any(r["source_id"] == "plan-execution-review" for r in reads)


def test_cli_exposes_actual_selection_despite_planned_stage_annotations(tmp_path, capsys):
    from zf.cli.main import main

    config = tmp_path / "zf.yaml"
    config.write_text("project:\n  name: sample\n  state_dir: state\n")
    plan = tmp_path / "task_map.json"
    plan.write_text(json.dumps({"tasks": [{
        "task_id": "release",
        "validation": {"commands": [
            {"id": "local", "owner": "impl_self_check", "producer_task_id": "release"},
            {"id": "audit", "owner": "candidate_verify", "producer_task_id": "release",
             "execution_stage": "after_independent_verify"},
            {"id": "sign", "owner": "human", "producer_task_id": "release"},
            {"id": "upstream", "owner": "task_verify", "producer_task_id": "other"},
        ]},
        "acceptance_criteria": [
            {"id": "AC-audit", "verification_owner": "candidate_verify",
             "verification_command_ids": ["audit"], "execution_stage": "after_independent_verify"},
            {"id": "AC-sign", "verification_owner": "human"},
        ],
    }]}))
    assert main(["plan", "inspect-execution", "--config", str(config),
                 "--task-map", str(plan), "--flow-kind", "prd"]) == 0
    report = json.loads(capsys.readouterr().out)
    row = report["verification"]["task_requirements"][0]
    commands = {c["command_id"]: c for c in row["commands"]}
    assert commands["local"]["required_by_verification_owner"] == [
        "impl_self_check", "task_verify", "candidate_verify",
    ]
    assert commands["audit"]["declared_execution_stage"] == "after_independent_verify"
    assert commands["audit"]["required_by_verification_owner"] == ["candidate_verify"]
    assert commands["sign"]["required_by_verification_owner"] == []
    assert commands["upstream"]["required_by_verification_owner"] == []
    criteria = {a["acceptance_id"]: a for a in row["acceptance_criteria"]}
    assert criteria["AC-audit"]["required_by_verification_owner"] == ["candidate_verify"]
    assert criteria["AC-audit"]["command_ids"] == ["audit"]
    assert criteria["AC-sign"]["required_by_verification_owner"] == []
    assert "verdict" not in report
    assert not (tmp_path / "state").exists()
