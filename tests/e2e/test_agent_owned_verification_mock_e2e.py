"""Multi-lane light-flow E2E for design 148 contracts."""

from __future__ import annotations

import json
import shlex
import subprocess

import yaml
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.schema import (
    FanoutAggregateConfig,
    WorkflowAffinityLaneConfig,
    WorkflowStageConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.goal_completion_receipt import build_goal_completion_receipt
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_contract import hydrate_run_effective_config, load_run_contract
from zf.runtime.product_acceptance import (
    product_acceptance_binding_from_package,
    product_acceptance_report_template,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.simulation_lifecycle import emit_simulation_done
from zf.runtime.task_contract_snapshot import TASK_CONTRACT_AUTHORITY_FIELDS
from zf.runtime.result_submit import (
    SemanticResultSubmitService,
    provision_role_submit_credential,
)


DELIVERABLES = (
    ("LIGHT-148-DELIVER-001", "app/result-a.txt", "delivered-a", "lane0"),
    ("LIGHT-148-DELIVER-002", "app/result-b.txt", "delivered-b", "lane1"),
)


class _Transport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str, object]] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _manifest(state_dir: Path, fanout_id: str) -> dict:
    return json.loads(
        (state_dir / "fanouts" / fanout_id / "manifest.json").read_text(
            encoding="utf-8"
        )
    )


def _latest_event(log: EventLog, event_type: str) -> ZfEvent:
    return next(event for event in reversed(log.read_all()) if event.type == event_type)


def _success_payload(briefing_path: Path) -> dict:
    briefing = briefing_path.read_text(encoding="utf-8")
    command = briefing.split("Success command:\n```bash\n", 1)[1].split("\n```", 1)[0]
    argv = shlex.split(command)
    if "--payload-file" in argv:
        return json.loads(
            Path(argv[argv.index("--payload-file") + 1]).read_text(encoding="utf-8")
        )
    return json.loads(argv[argv.index("--payload") + 1])


def _record_required_reads(state_dir: Path, payload: dict) -> None:
    manifest = hydrate_sidecar_ref(
        state_dir,
        payload["attempt_source_manifest"],
    ).payload
    for required in payload.get("required_reads") or []:
        read_attempt_artifact(
            state_dir,
            manifest=manifest,
            source_id=required["source_id"],
            artifact_id=required["artifact_id"],
            json_path=required["json_path"],
            max_items=int(required.get("max_items") or 0),
            max_chars=int(required.get("max_chars") or 0),
        )


def _dispatch_payload(child: dict) -> dict:
    payload = dict(child.get("payload") or {})
    payload.update({key: value for key, value in child.items() if key != "payload"})
    return payload


