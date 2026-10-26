from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.operator_plan_preview import build_plan_preview
from zf.runtime.operator_inbox import build_operator_inbox
from tests.test_writer_fanout_runtime import _approval_orch


def _artifacts(tmp_path: Path):
    state = tmp_path / "runtime"
    (state / "artifacts").mkdir(parents=True)
    (state / "kanban.json").write_text("[]\n")
    (state / "feature_list.json").write_text("[]\n")
    full = "# Complete plan\n\nArchitecture\n\n" + "Business rule\n" * 1000 + "END-OF-PLAN\n"
    (state / "artifacts/plan.md").write_text(full)
    (state / "artifacts/digest.md").write_text("# Digest\nMechanical checks only\n")
    (state / "artifacts/tasks.json").write_text(json.dumps({"tasks": []}))
    payload = {
        "plan_id": "evt-plan", "task_map_ref": "artifacts/tasks.json",
        "plan_artifact_ref": "artifacts/plan.md", "digest_ref": "artifacts/digest.md",
        "plan_artifact_digest": hashlib.sha256(full.encode()).hexdigest(),
    }
    return state, full, payload


def _preview(state, payload, *prior):
    request = ZfEvent(type="plan.approval.requested", actor="zf-cli", payload=payload)
    return build_plan_preview(state, iter([*prior, request]), plan_id="evt-plan", project_root=state.parent)


def test_full_plan_is_primary_and_digest_is_separate(tmp_path):
    state, full, payload = _artifacts(tmp_path)
    result = _preview(state, payload)
    assert result["markdown"] == full
    assert result["content_kind"] == "full_plan"
    assert result["refs"]["markdown_ref"] == payload["plan_artifact_ref"]
    assert result["digest_markdown"].startswith("# Digest")


@pytest.mark.parametrize("source_index,expected", [
    ({}, []),
    ({"authority_refs": None}, []),
    ({"authority_refs": "artifacts/plan.md"}, []),
    ({"authority_refs": {"ref": "artifacts/plan.md"}}, []),
    ({"authority_refs": ["artifacts/plan.md"]}, ["artifacts/plan.md"]),
])
def test_source_index_optional_authority_refs_preserve_preview(tmp_path, source_index, expected):
    state, full, payload = _artifacts(tmp_path)
    (state / "artifacts/source_index.json").write_text(json.dumps(source_index))
    payload["source_index_ref"] = "artifacts/source_index.json"

    result = _preview(state, payload)

    assert result["status"] == "pending"
    assert result["markdown"] == full
    assert result["authority_refs"] == expected


def test_review_dossier_backfills_bound_decision_register(tmp_path):
    state, _, payload = _artifacts(tmp_path)
    full = (
        "# Complete plan\n\n"
        "## Owner decision register\n\n"
        "| ID | Question | Options | Recommended | Blocking evidence |\n"
        "|---|---|---|---|---|\n"
        "| CR01 | Choose the clock contract? | Keep source semantics | Keep source | T01/T02 |\n\n"
        "## Trace\n\n"
        "All evidence is NOT_RUN.\n"
    )
    plan_path = state / "artifacts/plan.md"
    plan_path.write_text(full, encoding="utf-8")
    payload["plan_artifact_digest"] = hashlib.sha256(full.encode()).hexdigest()
    result = _preview(state, payload)

    assert result["decision_count"] == 1
    assert result["owner_decision_items"][0]["decision_id"] == "CR01"
    assert "**待决事项**：`1` 项" in result["review_dossier_markdown"]
    assert "Choose the clock contract?" in result["review_dossier_markdown"]


