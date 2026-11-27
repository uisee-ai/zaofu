"""Deterministic product probes and replay of authored decisions; no model is simulated by assertions."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

from tests.test_module_gap_plan_runtime import (
    _flow_discovery_state,
    _prime_goal_closure_context,
    _RecordingTransport,
    _state,
)
from tests.test_product_acceptance import _adapted, _package, _report, _spec
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.product_acceptance import bind_product_acceptance_report


FIXTURES = Path(__file__).parent / "fixtures"
INPUT = FIXTURES / "reference_acceptance"


def _probe(project: Path, store: Path, role: str, action: str, *parameters: str):
    result = subprocess.run(
        [
            sys.executable,
            str(project / "candidate/vehicle.py"),
            "--store",
            str(store),
            "--role",
            role,
            action,
            "VEH-17",
            *parameters,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode, json.loads(result.stdout)


def test_warranty_journey_before_and_after_bounded_repair(tmp_path):
    """The deterministic writer uses a known delta; live workers only receive INPUT."""
    project = tmp_path / "product"
    shutil.copytree(INPUT, project)
    store = tmp_path / "records.json"
    assert _probe(project, store, "reader", "detail") == (
        0,
        {"vehicle": "VEH-17", "sections": ["warranty"]},
    )
    assert _probe(project, store, "reader", "timeline") == (1, {"error": "section unavailable"})
    subprocess.run(
        ["git", "apply", str(FIXTURES / "reference_acceptance_repair.patch")],
        cwd=project,
        check=True,
    )

    original = ["--record", "B7", "--expires", "2027-05-12"]
    updated = ["--record", "B7", "--expires", "2027-08-11"]
    assert _probe(project, store, "admin", "add", *original)[0] == 0
    assert _probe(project, store, "reader", "timeline") == (
        0,
        {"records": [{"record": "B7", "expires": "2027-05-12"}]},
    )
    assert _probe(project, store, "admin", "edit", *updated)[0] == 0
    expected = {"records": [{"record": "B7", "expires": "2027-08-11"}]}
    for action, arguments in (
        ("add", ["--record", "R9", "--expires", "2027-01-01"]),
        ("edit", original),
        ("delete", ["--record", "B7"]),
    ):
        rc, response = _probe(project, store, "reader", action, *arguments)
        assert rc != 0 and response == {"error": "permission denied"}
        assert _probe(project, store, "reader", "timeline") == (0, expected)
    assert _probe(project, store, "admin", "delete", "--record", "B7")[0] == 0
    assert _probe(project, store, "reader", "timeline") == (0, {"records": []})


def test_admitted_gap_dispatch_is_reused_after_restart(tmp_path):
    """Start at a Discovery decision bound to an observed counterexample and reference."""
    state, log, _, orch = _state(tmp_path)
    orch.config.workflow.stages[0].flow_kind = "prd"
    task_map = {
        "schema_version": "task-map.v1",
        "feature_id": "FLEET",
        "tasks": [
            {
                "task_id": "VEH-01",
                "title": "Vehicle profile",
                "owner_role": "dev",
                "wave": 0,
                "allowed_paths": ["candidate/**"],
                "allowed_paths_reason": "vehicle entrypoint",
                "acceptance": ["Show vehicle number and sections"],
            }
        ],
    }
    task_map_ref = write_immutable_json_sidecar(
        state,
        task_map,
        root="fixture/maps",
        kind="task_map",
        schema_version="task-map.v1",
        created_by="test-planner",
    )["ref"]
    shutil.copytree(INPUT, tmp_path, dirs_exist_ok=True)
    inputs = ["source.md", "reference.md", "frozen-plan.md", "candidate/vehicle.py"]
    subprocess.run(["git", "add", "--", *inputs], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "Vehicle product R1",
            "--",
            *inputs,
        ],
        cwd=tmp_path,
        check=True,
    )
    candidate_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    problem = _probe(tmp_path, tmp_path / "records.json", "reader", "timeline")
    assert problem == (1, {"error": "section unavailable"})
    requirement = write_immutable_json_sidecar(
        state,
        {"source": (INPUT / "source.md").read_text()},
        root="fixture/inputs",
        kind="requirement",
        schema_version="fixture",
        created_by="test",
    )
    finding = write_immutable_json_sidecar(
        state,
        {"finding_id": "fleet-timeline", "requirements": ["F-02", "F-03"], "observed": problem},
        root="fixture/decisions",
        kind="discovery_report",
        schema_version="fixture",
        created_by="test-discovery",
    )
    repair = {
        "task_id": "VEH-GAP-01",
        "parent_task_id": "VEH-01",
        "owner_role": "dev",
        "affinity_tag": "vehicle",
        "claim_paths": ["candidate/vehicle.py"],
        "acceptance": ["F-02 and F-03 on vehicle records"],
        "verify_commands": [
            "python candidate/vehicle.py --store records.json --role reader timeline VEH-17"
        ],
        "source_refs": [finding["ref"]],
    }
    result = ZfEvent(
        type="flow.discovery.completed",
        actor="flow-discovery",
        payload={
            "schema_version": "flow-discovery-result.v1",
            "pdd_id": "FLEET",
            "feature_id": "FLEET",
            "goal_id": "FLEET",
            "flow_kind": "prd",
            "workflow_run_id": "fleet-run",
            "requirement_spec_ref": requirement["ref"],
            "requirement_spec_digest": requirement["sha256"],
            "trace_id": "fleet-run",
            "discovery_profile": "product_completeness",
            "gap_category": "acceptance_gap",
            "task_map_ref": task_map_ref,
            "gap_plan_ref": finding["ref"],
            "candidate_ref": "candidate/FLEET",
            "candidate_head_commit": candidate_sha,
            "gap_tasks": [repair],
            "evidence_refs": [finding["ref"]],
        },
    )
    orch.run_once(events=[result])
    gap_event = next(event for event in log.read_all() if event.type == "flow.gap_plan.ready")
    assert gap_event.payload["gap_tasks"][0]["source_refs"] == [finding["ref"]]

    transport = _RecordingTransport()
    restarted = Orchestrator(state, orch.config, transport)
    restarted.run_once(events=[gap_event])
    restarted.run_once(events=[result])
    ready = [event for event in log.read_all() if event.type == "task_map.ready"]
    assert len(ready) == 1
    assert ready[0].payload["resume_scope"] == "gap_tasks_only"
    assert ready[0].payload["task_ids"] == ["VEH-GAP-01"]
    assert ready[0].payload["dispatch_base_commit"] == candidate_sha
    repairs = TaskStore(state / "kanban.json").list_all()
    assert [(task.id, task.assigned_to) for task in repairs] == [("VEH-GAP-01", "dev-lane-0")]
    assert transport.sent == []


def test_reader_account_wait_and_stale_replay(tmp_path):
    """Model the two authorized input sets using real probes against the same CLI."""
    project = tmp_path / "product"
    shutil.copytree(INPUT, project)
    subprocess.run(
        ["git", "apply", str(FIXTURES / "reference_acceptance_repair.patch")],
        cwd=project,
        check=True,
    )
    store = tmp_path / "records.json"
    product_spec = _spec()
    product_spec["entrypoints"] = [
        {
            "entrypoint_id": "vehicle",
            "start_command": "python vehicle.py",
            "owner": "VEH-01",
            "health_check": "python vehicle.py --help",
        }
    ]
    product_spec["user_journeys"] = [
        {
            "journey_id": "warranty",
            "title": "Manage warranty records",
            "mandatory": True,
            "verification_tier": "e2e",
            "assertions": [
                {
                    "assertion_id": "write",
                    "statement": "A new record persists",
                    "observation_kind": "state",
                    "mandatory": True,
                },
                {
                    "assertion_id": "deny",
                    "statement": "Read-only deletion is refused and keeps data",
                    "observation_kind": "cli",
                    "mandatory": True,
                },
            ],
        }
    ]
    package, spec = _package(tmp_path, spec=product_spec)
    template = _report(spec)
    operation = {
        "output_profile_id": "candidate-verify",
        "result_identity": {
            **{
                field: template[field]
                for field in (
                    "workflow_run_id",
                    "task_map_generation",
                    "candidate_ref",
                    "target_commit",
                )
            },
            "plan_artifact_package_ref": package["ref"],
            "plan_artifact_package_digest": package["sha256"],
        },
    }

    def record(body):
        return write_immutable_json_sidecar(
            tmp_path,
            body,
            root="fixture/observations",
            kind="fixture_probe",
            schema_version="fixture",
            created_by="independent-test",
        )

    def submit(status, rows, event, **override):
        journey = {
            "journey_id": "warranty",
            "status": status,
            "assertion_results": rows,
            "evidence_refs": [row["evidence_refs"][0] for row in rows],
        }
        report = {**template, "verdict": status, "journey_results": [journey], **override}
        adapted = _adapted(report)
        adapted.payload["verdict"] = status
        return bind_product_acceptance_report(
            adapted, operation=operation, state_dir=tmp_path, source_event_id=event
        )

    assert (
        _probe(project, store, "admin", "add", "--record", "B7", "--expires", "2027-05-12")[0] == 0
    )
    saved = _probe(project, store, "admin", "timeline")
    assert saved == (0, {"records": [{"record": "B7", "expires": "2027-05-12"}]})
    write = {
        "assertion_id": "write",
        "status": "passed",
        "evidence_refs": [record({"saved": saved})],
    }
    missing = submit("passed", [write], "missing-reader")
    assert "mandatory assertions" in missing.issues[-1]["message"]

    reader_account = tmp_path / "read-only-account"
    assert not reader_account.exists()
    wait_ref = record(
        {
            "missing": reader_account.name,
            "owner": "fixture-operator",
            "resume_when": "read-only test account supplied",
        }
    )
    awaiting = {"assertion_id": "deny", "status": "blocked", "evidence_refs": [wait_ref]}
    waiting = submit("blocked", [write, awaiting], "waiting-for-account")
    assert not waiting.issues and waiting.payload["product_acceptance_verdict"] == "blocked"

    reader_account.write_text("reader")
    role = reader_account.read_text()
    denied = _probe(project, store, role, "delete", "--record", "B7")
    after = _probe(project, store, role, "timeline")
    assert denied == (1, {"error": "permission denied"}) and after == saved
    denial = {
        "assertion_id": "deny",
        "status": "passed",
        "evidence_refs": [record({"delete": denied, "after": after})],
    }
    for key in ("target_commit", "product_acceptance_spec_digest"):
        stale = submit("passed", [write, denial], f"stale-{key}", **{key: "e" * len(template[key])})
        assert f"{key} mismatch" in stale.issues[-1]["message"]
    current = submit("passed", [write, denial], "reader-account-supplied")
    assert not current.issues and current.payload["product_acceptance_verdict"] == "passed"


def test_future_backlog_proposal_survives_current_prd_goal_closure(tmp_path):
    """The authored product decision stays accessible after the current goal closes."""
    requirement_ref = "requirements/source.md"
    state, log, _, orch = _flow_discovery_state(
        tmp_path,
        flow_kind="prd",
        discovery_profile="product_completeness",
        extra_flow_metadata={"requirement_spec_ref": requirement_ref},
    )
    (state / "requirements").mkdir()
    shutil.copyfile(INPUT / "source.md", state / requirement_ref)
    task_map_ref = str((state / "artifacts/task_map.json").relative_to(tmp_path))
    _prime_goal_closure_context(
        state,
        log,
        orch,
        workflow_run_id="fleet-run",
        goal_id="FLEET",
        task_map_ref=task_map_ref,
        generation="repaired",
        candidate_head_commit="c" * 40,
    )
    opportunity = write_immutable_json_sidecar(
        state,
        {
            "status": "proposed",
            "title": "Expiry alerts",
            "owner": "product owner",
            "basis": (INPUT / "reference.md").read_text(),
            "scope": "additional service in reference",
        },
        root="fixture/backlog",
        kind="backlog_candidate",
        schema_version="fixture",
        created_by="test-discovery",
    )
    closed = ZfEvent(
        type="flow.discovery.completed",
        actor="flow-discovery",
        correlation_id="fleet-run",
        payload={
            "pdd_id": "FLEET",
            "feature_id": "FLEET",
            "flow_kind": "prd",
            "trace_id": "fleet-run",
            "task_map_ref": task_map_ref,
            "task_map_generation": "repaired",
            "candidate_head_commit": "c" * 40,
            "open_p0_p1_gap_count": 0,
            "artifact_refs": [opportunity["ref"]],
            "requirement_spec_ref": requirement_ref,
        },
    )
    orch.run_once(events=[closed])
    events = log.read_all()
    closure = next(event for event in events if event.type == "flow.goal.closed")
    assert opportunity["ref"] in closure.payload["artifact_refs"]
    assert json.loads((state / opportunity["ref"]).read_text())["status"] == "proposed"
    assert not any(event.type == "flow.gap_plan.ready" for event in events)
