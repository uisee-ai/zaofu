from pathlib import Path

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


def test_cli_inspection_is_read_only(tmp_path: Path, capsys):
    from zf.cli.main import main
    (tmp_path / "zf.yaml").write_text("project:\n  name: sample\n  state_dir: state\n")
    plan = tmp_path / "task_map.json"
    plan.write_text('{"tasks": [{"task_id":"a","blocked_by":[]}]}')
    assert main(["plan", "inspect-execution", "--config", str(tmp_path / "zf.yaml"),
                 "--task-map", str(plan), "--flow-kind", "prd"]) == 0
    assert '"longest_dependency_chain": 1' in capsys.readouterr().out
    assert not (tmp_path / "state").exists()


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