def test_preview_renders_the_bound_task_map_for_human_review(tmp_path):
    state, _, payload = _artifacts(tmp_path)
    task_map = {
        "schema_version": "task-map.v1",
        "tasks": [{
            "task_id": "T01",
            "title": "Identity and asset catalog",
            "owner_role": "dev-data",
            "wave": 1,
            "blocked_by": [],
            "allowed_paths": ["app/autodataloop/identity.py"],
            "exclusive_files": ["app/tests/test_t01.py"],
            "source_key": "WP14-A/T01",
            "source_ref": "docs/prd/spec.md#appendix-b",
            "source_excerpt": "Stable identity and provenance.",
            "acceptance_criteria": [{
                "id": "T01-AC1",
                "statement": "Every input receives a stable asset ID.",
            }],
            "owner_decision_ids": ["CR03"],
            "external_prerequisite_ids": ["PREREQ-01"],
            "explicit_non_goals": ["Do not publish unverified assets."],
            "validation": {"commands": [{
                "id": "CMD-T01",
                "command": "pytest app/tests/test_t01.py -q",
            }]},
        }, {
            "task_id": "T02",
            "title": "Contracts",
            "owner_role": "dev-contract",
            "wave": 2,
            "blocked_by": ["T01"],
            "acceptance_criteria": [],
        }],
    }
    task_ref = state / "artifacts/tasks.json"
    task_ref.write_text(json.dumps(task_map), encoding="utf-8")
    payload["task_count"] = 2
    result = _preview(state, payload)

    row = result["task_map_summary"]["tasks"][0]
    assert row["owner_role"] == "dev-data"
    assert row["wave"] == "1"
    assert row["blocked_by"] == ""
    assert row["acceptance_criteria"][0]["id"] == "T01-AC1"
    assert "## Task Map（完整拆分）" in result["task_map_markdown"]
    assert "T01-AC1" in result["task_map_markdown"]
    assert "CMD-T01" in result["task_map_markdown"]
    assert "T02" in result["task_map_markdown"]


def test_owner_decision_checkpoint_exposes_the_same_full_plan_preview(tmp_path):
    state, full, payload = _artifacts(tmp_path)
    request = ZfEvent(
        id="evt-owner-plan",
        type="approval.requested",
        actor="zf-cli",
        payload={
            **payload,
            "owner_route": "plan_synth_owner_decision",
            "plan_id": "evt-owner-plan",
            "plan_preview": True,
            "approval_ref": "plan-owner-checkpoint",
            "owner_decision_items": [{"decision_id": "CR01", "question": "Approve?"}],
            "approve_action": "replan-approve",
            "reject_action": "replan-reject",
        },
    )
    result = build_plan_preview(
        state,
        [request],
        plan_id="evt-owner-plan",
        project_root=state.parent,
    )
    assert result["markdown"] == full
    assert result["content_kind"] == "full_plan"
    assert result["actions"]["approve"] == "replan-approve"
    assert result["owner_decision_items"][0]["decision_id"] == "CR01"

    inbox = build_operator_inbox(
        state,
        [request],
        project_root=state.parent,
    )
    item = inbox["items"][0]
    assert item["kind"] == "approval"
    assert item["plan_id"] == "evt-owner-plan"
    assert item["owner_route"] == "plan_synth_owner_decision"
    assert item["preview"]["available"] is True


def test_legacy_owner_checkpoint_backfills_candidate_refs_from_source_event(tmp_path):
    state, full, payload = _artifacts(tmp_path)
    source = ZfEvent(
        id="evt-synth-source",
        type="fanout.synth.completed",
        actor="plan-critic",
        payload={
            "previous_plan_candidate_refs": [
                {
                    "artifact_id": "prd-plan.md",
                    "ref": payload["plan_artifact_ref"],
                    "sha256": payload["plan_artifact_digest"],
                },
                {
                    "artifact_id": "task_map.json",
                    "ref": payload["task_map_ref"],
                },
            ],
        },
    )
    request = ZfEvent(
        id="evt-owner-request",
        type="approval.requested",
        actor="zf-cli",
        payload={
            "approval_ref": "owner-checkpoint",
            "owner_route": "plan_synth_owner_decision",
            "source_event_id": source.id,
            "owner_decision_items": [],
            "approve_action": "replan-approve",
            "reject_action": "replan-reject",
        },
    )
    events = [source, request]
    inbox = build_operator_inbox(state, events, project_root=state.parent)
    item = inbox["items"][0]
    assert item["plan_id"] == source.id
    assert item["preview"]["available"] is True
    result = build_plan_preview(
        state,
        events,
        plan_id=source.id,
        project_root=state.parent,
    )
    assert result["markdown"] == full


