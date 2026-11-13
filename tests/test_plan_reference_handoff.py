"""Keep full referenced ports available to preflight, Critic and Package readers."""
import hashlib
import json
import shutil

import pytest

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.fanout_artifact_refs import prepare_fanout_synth_reports
from zf.runtime.plan_artifact_package_ports import materialize_explicit_plan_ports
from zf.runtime.plan_candidate_ports import plan_port_bodies
from zf.runtime.plan_synth_handoff import build_plan_candidate_input_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.result_submit import parse_semantic_result_bytes
from zf.runtime.workflow_read_result import normalize_workflow_read_result


@pytest.mark.parametrize("name", ["source_inventory", "capability_matrix", "acceptance_matrix",
                                  "test_matrix", "real_e2e_matrix", "source_index"])
def test_complete_referenced_port_survives_author_cleanup(tmp_path, name):
    project = tmp_path / "project"
    state = project / "runtime-state"
    author = state / "workdirs/planner/project"
    ref = f"artifacts/plan/{name}.json"
    source = author / ref
    source.parent.mkdir(parents=True)
    body = {"schema_version": "fixture.v1", "content": "完整语义，保留失败不变项", "rows": [{"id": "S1"}]}
    source.write_text(json.dumps(body, ensure_ascii=False))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    original = {f"{name}_ref": ref, "artifact_refs": [ref]}
    submitted = parse_semantic_result_bytes(json.dumps({
        **original, "summary": "完整引用交接", "result_semantics": "artifact_production",
    }).encode())
    normalized, issues = normalize_workflow_read_result(ZfEvent(
        type="fanout.child.completed", actor="planner", payload={"report": submitted},
    ))
    assert not issues
    assert normalized[f"{name}_ref"] == ref
    reports = prepare_fanout_synth_reports(
        reports=[{"child_id": "planner", "role_instance": "planner", "report": normalized}],
        manifest={"fanout_id": "F-REF"}, state_dir=state, project_root=project,
        config=ZfConfig(), roles=[],
    )
    shutil.rmtree(author)
    report = reports[0]["report"]
    errors = []
    bodies = plan_port_bodies([report], trigger={}, state_dir=state, project_root=project, errors=errors)
    assert not errors
    assert bodies[name] == body
    inputs, _ = build_plan_candidate_input_refs(state_dir=state, project_root=project, reports=reports)
    bound = [item for item in inputs if item["sha256"] == digest]
    assert len(bound) == 1  # duplicate ref discovery must not duplicate the required body
    assert hydrate_sidecar_ref(state, bound[0]).payload == body
    ports = materialize_explicit_plan_ports(state_dir=state, project_root=project, payload=report, flow_kind="prd")
    assert ports[name]["sha256"] == digest
    assert hydrate_sidecar_ref(state, ports[name]).payload == body
    assert original == {f"{name}_ref": ref, "artifact_refs": [ref]}
    (state / ports[name]["ref"]).unlink()
    errors = []
    assert name not in plan_port_bodies([report], trigger={}, state_dir=state, project_root=project, errors=errors)
    assert any(error["code"] == "plan_port_unreadable" for error in errors)
