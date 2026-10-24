"""doc 78 W3a: refactor plan enters the artifact-ledger version chain."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.refactor_artifacts import (
    build_plan_manifest_payload,
    project_refactor_artifacts,
)
from zf.runtime.task_map_history import build_task_map_history


def test_refactor_plan_skill_does_not_require_unemitted_kernel_ports() -> None:
    skill = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "zf-refactor-plan-synth"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Kernel/profile-required ports" in skill
    assert "Do not redeclare a" in skill
    assert "Kernel-derived port unless this Plan result" in skill
    assert (
        "including the requirement/contract ref, Goal Claim Set, Task Map, "
        "planning result"
    ) not in skill


def _projection(plan_ref: str, task_map_ref: str) -> dict:
    return {
        "plan_artifact_ref": plan_ref,
        "task_map_ref": task_map_ref,
        "artifact_digests": {plan_ref: "sha-plan", task_map_ref: "sha-map"},
    }


def test_builder_emits_task_map_and_plan_refs_with_sha():
    payload = build_plan_manifest_payload(
        projection_payload=_projection("/a/refactor-plan.md", "/a/task_map.json"),
        feature_id="CJMIN-1",
    )
    kinds = {ref["kind"]: ref for ref in payload["artifact_refs"]}
    assert set(kinds) == {"task_map", "implementation_plan"}
    assert kinds["task_map"]["sha256"] == "sha-map"
    assert payload["feature_id"] == "CJMIN-1"
    # must NOT look like an orchestrator final manifest (reactor collision guard)
    assert payload["role"] != "orchestrator"
    assert "product_delivery" not in payload["handoff_contract"]


def test_builder_recomputes_manifest_sha_for_existing_artifacts(tmp_path: Path):
    plan = tmp_path / "refactor-plan.md"
    task_map = tmp_path / "task_map.json"
    plan.write_text("fresh plan\n", encoding="utf-8")
    task_map.write_text('{"tasks":[]}\n', encoding="utf-8")

    payload = build_plan_manifest_payload(
        projection_payload={
            "plan_artifact_ref": str(plan),
            "task_map_ref": str(task_map),
            "artifact_digests": {
                str(plan): "stale-plan-sha",
                str(task_map): "stale-task-map-sha",
            },
        },
        feature_id="CJMIN-1",
    )

    refs = {ref["kind"]: ref for ref in payload["artifact_refs"]}
    assert refs["implementation_plan"]["sha256"] == hashlib.sha256(
        plan.read_bytes()
    ).hexdigest()
    assert refs["task_map"]["sha256"] == hashlib.sha256(
        task_map.read_bytes()
    ).hexdigest()


def test_replan_marks_summary():
    payload = build_plan_manifest_payload(
        projection_payload=_projection("/b/refactor-plan.md", "/b/task_map.json"),
        feature_id="CJMIN-1",
        is_replan=True,
    )
    task_map = next(r for r in payload["artifact_refs"] if r["kind"] == "task_map")
    assert "replan" in task_map["summary"]


def test_legacy_refactor_plan_ready_projects_plan_artifact(tmp_path):
    projection = project_refactor_artifacts(
        state_dir=tmp_path / ".zf",
        manifest={
            "fanout_id": "fanout-legacy-plan",
            "trigger_payload": {
                "review_artifact_ref": "docs/review.md",
                "plan_intent": "Plan legacy refactor.",
            },
        },
        success_event="refactor.plan.ready",
        synth_event=ZfEvent(
            type="fanout.synth.completed",
            actor="refactor-plan-synth",
            payload={
                "report": {
                    "review_artifact_ref": "docs/review.md",
                    "plan_intent": "Plan legacy refactor.",
                    "refactor_plan_md": "## Plan\n\nSplit runtime flow.",
                    "task_map": {"tasks": []},
                    "gates": [{"command": "pytest"}],
                    "risk_register": [],
                    "backlog_candidates": [],
                },
            },
        ),
    )

    assert projection is not None
    assert projection.ok is True
    plan_ref = projection.payload["plan_artifact_ref"]
    assert "Split runtime flow" in Path(plan_ref).read_text(encoding="utf-8")


def test_refactor_plan_projection_preserves_planner_structure_for_sparse_synth(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    fanout_id = "fanout-plan-sparse-synth"
    child_id = "refactor-plan-author"
    result_dir = state_dir / "fanouts" / fanout_id / "children" / child_id
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(json.dumps({
        "payload": {
            "source_index_ref": "docs/plans/source-index.json",
            "scan_quality_audit_ref": "docs/plans/scan-quality-audit.json",
            "coverage_matrix_ref": "artifacts/review/coverage.json",
            "findings_ref": "artifacts/review/findings.json",
            "plan_ports": [{
                "logical_name": "source_inventory",
                "schema_version": "source-inventory.v1",
                "body": {"schema_version": "source-inventory.v1", "items": []},
            }],
            "report": {
                "review_artifact_ref": "artifacts/review/review.md",
                "plan_intent": "Keep the planner structure.",
                "refactor_plan_md": "## Plan\n\nExtract one helper.",
                "task_map": {
                    "schema_version": "task-map.v1",
                    "required_plan_ports": [
                        "source_index",
                        "scan_quality_audit",
                        "review_artifact",
                        "coverage_matrix",
                        "findings",
                    ],
                    "tasks": [],
                },
                "gates": [{"command": "pytest"}],
                "risk_register": [],
                "backlog_candidates": [],
            },
        },
    }), encoding="utf-8")

    projection = project_refactor_artifacts(
        state_dir=state_dir,
        manifest={
            "fanout_id": fanout_id,
            "children": [{"child_id": child_id}],
        },
        success_event="zaofu.refactor.plan.ready",
        synth_event=ZfEvent(
            type="fanout.synth.completed",
            actor="plan-critic",
            payload={
                "status": "completed",
                "recommendation": "approve",
                "report": {
                    "status": "passed",
                    "recommendation": "approve",
                    "summary": "The plan is semantically sufficient.",
                },
            },
        ),
    )

    assert projection is not None and projection.ok is True
    assert projection.payload["plan_intent"] == "Keep the planner structure."
    assert projection.payload["plan_ports"][0]["logical_name"] == "source_inventory"
    assert projection.payload["source_index_ref"] == "docs/plans/source-index.json"
    assert projection.payload["scan_quality_audit_ref"] == (
        "docs/plans/scan-quality-audit.json"
    )
    assert projection.payload["review_artifact_ref"] == "artifacts/review/review.md"
    assert projection.payload["coverage_matrix_ref"] == "artifacts/review/coverage.json"
    assert projection.payload["findings_ref"] == "artifacts/review/findings.json"


def test_refactor_plan_projection_preserves_inventory_refs(tmp_path):
    projection = project_refactor_artifacts(
        state_dir=tmp_path / ".zf",
        manifest={"fanout_id": "fanout-plan-inventory"},
        success_event="zaofu.refactor.plan.ready",
        synth_event=ZfEvent(
            type="fanout.synth.completed",
            actor="refactor-plan-synth",
            payload={
                "report": {
                    "review_artifact_ref": "docs/review.md",
                    "plan_intent": "Plan with source inventory.",
                    "refactor_plan_md": "## Plan\n\nUse source inventory.",
                    "task_map": {"tasks": []},
                    "gates": [{"command": "pytest"}],
                    "risk_register": [],
                    "backlog_candidates": [],
                    "inventory_refs": [
                        "docs/plans/hermes-tool-inventory.json",
                    ],
                    "hermes_source_inventory_ref": (
                        "docs/plans/hermes-source-inventory.json"
                    ),
                    "inventory_coverage_matrix_ref": (
                        "docs/plans/hermes-inventory-coverage-matrix.json"
                    ),
                },
            },
        ),
    )

    assert projection is not None and projection.ok is True
    assert projection.payload["inventory_refs"] == [
        "docs/plans/hermes-tool-inventory.json",
    ]
    assert projection.payload["source_inventory_ref"] == (
        "docs/plans/hermes-source-inventory.json"
    )
    assert "hermes_source_inventory_ref" not in projection.payload
    assert projection.payload["inventory_coverage_matrix_ref"] == (
        "docs/plans/hermes-inventory-coverage-matrix.json"
    )
    assert "docs/plans/hermes-tool-inventory.json" in projection.payload["artifact_refs"]
    assert "docs/plans/hermes-source-inventory.json" in projection.payload["artifact_refs"]


def test_refactor_plan_projection_prefers_task_map_identity(tmp_path):
    projection = project_refactor_artifacts(
        state_dir=tmp_path / ".zf",
        manifest={"fanout_id": "fanout-plan-revision"},
        success_event="zaofu.refactor.plan.ready",
        synth_event=ZfEvent(
            type="fanout.synth.completed",
            actor="refactor-plan-synth",
            payload={
                "plan_revision": "revision-0002",
                "task_map_generation": "generation-old",
                "report": {
                    "review_artifact_ref": "docs/review.md",
                    "refactor_plan_md": "## Plan\n\nReplace the stale contract.",
                    "task_map": {
                        "schema_version": "task-map.v1",
                        "task_map_generation": "generation-new",
                        "metadata": {"plan_revision": "revision-0003"},
                        "tasks": [],
                    },
                    "gates": [{"command": "pytest"}],
                    "risk_register": [],
                    "backlog_candidates": [],
                },
            },
        ),
    )

    assert projection is not None and projection.ok is True
    assert projection.payload["plan_revision"] == "revision-0003"
    assert projection.payload["task_map_generation"] == "generation-new"


def test_refactor_plan_projection_inherits_replan_metadata_from_child_manifest(tmp_path):
    state_dir = tmp_path / ".zf"
    fanout_id = "fanout-plan-replan"
    child_id = "refactor-plan-synth"
    result_dir = state_dir / "fanouts" / fanout_id / "children" / child_id
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(json.dumps({
        "payload": {
            "report": {
                "review_artifact_ref": "docs/review.md",
                "plan_intent": "Refresh plan.",
                "refactor_plan_md": "## Plan\n\nRefresh task map.",
                "task_map": {"tasks": []},
                "gates": [{"command": "pytest"}],
                "risk_register": [],
                "backlog_candidates": [],
            },
        },
    }), encoding="utf-8")

    projection = project_refactor_artifacts(
        state_dir=state_dir,
        manifest={
            "fanout_id": fanout_id,
            "children": [{
                "child_id": child_id,
                "payload": {
                    "trigger_payload": {
                        "rework_of": "verify-failed-r35-contract-gap",
                        "rework_attempt": 2,
                        "rework_source": "verify.failed",
                        "replan_classification": "contract_freeze_gap",
                    },
                },
            }],
        },
        success_event="zaofu.refactor.plan.ready",
    )

    assert projection is not None and projection.ok is True
    assert projection.payload["rework_of"] == "verify-failed-r35-contract-gap"
    assert projection.payload["rework_attempt"] == 2
    assert projection.payload["rework_source"] == "verify.failed"
    assert projection.payload["replan_classification"] == "contract_freeze_gap"


def _ev(seq, payload):
    return (seq, SimpleNamespace(
        type="artifact.manifest.published", payload=payload, id=f"e{seq}",
        ts=f"2026-06-04T0{seq}:00:00Z", task_id="", feature_id="",
    ))


def test_two_published_plans_form_supersedes_chain():
    # The whole point of W3a: a fresh plan (v1) then a replan (v2) for the same
    # feature_id form a version chain where v2 is current and v1 greys out.
    v1 = build_plan_manifest_payload(
        projection_payload=_projection("/v1/plan.md", "/v1/task_map.json"),
        feature_id="CJMIN-1",
    )
    v2 = build_plan_manifest_payload(
        projection_payload=_projection("/v2/plan.md", "/v2/task_map.json"),
        feature_id="CJMIN-1", is_replan=True,
    )
    history = build_task_map_history([_ev(1, v1), _ev(2, v2)], feature_id="CJMIN-1")
    assert len(history) == 2
    assert history[0]["ref"] == "/v1/task_map.json"
    assert history[0]["superseded"] is True
    assert history[1]["ref"] == "/v2/task_map.json"
    assert history[1]["is_current"] is True


# --- W3a coverage: orchestrator-agent task_map.ready path (live refactor config) ---

class _Loaded:
    def __init__(self, feature_id, task_map_ref, task_map_path=None):
        self.feature_id = feature_id
        self.pdd_id = feature_id
        self.task_map_ref = task_map_ref
        self.task_map_path = task_map_path


def test_task_map_version_manifest_published_for_orchestrator_path(tmp_path):
    # The live refactor config's task_map comes from the orchestrator agent's
    # task_map.ready (not the plan-stage aggregate). _publish_task_map_version_
    # manifest must emit an artifact.manifest.published(kind=task_map) so the
    # version chain populates. Verified by feeding the emitted event through
    # build_task_map_history.
    from zf.runtime.orchestrator import Orchestrator

    captured = []

    class _Writer:
        def append(self, event):
            captured.append(event)
            return event

    orch = Orchestrator.__new__(Orchestrator)
    orch.event_writer = _Writer()

    tm = tmp_path / "task_map.json"
    tm.write_text('{"tasks": []}', encoding="utf-8")
    orch._publish_task_map_version_manifest(
        loaded=_Loaded("CJMIN-R6", ".zf/artifacts/CJMIN-R6/task_map.json", tm),
        trace_id="t1",
        is_replan=False,
    )
    assert len(captured) == 1
    ev = captured[0]
    assert ev.type == "artifact.manifest.published"
    assert ev.task_id in (None, "")  # reactor-collision-safe (early-returns)
    assert ev.payload["role"] != "orchestrator"
    ref = ev.payload["artifact_refs"][0]
    assert ref["kind"] == "task_map"
    assert ref["sha256"]  # hashed the file
    assert ev.payload["feature_id"] == "CJMIN-R6"


def test_refactor_plan_materializes_canonical_task_map_port(tmp_path):
    task_map = {'schema_version': 'task-map.v1', 'tasks': [{'task_id': 'CALC-001'}]}
    projection = project_refactor_artifacts(
        state_dir=tmp_path / '.state',
        manifest={'fanout_id': 'fanout-port-plan'},
        success_event='zaofu.refactor.plan.ready',
        synth_event=ZfEvent(type='fanout.synth.completed', actor='plan-critic', payload={
            'report': {'review_artifact_ref': 'artifacts/review.md',
                'refactor_plan_md': 'Extract one shared validator.',
                'gates': [{'command': 'python -m unittest discover -v'}],
                'plan_ports': [{'logical_name': 'task_map', 'body': task_map}]},
        }),
    )
    assert projection is not None and projection.ok, projection.diagnostics
    assert projection.payload['task_count'] == 1
    assert json.loads(Path(projection.payload['task_map_ref']).read_text()) == task_map


@pytest.mark.parametrize('variant', ['ref', 'bad_digest', 'missing_ref', 'duplicate', 'conflict', 'invalid_body'])
def test_refactor_task_map_port_provenance_and_conflicts(tmp_path, variant):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar

    state = tmp_path / '.state'
    task_map = {'tasks': [{'task_id': 'TASK-ONE'}]}
    descriptor = write_immutable_json_sidecar(state, task_map, root='plan-ports',
        kind='task_map', schema_version='task-map.v1', created_by='test', source_event_id='source')
    port = {'logical_name': 'task_map', 'ref': descriptor['ref'], 'sha256': descriptor['sha256']}
    report = {'review_artifact_ref': 'artifacts/review.md', 'refactor_plan_md': 'One task.',
              'gates': [{'command': 'python -m unittest discover -v'}], 'plan_ports': [port]}
    if variant == 'bad_digest':
        port['sha256'] = 'f' * 64
    elif variant == 'missing_ref':
        port['ref'] = 'artifacts/missing.json'
    elif variant == 'duplicate':
        report['plan_ports'].append(dict(port))
    elif variant == 'conflict':
        report['task_map'] = {'tasks': [{'task_id': 'TASK-OTHER'}]}
    elif variant == 'invalid_body':
        port['body'] = {'tasks': 'not a list'}
    projection = project_refactor_artifacts(state_dir=state,
        manifest={'fanout_id': 'fanout-port-identity'}, success_event='zaofu.refactor.plan.ready',
        synth_event=ZfEvent(type='fanout.synth.completed', actor='plan-critic', payload={'report': report}))
    assert projection.ok is (variant == 'ref')
    if variant == 'ref':
        assert json.loads(Path(projection.payload['task_map_ref']).read_text()) == task_map
    else:
        assert any('task_map port invalid' in diagnostic for diagnostic in projection.diagnostics)


@pytest.mark.parametrize('tamper', ['', 'artifact', 'candidate', 'fanout'])
def test_refactor_projection_consumes_exact_critic_reviewed_ref_only_plan(tmp_path, tamper):
    from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar

    state = tmp_path / '.state'
    task_map = {'schema_version': 'task-map.v1', 'tasks': [{'task_id': 'CALC-001'}]}
    task_ref = write_immutable_json_sidecar(state, task_map, root='plan-ports',
        kind='task_map', schema_version='task-map.v1', created_by='test', source_event_id='plan')
    report = {'task_map_ref': task_ref['ref'], 'review_artifact_ref': 'artifacts/review.md',
              'refactor_plan_md': 'One atomic task.', 'gates': [{'command': 'python -m unittest discover -v'}]}
    manifest = {'fanout_id': 'fanout-ref-plan', 'stage_id': 'flow-plan', 'workflow_run_id': 'run-one'}
    bound = build_plan_synth_call_payload(state_dir=state, project_root=tmp_path,
        manifest=manifest, reports=[{'child_id': 'planner', 'report': report}],
        run_id='attempt-critic', role_instance='critic')
    payload = {**bound, 'report': report}
    if tamper == 'artifact':
        (state / task_ref['ref']).write_text('{"tasks": []}')
    elif tamper == 'candidate':
        payload['reviewed_plan_candidate_digest'] = 'f' * 64
    elif tamper == 'fanout':
        payload['fanout_id'] = 'fanout-other'
    projection = project_refactor_artifacts(state_dir=state, manifest=manifest,
        success_event='zaofu.refactor.plan.ready',
        synth_event=ZfEvent(type='fanout.synth.completed', actor='critic', payload=payload))
    assert projection.ok is (not tamper), projection.diagnostics
    if not tamper:
        assert projection.payload['task_count'] == 1
        assert json.loads(Path(projection.payload['task_map_ref']).read_text()) == task_map
    else:
        assert any('task_map port invalid' in item for item in projection.diagnostics)