def test_legacy_request_recovers_only_its_exact_trigger(tmp_path):
    state, full, payload = _artifacts(tmp_path)
    trigger = ZfEvent(id="evt-plan", type="task_map.ready", actor="planner", payload=dict(payload))
    payload.pop("plan_artifact_ref")
    payload.pop("plan_artifact_digest")
    result = _preview(state, payload, trigger)
    assert result["markdown"] == full
    assert result["source_event_id"] == trigger.id


def test_preview_uses_bound_synth_plan_evidence_when_declared_plan_path_is_stale(tmp_path):
    state, full, payload = _artifacts(tmp_path)
    canonical_ref = (
        "artifacts/fanouts/fanout-prd-plan-1/planner/docs/plans/"
        "prd-plan-planner-plan.md"
    )
    canonical = state / canonical_ref
    canonical.parent.mkdir(parents=True)
    canonical.write_text(full)
    synth = ZfEvent(
        id="evt-synth",
        type="fanout.synth.completed",
        actor="plan-critic",
        payload={"evidence_refs": [canonical_ref]},
    )
    trigger = ZfEvent(
        id="evt-plan",
        type="task_map.ready",
        actor="zf-cli",
        payload={**payload, "synth_event_id": synth.id},
    )
    request = ZfEvent(
        id="evt-approval",
        type="plan.approval.requested",
        actor="zf-cli",
        payload={
            "plan_id": trigger.id,
            "task_map_ref": payload["task_map_ref"],
            "plan_artifact_ref": "docs/plans/prd-plan-synth-plan.md",
            "digest_ref": payload["digest_ref"],
        },
    )
    result = build_plan_preview(
        state, [synth, trigger, request], plan_id=trigger.id, project_root=state.parent,
    )
    assert result["markdown"] == full
    assert result["content_kind"] == "full_plan"
    assert result["refs"]["markdown_ref"] == canonical_ref
    assert any(
        item["name"] == "accepted_plan" and item["ref"] == canonical_ref
        for item in result["artifacts"]
    )


