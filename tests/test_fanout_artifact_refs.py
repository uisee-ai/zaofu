from __future__ import annotations

import json
import hashlib
import shutil

import pytest

from zf.core.config.schema import ZfConfig
from zf.runtime.fanout_artifact_refs import (
    prepare_fanout_synth_reports,
    relocate_fanout_artifact_refs,
)
from zf.runtime.plan_artifact_package_ports import materialize_explicit_plan_ports
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package,
    hydrate_plan_artifact_package,
    write_plan_artifact_package,
)
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref
from tests.test_plan_artifact_package import _run_contract


@pytest.mark.parametrize("nested", [False, True])
def test_referenced_plan_ports_survive_author_workdir_removal(tmp_path, nested):
    project_root = tmp_path / "project"
    state_dir = project_root / "runtime-state"
    child_root = state_dir / "workdirs" / "planner" / "project"
    ref = "artifacts/plan/behavior-specs.json"
    source = child_root / ref
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"cases": [{"id": "CASE-1", "expected": "rollback"}]}))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    descriptor = {
        "logical_name": "behavior_specs",
        "schema_version": "behavior-specs.v1",
        "ref": ref,
        "sha256": digest,
    }
    body = {"plan_ports": [descriptor]}
    report = {"plan_synthesis_result": body} if nested else body
    row = {
        "child_id": "planner",
        "role_instance": "planner",
        "report": report,
    }
    prepared = prepare_fanout_synth_reports(
        reports=[row],
        manifest={"fanout_id": "F-PLAN"},
        state_dir=state_dir,
        project_root=project_root,
        config=ZfConfig(),
        roles=[],
    )
    shutil.rmtree(child_root)
    ports = materialize_explicit_plan_ports(
        state_dir=state_dir,
        project_root=project_root,
        payload=prepared[0]["report"],
        flow_kind="prd",
        required_ports=["behavior_specs"],
    )
    port = ports["behavior_specs"]
    assert port["ref"].startswith("artifacts/fanouts/F-PLAN/planner/")
    assert port["sha256"] == digest
    assert hydrate_sidecar_ref(state_dir, port).payload == {
        "cases": [{"id": "CASE-1", "expected": "rollback"}],
    }
    package = build_plan_artifact_package(
        workflow_run_id="run-plan",
        flow_kind="prd",
        producer_stage_id="prd-plan",
        run_contract=_run_contract(state_dir),
        plan_revision="R1",
        task_map_generation="G1",
        produced=ports.values(),
        required_ports=["behavior_specs"],
    )
    package_ref = write_plan_artifact_package(state_dir, package)
    assert hydrate_plan_artifact_package(state_dir, package_ref) == package
    assert descriptor["ref"] == ref  # Preserve the submitted report.
    (state_dir / port["ref"]).write_text('{"cases": []}')
    with pytest.raises(SidecarRefError, match="digest|sha256|hash"):
        materialize_explicit_plan_ports(
            state_dir=state_dir,
            project_root=project_root,
            payload=prepared[0]["report"],
            flow_kind="prd",
            required_ports=["behavior_specs"],
        )


def test_referenced_plan_port_does_not_copy_an_escaping_symlink(tmp_path):
    project_root = tmp_path / "project"
    state_dir = project_root / "runtime-state"
    child_root = state_dir / "workdirs" / "planner" / "project"
    source = child_root / "artifacts" / "escape.json"
    source.parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"private": true}')
    source.symlink_to(outside)
    report = {"plan_ports": [{
        "logical_name": "behavior_specs",
        "ref": "artifacts/escape.json",
        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
    }]}
    prepared = prepare_fanout_synth_reports(
        reports=[{"child_id": "planner", "role_instance": "planner", "report": report}],
        manifest={"fanout_id": "F-PLAN"},
        state_dir=state_dir,
        project_root=project_root,
        config=ZfConfig(),
        roles=[],
    )
    with pytest.raises(SidecarRefError):
        materialize_explicit_plan_ports(
            state_dir=state_dir,
            project_root=project_root,
            payload=prepared[0]["report"],
            flow_kind="prd",
        )
    assert not (state_dir / "artifacts" / "fanouts" / "F-PLAN").exists()