@pytest.mark.parametrize("discovery_closeout", [False, True])
def test_light_profile_mock_e2e_exact_verify(
    tmp_path: Path,
    monkeypatch,
    discovery_closeout: bool,
) -> None:
    monkeypatch.setenv("ZF_CLI_CMD", "zf")
    workflow_run_id = "evt-light-entry-148"
    config_source = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "prod"
        / "controller"
        / "prd-light-v3.yaml"
    )
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        config_source.read_text(encoding="utf-8")
        .replace(
            "profile_sources: [common/profiles.yaml]",
            f"profile_sources: [{config_source.parent / 'common' / 'profiles.yaml'}]",
        )
        .replace(
            "  project:\n    name: prd-light-v3\n",
            "  project:\n    name: prd-light-v3\n    state_dir: .zf-148-mock\n",
        ),
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "zf-148@example.com")
    _git(tmp_path, "config", "user.name", "ZF 148 Mock")
    (tmp_path / "README.md").write_text("zf 148 mock\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        ".zf-148-mock/\nartifacts/\ndocs/intake/\nskills\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "README.md", ".gitignore", "zf.yaml")
    _git(tmp_path, "commit", "-q", "-m", "init")
    _git(tmp_path, "branch", "-M", "main")
    (tmp_path / "skills").symlink_to(
        Path(__file__).resolve().parents[2] / "skills",
        target_is_directory=True,
    )

    config = load_config(config_path)
    if discovery_closeout:
        config.workflow.flow_metadata["post_verify_discovery"] = "product_completeness"
        discovery_role = deepcopy(next(
            role for role in config.roles if role.instance_id == "verify-lane-0"
        ))
        discovery_role.name = discovery_role.instance_id = "flow-discovery"
        config.roles.append(discovery_role)
        config.workflow.stages.append(WorkflowStageConfig(
            id="prd-post-verify-discovery",
            trigger="flow.discovery.requested",
            topology="fanout_reader",
            roles=["flow-discovery"],
            aggregate=FanoutAggregateConfig(
                mode="wait_for_all",
                child_success_event="flow.discovery.child.completed",
                child_failure_event="flow.discovery.child.failed",
                success_event="flow.discovery.completed",
                failure_event="flow.discovery.failed",
            ),
        ))
    state_dir = tmp_path / ".zf-148-mock"
    config.project.root = str(tmp_path)
    config.project.state_dir = str(state_dir)
    config.runtime.git.candidate_base_ref = "main"
    config.runtime.git.ship_target_branch = "main"
    config.runtime.git.auto_ship_on_judge_passed = False
    config.orchestrator.backend = "mock"
    for role in config.roles:
        role.backend = "mock"
    dev_lane_0 = next(role for role in config.roles if role.instance_id == "dev-lane-0")
    verify_lane_0 = next(
        role for role in config.roles if role.instance_id == "verify-lane-0"
    )
    dev_lane_1 = deepcopy(dev_lane_0)
    dev_lane_1.name = dev_lane_1.instance_id = "dev-lane-1"
    verify_lane_1 = deepcopy(verify_lane_0)
    verify_lane_1.name = verify_lane_1.instance_id = "verify-lane-1"
    config.roles.extend((dev_lane_1, verify_lane_1))
    config.workflow.affinity_lanes["prd-lanes-slot"].lanes.append(
        WorkflowAffinityLaneConfig(
            id="lane1",
            impl="dev-lane-1",
            verify="verify-lane-1",
        )
    )
    for stage in config.workflow.stages:
        if stage.id == "prd-lanes-impl":
            stage.roles.append("dev-lane-1")
        elif stage.id == "prd-lanes-verify":
            stage.roles.append("verify-lane-1")
    assert config.workflow.impl_self_check_required is True
    assert config.workflow.candidate_quality_source == "task_contract_required"
    assert config.workflow.flow_metadata["topology"] == "light"
    assert config.workflow.flow_metadata["result_protocol"]["semantic_submit_profiles"] == {
        "implementation": "blocking",
        "thin-judge-goal-closure": "blocking",
        "task-verify": "blocking",
        "candidate-verify": "blocking",
    }
    package_policy = config.workflow.flow_metadata["artifact_package"]
    package_policy["required_ports"] = [
        *package_policy["required_ports"],
        "product_acceptance_spec",
    ]
    package_policy["conditional_ports"] = [
        item
        for item in package_policy.get("conditional_ports") or []
        if item != "product_acceptance_spec"
    ]
    config.workflow.flow_metadata["product_acceptance"] = {"mode": "blocking"}

    # Proposal and consumer must read the same approved two-lane policy.
    config_path.write_text(
        yaml.safe_dump(renderable_config_to_primitive(config), sort_keys=False),
        encoding="utf-8",
    )
    config = load_config(config_path)
    _git(tmp_path, "add", "zf.yaml")
    _git(tmp_path, "commit", "-q", "-m", "Pin approved two-lane mock policy")

    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    submitter = SemanticResultSubmitService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
    )
    submit_tokens = {
        role_instance: provision_role_submit_credential(
            state_dir,
            role_instance,
        ).read_text().strip()
        for role_instance in (
            "dev-lane-0",
            "dev-lane-1",
            "verify-lane-0",
            "verify-lane-1",
            "judge-prd",
                *(("flow-discovery",) if discovery_closeout else ()),
        )
    }
    actions = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=tmp_path,
        actor="e2e-operator",
        source="mock-e2e",
        surface="test",
    )
    request_payload = {
        "request_id": workflow_run_id,
        "kind": "prd",
        "objective": "Deliver two independently verified result files.",
        "source_ref": "README.md",
        "target_root": "app",
        "backend": "mock",
        "acceptance": [
            "Both result files exist with their expected deterministic content.",
        ],
        "constraints": ["Use the direct-v1 execution profile."],
        "allow_missing_env": True,
    }
    request_event = writer.append(ZfEvent(
        type="control.action.requested",
        actor="e2e-operator",
        correlation_id=workflow_run_id,
        payload={"action": "workflow-request", **request_payload},
    ))
    proposed = actions.execute(
        action="workflow-request",
        requested_action="workflow-request",
        payload=request_payload,
        requested=request_event,
    )
    assert proposed["status"] == "proposal_ready", json.dumps(
        proposed.get("blockers") or [],
        indent=2,
    )
    submit_payload = {
        "request_id": workflow_run_id,
        "intake_ref": proposed["intake_ref"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": proposed["proposal_digest"],
        "kind": "prd",
        "allow_missing_env": True,
    }
    submit_event = writer.append(ZfEvent(
        type="control.action.requested",
        actor="e2e-operator",
        correlation_id=workflow_run_id,
        payload={"action": "workflow-submit", **submit_payload},
    ))
    submitted = actions.execute(
        action="workflow-submit",
        requested_action="workflow-submit",
        payload=submit_payload,
        requested=submit_event,
    )
    assert submitted["status"] == "accepted"
    entry_event = next(
        event for event in reversed(log.read_all())
        if event.type in {"prd.requested", "workflow.invoke.requested"}
        and event.correlation_id == workflow_run_id
    )
    run_contract = load_run_contract(state_dir)
    assert run_contract is not None
    assert run_contract["workflow"]["proposal_ref"] == proposed["proposal_ref"]
    assert run_contract["workflow"]["proposal_digest"] == proposed["proposal_digest"]
    effective_config = hydrate_run_effective_config(state_dir, run_contract)
    assert effective_config["project"]["name"] == "prd-light-v3"
    assert entry_event.payload["effective_config_ref"] == run_contract[
        "config"
    ]["effective_snapshot_ref"]
    assert entry_event.payload["effective_config_digest"] == run_contract[
        "config"
    ]["effective_snapshot_digest"]
    assert entry_event.payload["run_contract_ref"]
    assert entry_event.payload["run_contract_digest"] == run_contract[
        "contract_digest"
    ]
    assert run_contract["protocols"]["execution_profile"]["roles"][
        "dev-lane-0"
    ]["default_profile"] == "bounded-direct-v1"

    task_map_ref = f"{state_dir.name}/artifacts/LIGHT-148/task_map.json"
    task_map_path = tmp_path / task_map_ref
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(json.dumps({
        "schema_version": "task-map.v1",
        "feature_id": "LIGHT-148",
        "required_plan_ports": [
            "requirement_spec",
            "goal_claim_set",
            "task_map",
            "planning_result",
            "product_acceptance_spec",
        ],
        "tasks": [{
            "task_id": task_id,
            "title": f"Create verified result {index}",
            "scope": [path],
            "allowed_paths": [path],
            "allowed_paths_reason": "independent multi-lane light deliverable",
            "owner_role": "dev",
            "affinity_tag": lane_id,
            "acceptance_criteria": [{
                "id": f"AC-RESULT-{index}",
                "text": f"{path} exists and contains {content}",
                "verification_owner": "task_verify",
                "verification_tier": "task_non_smoke",
                "verification_command_ids": [f"result-file-{index}"],
            }],
            "verification": f"grep -qx {content} {path}",
            "validation": {"commands": [{
                "id": f"result-file-{index}",
                "command": f"grep -qx {content} {path}",
                "acceptance_ids": [f"AC-RESULT-{index}"],
                "owner": "impl_self_check",
                "tier": "task_non_smoke",
                "deterministic": True,
                "reusable": True,
                "timeout_seconds": 30,
            }]},
        } for index, (task_id, path, content, lane_id) in enumerate(
            DELIVERABLES, start=1
        )],
    }), encoding="utf-8")

    transport = _Transport()
    orchestrator = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    orchestrator.run_once(events=[entry_event])
    trigger = ZfEvent(
        type="task_map.ready",
        actor="orchestrator",
        causation_id=entry_event.id,
        correlation_id=workflow_run_id,
        payload={
            "pdd_id": "LIGHT-148",
            "feature_id": "LIGHT-148",
            "workflow_run_id": workflow_run_id,
            "prd_ref": "README.md",
            "source_refs": ["README.md"],
            "task_map_ref": task_map_ref,
            "flow_kind": "prd",
            "plan_ports": [{
                "logical_name": "product_acceptance_spec",
                "schema_version": "product_acceptance_spec.v1",
                "body": {
                    "schema_version": "product_acceptance_spec.v1",
                    "assembly_owner": "LIGHT-148-DELIVER-001",
                    "entrypoints": [{
                        "entrypoint_id": "result-files",
                        "start_command": "test -f app/result-a.txt && test -f app/result-b.txt",
                        "health_check": "grep -qx delivered-a app/result-a.txt && grep -qx delivered-b app/result-b.txt",
                        "owner": "LIGHT-148-DELIVER-001",
                    }],
                    "user_journeys": [{
                        "journey_id": f"journey-result-{index}",
                        "title": f"Read delivered result {index}",
                        "mandatory": True,
                        "verification_tier": "runtime",
                        "assertions": [{
                            "assertion_id": f"result-{index}-content",
                            "statement": f"{path} contains {content}",
                            "observation_kind": "state",
                            "mandatory": True,
                        }],
                    } for index, (_task_id, path, content, _lane) in enumerate(
                        DELIVERABLES, start=1
                    )],
                    "provider_qualification": {
                        "required_for_goal": True,
                        "providers": ["mock-provider"],
                        "ttl_seconds": 300,
                    },
                },
            }],
            **{
                key: entry_event.payload[key]
                for key in (
                    "workflow_proposal_ref",
                    "workflow_proposal_digest",
                    "effective_config_ref",
                    "effective_config_digest",
                    "run_contract_ref",
                    "run_contract_digest",
                )
            },
        },
    )
    log.append(trigger)
    orchestrator.run_once(events=[trigger])
    package_event = _latest_event(log, "plan.artifact_package.admitted")
    assert package_event.payload["run_contract_digest"] == run_contract[
        "contract_digest"
    ]
    package_body = hydrate_sidecar_ref(
        state_dir,
        {
            "ref": package_event.payload["package_ref"],
            "sha256": package_event.payload["package_digest"],
        },
    ).payload
    assert "product_acceptance_spec" in package_body["required_ports"]

    impl_started = next(
        event for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "prd-lanes-impl"
    )
    impl_manifest = _manifest(state_dir, str(impl_started.payload["fanout_id"]))
    impl_children = [
        item for item in impl_manifest["children"] if item["status"] == "dispatched"
    ]
    assert {item["role_instance"] for item in impl_children} == {
        "dev-lane-0",
        "dev-lane-1",
    }
    receipt_ids: dict[str, str] = {}
    source_commits: set[str] = set()
    deliverables = {item[0]: item for item in DELIVERABLES}
    for child in impl_children:
        _record_required_reads(state_dir, _dispatch_payload(child))
        task_id, path, content, _lane_id = deliverables[child["task_id"]]
        workdir = Path(child["workdir"])
        contract = json.loads(
            (state_dir / child["contract_snapshot_ref"]).read_text(encoding="utf-8")
        )
        command = contract["verification_commands"][0]
        criterion = contract["acceptance_criteria"][0]
        target = workdir / path
        target.parent.mkdir(exist_ok=True)
        target.write_text(f"{content}\n", encoding="utf-8")
        _git(workdir, "add", path)
        _git(workdir, "commit", "-q", "-m", f"feat: deliver {task_id}")
        source_commit = _git(workdir, "rev-parse", "HEAD")
        source_commits.add(source_commit)
        receipt_id = f"receipt-{command['command_id']}"
        receipt_ids[task_id] = receipt_id
        impl_self_check = {
            "schema_version": "impl-self-check.v1",
            "workflow_run_id": contract["workflow_run_id"],
            "task_id": task_id,
            "attempt_id": child["run_id"],
            "contract_revision": contract["contract_revision"],
            "task_map_generation": contract["task_map_generation"],
            "source_commit": source_commit,
            "target_commit": source_commit,
            "contract_snapshot_ref": child["contract_snapshot_ref"],
            "contract_snapshot_digest": child["contract_snapshot_digest"],
            **{
                key: str(contract[key])
                for key in TASK_CONTRACT_AUTHORITY_FIELDS
                if contract.get(key) not in (None, "", 0)
            },
            "command_receipts": [{
                "receipt_id": receipt_id,
                "command_id": command["command_id"],
                "command_digest": command["command_digest"],
                "target_commit": source_commit,
                "status": "passed",
                "exit_code": 0,
                "evidence_refs": [f"mock://command/{command['command_id']}"],
            }],
            "acceptance_results": [{
                "acceptance_id": criterion["acceptance_id"],
                "status": "passed",
                "command_receipt_ids": [receipt_id],
                "evidence_refs": [
                    f"mock://acceptance/{criterion['acceptance_id']}"
                ],
                "residual_risks": [],
            }],
            "evidence_refs": [f"mock://impl/{task_id}"],
            "residual_risks": [],
        }
        submitted_impl = submitter.submit(
            operation_id=child["operation_id"],
            semantic_result={
                "schema_version": "implementation-result.v1",
                "execution_status": "completed",
                "verdict": "passed",
                "target_commit": source_commit,
                "changed_files": [path],
                "evidence_refs": [f"mock://impl/{task_id}"],
                "self_check": impl_self_check,
                "known_gaps": [],
                "summary": f"implemented {task_id}",
            },
            role_instance=child["role_instance"],
            credential=submit_tokens[child["role_instance"]],
        )
        completion = next(
            item for item in log.read_all()
            if item.id == submitted_impl.canonical_event_id
        )
        assert completion.payload["semantic_result_profile"] == {
            "profile_id": "implementation",
            "revision": "1",
        }
        assert "implementation_result" not in completion.payload
        orchestrator.run_once(events=[completion])

    candidate = _latest_event(log, "candidate.ready")
    assert candidate.payload["completed_task_ids"] == sorted(deliverables)
    assert candidate.payload["candidate_head_commit"] not in source_commits
    orchestrator.run_once(events=[candidate])
    verify_started = next(
        event for event in reversed(log.read_all())
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "prd-lanes-verify"
    )
    verify_manifest = _manifest(state_dir, str(verify_started.payload["fanout_id"]))
    verify_children = [
        item for item in verify_manifest["children"] if item["status"] == "dispatched"
    ]
    assert {item["role_instance"] for item in verify_children} == {
        "verify-lane-0",
        "verify-lane-1",
    }
    verified_targets: set[str] = set()
    for verify_child in verify_children:
        verify_payload = _dispatch_payload(verify_child)
        _record_required_reads(state_dir, verify_payload)
        verify_dispatch = next(
            event for event in reversed(log.read_all())
            if event.type == "fanout.child.dispatched"
            and event.payload.get("fanout_id") == verify_manifest["fanout_id"]
            and event.payload.get("child_id") == verify_child["child_id"]
        )
        verify_briefing = Path(verify_dispatch.payload["briefing_path"])
        assert "reusable_impl_receipts" in verify_briefing.read_text(encoding="utf-8")
        verify_contract = json.loads(
            (state_dir / verify_payload["contract_snapshot_ref"]).read_text(
                encoding="utf-8"
            )
        )
        task_id = verify_payload["task_id"]
        verified_targets.add(verify_payload["target_commit"])
        product_binding = product_acceptance_binding_from_package(
            state_dir,
            {
                "ref": verify_payload["plan_artifact_package_ref"],
                "sha256": verify_payload["plan_artifact_package_digest"],
            },
        )
        product_evidence = write_immutable_json_sidecar(
            state_dir,
            {
                "schema_version": "mock-product-evidence.v1",
                "task_id": task_id,
                "target_commit": verify_payload["target_commit"],
                "journeys": ["journey-result-1", "journey-result-2"],
            },
            root="e2e/product-acceptance-evidence",
            kind="mock_product_acceptance_evidence",
            schema_version="mock-product-evidence.v1",
            created_by="mock-candidate-verify",
        )
        product_report = product_acceptance_report_template(
            spec=product_binding["spec"],
            spec_ref=product_binding["spec_ref"],
            spec_digest=product_binding["spec_digest"],
            candidate_ref=verify_payload["candidate_ref"],
            target_commit=verify_payload["target_commit"],
        )
        for journey in product_report["journey_results"]:
            journey["evidence_refs"] = [dict(product_evidence)]
            for assertion in journey["assertion_results"]:
                assertion["evidence_refs"] = [dict(product_evidence)]
        observed_at = datetime.now(timezone.utc)
        product_report["provider_qualification_receipts"] = [{
            "schema_version": "provider-qualification-receipt.v1",
            "provider": "mock-provider",
            "workflow_run_id": workflow_run_id,
            "target_commit": verify_payload["target_commit"],
            "status": "passed",
            "deterministic": False,
            "reusable": False,
            "observed_at": observed_at.isoformat(),
            "expires_at": (observed_at + timedelta(seconds=240)).isoformat(),
            "evidence_refs": [dict(product_evidence)],
        }]
        submitted_verify = submitter.submit(
            operation_id=verify_payload["operation_id"],
            semantic_result={
                "schema_version": "verification-result.v1",
                "execution_status": "completed",
                "verdict": "passed",
                "verification_owner": "task_verify",
                "verification_tier": "task_non_smoke",
                "summary": "verified",
                "evidence_refs": [f"mock://verify/{task_id}"],
                "reused_command_receipt_ids": [],
                "probe_receipts": [{
                    "probe_id": f"verify-{command['command_id']}",
                    "command_id": command["command_id"],
                    "command": command["command"],
                    "command_digest": command["command_digest"],
                    "target_commit": verify_payload["target_commit"],
                    "status": "passed",
                    "exit_code": 0,
                    "evidence_refs": [f"mock://verify/{task_id}/probe"],
                } for command in verify_contract["verification_commands"]],
                "rework_items": [],
                **({} if discovery_closeout else {
                    "product_acceptance_report": product_report,
                }),
                "requirement_results": [{
                    "acceptance_id": item["acceptance_id"],
                    "status": "passed",
                    "verification_owner": item["verification_owner"],
                    "verification_tier": item["verification_tier"],
                    "evidence_refs": [f"mock://verify/{task_id}"],
                    "findings": [],
                    "reproduction_commands": [
                        command["command"]
                        for command in verify_contract["verification_commands"]
                        if command["command_id"]
                        in item["verification_command_ids"]
                    ],
                } for item in verify_contract["acceptance_criteria"]],
            },
            role_instance=verify_child["role_instance"],
            credential=submit_tokens[verify_child["role_instance"]],
        )
        verify_result = next(
            item for item in log.read_all()
            if item.id == submitted_verify.canonical_event_id
        )
        assert verify_result.payload["semantic_result_profile"] == {
            "profile_id": "task-verify" if discovery_closeout else "candidate-verify",
            "revision": "1",
        }
        assert "verification_result" not in verify_result.payload
        orchestrator.run_once(events=[verify_result])

    test_passed = _latest_event(log, "test.passed")
    orchestrator.run_once(events=[test_passed])
    if discovery_closeout:
        # Both task results have been admitted before the global consumer runs.
        assert len([
            item for item in log.read_all() if item.type == "verify.child.completed"
        ]) == 2
        assert not any(item.type == "flow.goal.closed" for item in log.read_all())
        discovery_dispatch = _latest_event(log, "fanout.child.dispatched")
        assert discovery_dispatch.payload["stage_id"] == "prd-post-verify-discovery"
        discovery_manifest = _manifest(
            state_dir, discovery_dispatch.payload["fanout_id"],
        )
        discovery_payload = _dispatch_payload(discovery_manifest["children"][0])
        discovery_sources = hydrate_sidecar_ref(
            state_dir, discovery_payload["attempt_source_manifest"],
        ).payload["sources"]
        source_refs = {item["ref"] for item in discovery_sources}
        for child_event in log.read_all():
            if child_event.type == "verify.child.completed":
                assert child_event.payload["control_result_ref"]["ref"] in source_refs
                assert child_event.payload["call_result_envelope_ref"]["ref"] in source_refs
        assert any(
            item["source_id"] == "plan-port-product_acceptance_spec"
            for item in discovery_sources
        )
        _record_required_reads(state_dir, discovery_payload)
        submitted_discovery = submitter.submit(
            operation_id=discovery_payload["operation_id"],
            semantic_result={
                "schema_version": "workflow-read-result.v1",
                "status": "passed",
                "recommendation": "approve",
                "summary": "Full product evidence reconciled after both task readers.",
                "evidence_refs": [str(product_evidence["ref"])],
                "findings": [],
                "open_p0_p1_gap_count": 0,
                "product_acceptance_report": product_report,
            },
            role_instance="flow-discovery",
            credential=submit_tokens["flow-discovery"],
        )
        discovery_result = next(
            item for item in log.read_all()
            if item.id == submitted_discovery.canonical_event_id
        )
        orchestrator.run_once(events=[discovery_result])
        orchestrator.run_once(events=[_latest_event(log, "flow.discovery.completed")])
    goal_closed = _latest_event(log, "flow.goal.closed")
    orchestrator.run_once(events=[goal_closed])
    judge_dispatch = next(
        event for event in reversed(log.read_all())
        if event.type == "fanout.child.dispatched"
        and event.payload.get("stage_id") == "prd-lanes-final"
    )
    judge_manifest = _manifest(state_dir, str(judge_dispatch.payload["fanout_id"]))
    judge_child = next(
        item for item in judge_manifest["children"]
        if item["child_id"] == judge_dispatch.payload["child_id"]
    )
    judge_child_payload = _dispatch_payload(judge_child)
    judge_source_manifest = hydrate_sidecar_ref(
        state_dir,
        judge_child_payload["attempt_source_manifest"],
    ).payload
    for required_read in judge_child_payload["required_reads"]:
        read_attempt_artifact(
            state_dir,
            manifest=judge_source_manifest,
            source_id=required_read["source_id"],
            artifact_id=required_read["artifact_id"],
            json_path=required_read["json_path"],
        )
    judge_briefing_path = Path(judge_dispatch.payload["briefing_path"])
    judge_briefing = judge_briefing_path.read_text(encoding="utf-8")
    assert "result submit" in judge_briefing
    assert "ZF_RESULT_SUBMIT_TOKEN" not in judge_briefing
    command = judge_briefing.split(
        "Success command:\n```bash\n", 1,
    )[1].split("\n```", 1)[0]
    command_argv = shlex.split(command)
    assert "--scratch" in command_argv
    semantic_result = json.loads(
        (state_dir / judge_child_payload["result_scratch_ref"]).read_text(
            encoding="utf-8",
        )
    )
    submitted = submitter.submit(
        operation_id=judge_child_payload["operation_id"],
        semantic_result=semantic_result,
        role_instance="judge-prd",
        credential=submit_tokens["judge-prd"],
    )
    judged = next(
        item for item in log.read_all()
        if item.id == submitted.canonical_event_id
    )
    orchestrator.run_once(events=[judged])
    synthesized = _latest_event(log, "goal.closure.synthesized")
    closure_result = synthesized.payload["goal_closure_result"]
    assert closure_result["product_acceptance_required"] is True
    assert closure_result["product_acceptance_verdict"] == "passed"
    assert closure_result["provider_qualification_status"] == "passed"
    orchestrator.run_once(events=[synthesized])
    delivery_settled = _latest_event(log, "run.delivery.settled")
    orchestrator.run_once(events=[delivery_settled])
    terminal = _latest_event(log, "run.goal.completed")
    assert emit_simulation_done(
        terminal,
        events=log.read_all(),
        writer=EventWriter(log),
    ) is not None

    events = log.read_all()
    direct_requests = []
    for operation in (
        item
        for item in events
        if item.type == "workflow.operation.requested"
        and item.payload.get("operation_type")
        in {"fanout_writer_child", "fanout_reader_child"}
    ):
        request = hydrate_sidecar_ref(
            state_dir,
            operation.payload["request_ref"],
        ).payload["request"]
        direct_requests.append({
            "operation_type": operation.payload.get("operation_type"),
            "stage_id": request.get("stage_id"),
            "effective_config_ref": request.get("effective_config_ref"),
            "effective_config_digest": request.get("effective_config_digest"),
        })
    assert direct_requests
    assert all(
        request["effective_config_ref"]
        == run_contract["config"]["effective_snapshot_ref"]
        and request["effective_config_digest"]
        == run_contract["config"]["effective_snapshot_digest"]
        for request in direct_requests
    ), json.dumps(direct_requests, indent=2)
    completed = [item for item in events if item.type == "run.goal.completed"]
    assert len(completed) == 1
    assert verified_targets == {candidate.payload["candidate_head_commit"]}
    assert completed[0].payload["verified_target_commit"] == next(iter(verified_targets))
    assert completed[0].payload["product_acceptance_verdict"] == "passed"
    assert completed[0].payload["provider_qualification_status"] == "passed"
    product_admissions = [
        item for item in events
        if item.type == "workflow.call.result.admitted"
        and item.payload.get("control_result_schema") == (
            "workflow-read-result.v1" if discovery_closeout else "verification-result.v1"
        )
        and item.payload.get("product_acceptance_report_ref")
    ]
    assert len({item.payload["operation_id"] for item in product_admissions}) == (
        1 if discovery_closeout else 2
    )
    assert all(
        item.payload["provider_qualification_status"] == "passed"
        for item in product_admissions
    )
    receipt = build_goal_completion_receipt(
        events,
        run_id=workflow_run_id,
        generated_at="2026-07-24T00:00:00+00:00",
        project_id="light-148",
    )
    assert receipt["terminal"]["event_id"] == completed[0].id
    assert receipt["completion_gate"]["verified_target_commit"] == next(
        iter(verified_targets)
    )
    assert receipt["delivery"]["status"] == "settled"
    assert len([item for item in events if item.type == "impl.self_check.completed"]) == 2
    assert len([item for item in events if item.type == "verify.child.completed"]) == 2
    assert not [item for item in events if item.type in {"test.failed", "judge.failed"}]
    assert len([
        item for item in events
        if item.type == "workflow.call.result.admitted"
        and item.payload.get("operation_id") == judge_child_payload["operation_id"]
    ]) == 1
    judge_metrics = json.loads(
        judge_briefing_path.with_suffix(".md.metrics.json").read_text()
    )
    assert judge_metrics["output_profile_id"] == "thin-judge-goal-closure"
    assert judge_metrics["required_read_count"] == len(
        judge_child_payload["required_reads"]
    )
    # The final attempt envelope and absolute paths count toward the advisory
    # budget; exceeding it is observable, not a delivery rejection.
    briefing_bytes = len(judge_briefing_path.read_bytes())
    assert judge_metrics["briefing_bytes"] == briefing_bytes
    assert judge_metrics["soft_budget_bytes"] == 10 * 1024
    assert judge_metrics["soft_budget_exceeded"] is (briefing_bytes > 10 * 1024)
    # Keep the hard envelope bounded while the 10 KiB value remains advisory.
    assert briefing_bytes <= 12 * 1024
    self_check_event = _latest_event(log, "impl.self_check.completed")
    assert "impl_self_check" not in self_check_event.payload
    assert (state_dir / self_check_event.payload["impl_self_check_ref"]).is_file()
    assert _git(tmp_path, "status", "--porcelain") == ""