def test_preview_prefers_planning_bundle_canonical_plan_over_register_port(tmp_path):
    from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
    from tests.test_plan_artifact_package import _run_contract

    state, _, payload = _artifacts(tmp_path)
    fanout = "fanout-prd-plan-bundle"
    canonical_ref = (
        f"artifacts/fanouts/{fanout}/planner/docs/plans/prd-plan-planner-plan.md"
    )
    register_ref = (
        f"artifacts/fanouts/{fanout}/planner/docs/plans/decision-register-v14.md"
    )
    bundle_ref = "artifacts/plan-ports/planning-result.json"
    canonical = (
        "# Complete PRD Plan\n\n"
        "## Owner 决策项\n\n"
        "| ID | Question | Options | Recommended | Blocking evidence |\n"
        "|---|---|---|---|---|\n"
        "| CR01 | Choose the clock contract? | Keep source semantics | Keep source | T01/T02 |\n\n"
        "## Task Map\n\n"
        "T01 through T10 remain pending approval.\n"
    )
    register = "# Decision Register\n\n## Status policy\n\n| ID | Question | Status |\n|---|---|---|\n| CR01 | Choose the clock contract? | PENDING_APPROVAL |\n"
    for ref, body in ((canonical_ref, canonical), (register_ref, register)):
        path = state / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    bundle = {
        "schema_version": "plan-preview-bundle.v1",
        "plan_ref": "docs/plans/prd-plan-planner-plan.md",
        "decision_register_ref": "docs/plans/decision-register-v14.md",
    }
    bundle_path = state / bundle_ref
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    task_ref = payload["task_map_ref"]
    ports = [
        {
            "logical_name": "planning_result",
            "artifact_kind": "planning_result",
            "producer_stage_id": "prd-plan",
            "ref": bundle_ref,
            "schema_version": "plan-preview-bundle.v1",
            "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        },
        {
            "logical_name": "accepted_plan",
            "artifact_kind": "accepted_plan",
            "producer_stage_id": "prd-plan",
            "ref": register_ref,
            "schema_version": "",
            "sha256": hashlib.sha256(register.encode()).hexdigest(),
        },
        {
            "logical_name": "task_map",
            "artifact_kind": "task_map",
            "producer_stage_id": "prd-plan",
            "ref": task_ref,
            "schema_version": "task-map.v1",
            "sha256": hashlib.sha256((state / task_ref).read_bytes()).hexdigest(),
        },
    ]
    package = build_plan_artifact_package(
        workflow_run_id="run-bundle", flow_kind="prd", producer_stage_id="prd-plan",
        run_contract=_run_contract(state), plan_revision="r1",
        task_map_generation="g1", produced=ports, required_ports=[],
    )
    package_desc = write_plan_artifact_package(state, package)
    payload.update({
        "workflow_run_id": "run-bundle",
        "fanout_id": fanout,
        "plan_artifact_ref": "docs/plans/prd-plan-synth-plan.md",
        "plan_artifact_package_ref": package_desc["ref"],
        "plan_artifact_package_digest": package_desc["sha256"],
    })
    trigger = ZfEvent(
        id="evt-plan", type="task_map.ready", actor="planner",
        payload={**payload, "fanout_id": fanout},
    )
    request = ZfEvent(
        id="evt-approval", type="plan.approval.requested", actor="zf-cli",
        payload={**payload, "plan_id": trigger.id},
    )
    result = build_plan_preview(
        state, [trigger, request], plan_id=trigger.id, project_root=state.parent,
    )
    assert result["markdown"] == canonical
    assert result["refs"]["markdown_ref"] == canonical_ref
    assert result["decision_count"] == 1
    assert any(
        item["name"] == "accepted_plan" and item["ref"] == canonical_ref
        for item in result["artifacts"]
    )


@pytest.mark.parametrize("mismatch", ["event_id", "task_map_ref", "pdd_id"])
def test_legacy_request_does_not_borrow_other_plan(tmp_path, mismatch):
    state, _, payload = _artifacts(tmp_path)
    source = {**payload, "pdd_id": "PDD-1"}
    payload["pdd_id"] = "PDD-1"
    event_id = "evt-plan"
    if mismatch == "event_id":
        event_id = "other-plan"
    else:
        source[mismatch] = "different"
    trigger = ZfEvent(id=event_id, type="task_map.ready", actor="planner", payload=source)
    payload.pop("plan_artifact_ref")
    payload.pop("plan_artifact_digest")
    result = _preview(state, payload, trigger)
    assert result["content_kind"] == "digest_only"
    assert "END-OF-PLAN" not in result["markdown"]


@pytest.mark.parametrize("failure", ["missing", "digest", "outside", "symlink", "binary"])
def test_declared_full_plan_failure_never_silently_becomes_digest(tmp_path, failure):
    state, _, payload = _artifacts(tmp_path)
    plan = state / payload["plan_artifact_ref"]
    if failure == "missing":
        plan.unlink()
    elif failure == "digest":
        plan.write_text("changed after approval request")
    elif failure == "binary":
        plan.write_bytes(b"\xff\xfe")
    else:
        outside = tmp_path.parent / f"{tmp_path.name}-other-project.md"
        outside.write_text("PRIVATE-OTHER-PROJECT")
        if failure == "outside":
            payload["plan_artifact_ref"] = str(outside)
        else:
            plan.unlink()
            plan.symlink_to(outside)
    result = _preview(state, payload)
    assert result["content_kind"] == "unavailable"
    assert result["content_error"]
    assert "PRIVATE-OTHER-PROJECT" not in result["markdown"]
    assert result["markdown"] != result["digest_markdown"]


def test_real_approval_request_preserves_full_plan_and_preview_is_read_only(tmp_path):
    state, log, orch = _approval_orch(tmp_path, enabled=True)
    plan = state / "artifacts/complete-plan.md"
    plan.write_text("# Architecture and business coverage\nEND-OF-PLAN\n")
    trigger = ZfEvent(type="task_map.ready", actor="zf-cli", correlation_id="trace-1", payload={
        "pdd_id": "F-11111111", "plan_artifact_ref": "artifacts/complete-plan.md",
    })
    EventWriter(log).append(trigger)
    orch.run_once(events=[trigger])
    requests = [e for e in log.read_all() if e.type == "plan.approval.requested"]
    assert len(requests) == 1
    assert requests[0].payload["plan_artifact_ref"] == "artifacts/complete-plan.md"
    assert requests[0].payload["plan_artifact_digest"] == hashlib.sha256(plan.read_bytes()).hexdigest()
    before = (state / "events.jsonl").read_bytes()
    result = build_plan_preview(state, log.read_all(), plan_id=trigger.id, project_root=tmp_path)
    assert result["markdown"] == plan.read_text()
    assert (state / "events.jsonl").read_bytes() == before
    assert not any(e.type in {"plan.approved", "fanout.started"} for e in log.read_all())


def test_plan_preview_real_api_preserves_complete_body(tmp_path):
    from fastapi.testclient import TestClient
    from zf.core.events.log import EventLog
    from zf.web.server import create_app

    state, full, payload = _artifacts(tmp_path)
    log = EventLog(state / "events.jsonl")
    EventWriter(log).append(ZfEvent(type="plan.approval.requested", actor="zf-cli", payload=payload))
    client = TestClient(create_app(state, project_root=tmp_path))
    project = client.get("/api/snapshot").json()["project"]["project_id"]
    before = (state / "events.jsonl").read_bytes()
    response = client.get(f"/api/projects/{project}/plans/evt-plan/preview")
    assert response.status_code == 200
    assert response.json()["markdown"] == full
    assert client.get(f"/api/projects/{project}/plans/another-plan/preview").status_code == 404
    assert (state / "events.jsonl").read_bytes() == before


def test_package_attachment_is_plan_bound_and_digest_checked_via_api(tmp_path):
    from fastapi.testclient import TestClient
    from zf.core.events.log import EventLog
    from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
    from tests.test_plan_artifact_package import _fixture_port, _run_contract
    from zf.web.server import create_app

    state, _, payload = _artifacts(tmp_path)
    port = _fixture_port(state, "acceptance_matrix", "END-OF-MATRIX")
    package = build_plan_artifact_package(
        workflow_run_id="run-1", flow_kind="prd", producer_stage_id="prd-plan",
        run_contract=_run_contract(state), plan_revision="r1", task_map_generation="g1",
        produced=[port], required_ports=["acceptance_matrix"],
    )
    descriptor = write_plan_artifact_package(state, package)
    payload.update({"plan_artifact_package_ref": descriptor["ref"], "plan_artifact_package_digest": descriptor["sha256"]})
    log = EventLog(state / "events.jsonl")
    EventWriter(log).append(ZfEvent(type="plan.approval.requested", actor="zf-cli", payload=payload))
    client = TestClient(create_app(state, project_root=tmp_path))
    project = client.get("/api/snapshot").json()["project"]["project_id"]
    base = f"/api/projects/{project}/plans/evt-plan"
    before = (state / "events.jsonl").read_bytes()
    assert client.get(base + "/preview").json()["artifacts"][0]["name"] == "acceptance_matrix"
    response = client.get(base + "/artifacts/acceptance_matrix")
    assert response.status_code == 200 and "END-OF-MATRIX" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(base + "/artifacts/not-bound").status_code == 404
    assert client.get(base.replace("evt-plan", "other-plan") + "/artifacts/acceptance_matrix").status_code == 404
    (state / port["ref"]).write_text("changed matrix")
    assert client.get(base + "/artifacts/acceptance_matrix").status_code == 404
    (state / descriptor["ref"]).write_text("changed package")
    preview = client.get(base + "/preview").json()
    assert not preview["artifacts"] and "digest mismatch" in preview["artifacts_error"]
    assert (state / "events.jsonl").read_bytes() == before


def test_current_digest_uses_bound_task_commands_without_rewriting_history(tmp_path):
    from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
    from tests.test_plan_artifact_package import _run_contract

    state, full, payload = _artifacts(tmp_path)
    task_map = json.dumps({"tasks": [{
        "task_id": "SLICE-1", "allowed_paths": ["src/feature.py"], "wave": 1,
        "validation": {"commands": [{"id": "local", "command": "node --test tests/feature.mjs"}]},
    }]})
    (state / payload["task_map_ref"]).write_text(task_map)
    port = {
        "logical_name": "task_map", "artifact_kind": "task_map", "schema_version": "task-map.v1",
        "producer_stage_id": "plan", "ref": payload["task_map_ref"],
        "sha256": hashlib.sha256(task_map.encode()).hexdigest(),
    }
    package = build_plan_artifact_package(
        workflow_run_id="run-1", flow_kind="prd", producer_stage_id="plan",
        run_contract=_run_contract(state), plan_revision="r1", task_map_generation="g1",
        produced=[port], required_ports=["task_map"],
    )
    descriptor = write_plan_artifact_package(state, package)
    payload.update(task_map_ref=port["ref"], plan_artifact_package_ref=descriptor["ref"],
                   plan_artifact_package_digest=descriptor["sha256"])
    stored = (state / payload["digest_ref"]).read_bytes()
    result = _preview(state, payload)
    assert result["markdown"] == full
    assert "node --test tests/feature.mjs" in result["digest_markdown"]
    assert result["digest_source"] == "bound_task_map"
    assert "🔴" not in result["digest_markdown"]
    assert (state / payload["digest_ref"]).read_bytes() == stored
    (state / port["ref"]).write_text('{"tasks": []}')
    result = _preview(state, payload)
    assert result["digest_source"] == "unavailable"
    assert result["digest_markdown"] == ""
    assert "digest mismatch" in result["artifacts_error"]


def test_owner_preview_bundle_exposes_requirements_and_plan_ports(tmp_path):
    from zf.runtime.plan_preview_artifacts import write_plan_preview_bundle

    state, full, payload = _artifacts(tmp_path)
    requirements = state / "artifacts/requirements"
    requirements.mkdir()
    (requirements / "requirements-a.md").write_text("# Requirement A\n\nFull source body.\n")
    (requirements / "requirements-b.md").write_text("# Requirement B\n\nOpen questions.\n")
    payload.update({
        "workflow_run_id": "run-1",
        "fanout_id": "fanout-1",
        "artifact_refs": [
            payload["plan_artifact_ref"],
            {
                "path": "artifacts/requirements/requirements-a.md",
                "sha256": hashlib.sha256(
                    (requirements / "requirements-a.md").read_bytes()
                ).hexdigest(),
            },
            "artifacts/requirements/requirements-b.md",
            payload["task_map_ref"],
        ],
        "authority_refs": [
            "artifacts/requirements/requirements-a.md",
            "artifacts/requirements/requirements-b.md",
        ],
        "plan_content_profile": "derived_plan_full",
        "plan_ports": [{
            "logical_name": "acceptance_matrix",
            "schema_version": "acceptance-matrix.v1",
            "body": {
                "schema_version": "acceptance-matrix.v1",
                "status": "ready",
                "metadata": {"enrichment_contract": {"status": "fulfilled"}},
                "traceability": [{"id": "R001", "status": "NOT_RUN"}],
            },
        }],
    })
    descriptor = write_plan_preview_bundle(
        state,
        payload=payload,
        refs=payload,
        project_root=tmp_path,
        source_event_id="evt-synth",
    )
    request = ZfEvent(
        id="evt-owner-bundle",
        type="approval.requested",
        actor="zf-cli",
        payload={
            **payload,
            "plan_id": "evt-owner-bundle",
            "owner_route": "plan_synth_owner_decision",
            "plan_preview_bundle_ref": descriptor["ref"],
            "plan_preview_bundle_digest": descriptor["sha256"],
            "owner_decision_items": [{"decision_id": "CR01", "question": "Approve?"}],
        },
    )
    result = build_plan_preview(
        state,
        [request],
        plan_id="evt-owner-bundle",
        project_root=tmp_path,
    )
    assert result["content_profile"] == "derived_plan_full"
    assert {item["name"] for item in result["requirement_artifacts"]} == {
        "requirement_2_requirements-a.md",
        "requirement_3_requirements-b.md",
    }
    assert any(item["name"] == "acceptance_matrix" for item in result["artifacts"])
    assert result["authority_refs"] == payload["authority_refs"]


def test_owner_preview_bundle_binds_authority_refs_when_artifact_list_omits_them(
    tmp_path,
):
    from zf.runtime.plan_preview_artifacts import write_plan_preview_bundle

    state, _full, payload = _artifacts(tmp_path)
    authority = state / "authority.md"
    authority.write_text("# Authority\n\nR001 must remain traceable.\n", encoding="utf-8")
    payload.update({
        "workflow_run_id": "run-authority",
        "authority_refs": ["authority.md"],
        "artifact_refs": [],
    })
    descriptor = write_plan_preview_bundle(
        state,
        payload=payload,
        refs=payload,
        project_root=tmp_path,
        source_event_id="evt-authority",
    )
    bundle = json.loads((state / descriptor["ref"]).read_text(encoding="utf-8"))
    assert bundle["authority_refs"] == ["authority.md"]
    assert any(item["ref"] == "authority.md" for item in bundle["artifacts"])


def test_review_packet_surfaces_cross_artifact_gaps_and_lineage(tmp_path):
    from zf.runtime.plan_preview_artifacts import write_plan_preview_bundle

    state, full, payload = _artifacts(tmp_path)
    requirement = state / "artifacts/requirements/authority.md"
    requirement.parent.mkdir(parents=True)
    requirement.write_text(
        "# Authority\n\nR001 M14-01 U14-01 import_config.yaml\n",
        encoding="utf-8",
    )
    full = "# Complete plan\n\n## Acceptance\n\n2 个任务 AC\n\nR001\n"
    (state / payload["plan_artifact_ref"]).write_text(full, encoding="utf-8")
    payload["plan_artifact_digest"] = hashlib.sha256(full.encode()).hexdigest()
    payload.update({
        "workflow_run_id": "run-r4",
        "authority_refs": ["artifacts/requirements/authority.md"],
        "artifact_refs": ["artifacts/requirements/authority.md"],
        "plan_ports": [
            {"logical_name": "acceptance_matrix", "body": {
                "schema_version": "acceptance-matrix.v1",
                "metadata": {"workflow_run_id": "run-r3"},
                "acceptance": [{"id": "T01-AC1"}],
            }},
            {"logical_name": "test_matrix", "body": {
                "schema_version": "test-matrix.v1",
                "metadata": {"workflow_run_id": "run-r3"},
                "commands": [{"id": "CMD-T01"}],
            }},
            {"logical_name": "source_index", "body": {
                "schema_version": "source-index.v1",
                "metadata": {"workflow_run_id": "run-r3"},
                "traceability": [{"trace_id": "R001"}],
            }},
            {"logical_name": "coverage_ledger", "body": {
                "schema_version": "coverage-ledger.v1",
                "metadata": {"workflow_run_id": "run-r3"},
                "entries": [{"trace_id": "R001"}],
                "uncovered": [],
            }},
            {"logical_name": "product_acceptance_spec", "body": {
                "schema_version": "product-acceptance-spec.v1",
                "metadata": {"workflow_run_id": "run-r3"},
                "user_journeys": [],
            }},
        ],
    })
    descriptor = write_plan_preview_bundle(
        state, payload=payload, refs=payload, project_root=tmp_path,
        source_event_id="evt-review-packet",
    )
    request = ZfEvent(
        id="evt-review-packet",
        type="plan.approval.requested",
        actor="zf-cli",
        payload={
            **payload,
            "plan_id": "evt-review-packet",
            "plan_preview_bundle_ref": descriptor["ref"],
            "plan_preview_bundle_digest": descriptor["sha256"],
        },
    )
    result = build_plan_preview(state, [request], plan_id="evt-review-packet", project_root=tmp_path)

    packet = result["review_packet"]
    assert packet["schema_version"] == "plan-review-packet.v1"
    assert {section["id"] for section in packet["sections"]} == {
        "prd", "sdd", "tdd", "traceability", "task_map",
        "source_coverage", "decisions", "provenance",
    }
    assert packet["status"] == "blocked"
    assert {item["code"] for item in packet["diagnostics"]} >= {
        "acceptance_count_mismatch", "artifact_lineage_mismatch",
    }
    sections = {section["id"]: section for section in packet["sections"]}
    assert sections["source_coverage"]["counts"] == {"source": 1, "covered": 1, "missing": 0}
    assert "## 1.1 审核包状态" in result["review_dossier_markdown"]
    assert "artifact_lineage_mismatch" in result["review_dossier_markdown"]