def test_relocate_fanout_artifact_refs_canonicalizes_legacy_inventory_ref(tmp_path):
    payload = relocate_fanout_artifact_refs(
        payload={"hermes_source_inventory_ref": "docs/plans/source-inventory.json"},
        payload_sources=[],
        manifest={"fanout_id": "scan"},
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        config=ZfConfig(),
        roles=[],
    )

    assert payload["source_inventory_ref"] == "docs/plans/source-inventory.json"
    assert "hermes_source_inventory_ref" not in payload


def test_prepare_fanout_synth_reports_prefers_child_workdir_artifact(tmp_path):
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    child_root = state_dir / "workdirs" / "issue-triage" / "project"
    child_artifact = child_root / "artifacts" / "task_map.json"
    stale_artifact = project_root / "artifacts" / "task_map.json"
    child_artifact.parent.mkdir(parents=True)
    stale_artifact.parent.mkdir(parents=True)
    child_artifact.write_text(
        json.dumps({"tasks": [{"task_id": "fresh-child-task"}]}),
        encoding="utf-8",
    )
    stale_artifact.write_text(
        json.dumps({"tasks": [{"task_id": "stale-project-task"}]}),
        encoding="utf-8",
    )

    prepared = prepare_fanout_synth_reports(
        reports=[{
            "child_id": "issue-triage",
            "role_instance": "issue-triage",
            "report_path": "fanouts/F-ISSUE/children/issue-triage/report.json",
            "report": {
                "status": "passed",
                "task_map_ref": "artifacts/task_map.json",
                "artifact_refs": [
                    "artifacts/task_map.json",
                    "command:npm test#exit=0",
                ],
            },
        }],
        manifest={"fanout_id": "F-ISSUE"},
        state_dir=state_dir,
        project_root=project_root,
        config=ZfConfig(),
        roles=[],
    )

    task_map_ref = prepared[0]["report"]["task_map_ref"]
    assert task_map_ref.startswith(
        "artifacts/fanouts/F-ISSUE/issue-triage/"
    )
    assert json.loads((state_dir / task_map_ref).read_text(encoding="utf-8")) == {
        "tasks": [{"task_id": "fresh-child-task"}]
    }
    assert prepared[0]["report"]["artifact_refs"] == [
        task_map_ref,
        "command:npm test#exit=0",
    ]
    assert prepared[0]["report_path"].startswith(
        "artifacts/fanouts/F-ISSUE/prepared-child-reports/issue-triage/"
    )


def test_relocate_refactor_plan_evidence_refs_from_child_workdir(tmp_path):
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    child_root = state_dir / "workdirs" / "refactor-plan-author" / "project"
    refs = {
        "source_index_ref": "docs/plans/source-index.json",
        "scan_quality_audit_ref": "docs/plans/scan-quality-audit.json",
        "review_artifact_ref": "docs/plans/review.md",
        "coverage_matrix_ref": "docs/plans/coverage.json",
        "findings_ref": "docs/plans/findings.json",
    }
    for index, ref in enumerate(refs.values()):
        path = child_root / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"index": index}), encoding="utf-8")

    relocated = relocate_fanout_artifact_refs(
        payload=dict(refs),
        payload_sources=[{
            "child_id": "refactor-plan-author",
            "role_instance": "refactor-plan-author",
            "report": dict(refs),
        }],
        manifest={"fanout_id": "F-PLAN"},
        state_dir=state_dir,
        project_root=project_root,
        config=ZfConfig(),
        roles=[],
    )

    for key, original in refs.items():
        assert relocated[key] != original
        assert relocated[key].startswith(
            "artifacts/fanouts/F-PLAN/refactor-plan-author/"
        )
        assert (state_dir / relocated[key]).exists()
