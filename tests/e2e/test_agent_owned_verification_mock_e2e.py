"""Multi-lane light-flow E2E for design 148 contracts."""

from __future__ import annotations

import json
import shlex
import subprocess
from copy import deepcopy
from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.config.schema import WorkflowAffinityLaneConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.simulation_lifecycle import emit_simulation_done
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


def test_light_profile_mock_e2e_closes_with_self_check_and_receipt_reuse(
    tmp_path: Path,
) -> None:
    workflow_run_id = "evt-light-entry-148"
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "zf-148@example.com")
    _git(tmp_path, "config", "user.name", "ZF 148 Mock")
    (tmp_path / "README.md").write_text("zf 148 mock\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".zf-148-mock/\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md", ".gitignore")
    _git(tmp_path, "commit", "-q", "-m", "init")
    _git(tmp_path, "branch", "-M", "main")

    config = load_config(
        Path(__file__).resolve().parents[2]
        / "examples" / "prod" / "controller" / "prd-light-v3.yaml"
    )
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
        "thin-judge-goal-closure": "blocking",
    }

    state_dir.mkdir()
    judge_token_path = provision_role_submit_credential(state_dir, "judge-prd")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task_map_ref = f"{state_dir.name}/artifacts/LIGHT-148/task_map.json"
    task_map_path = tmp_path / task_map_ref
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(json.dumps({
        "schema_version": "task-map.v1",
        "feature_id": "LIGHT-148",
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

    log = EventLog(state_dir / "events.jsonl")
    transport = _Transport()
    orchestrator = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    log.append(ZfEvent(
        type="run.goal.started",
        actor="orchestrator",
        correlation_id=workflow_run_id,
        payload={
            "run_id": "run-light-148",
            "goal_id": "LIGHT-148",
            "objective": "deliver one verified result",
        },
    ))
    trigger = ZfEvent(
        type="task_map.ready",
        actor="orchestrator",
        correlation_id=workflow_run_id,
        payload={
            "pdd_id": "LIGHT-148",
            "feature_id": "LIGHT-148",
            "task_map_ref": task_map_ref,
            "flow_kind": "prd",
        },
    )
    log.append(trigger)
    orchestrator.run_once(events=[trigger])

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
        completion = ZfEvent(
            type="dev.build.done",
            actor=child["role_instance"],
            task_id=task_id,
            correlation_id=workflow_run_id,
            payload={
                "fanout_id": impl_manifest["fanout_id"],
                "child_id": child["child_id"],
                "run_id": child["run_id"],
                "attempt_id": child["run_id"],
                "dispatch_id": child["run_id"],
                "pdd_id": "LIGHT-148",
                "source_commit": source_commit,
                "source_branch": child["source_branch"],
                "workdir": str(workdir),
                "impl_self_check": {
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
                },
            },
        )
        log.append(completion)
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
        verify_result = ZfEvent(
            type="verify.child.completed",
            actor=verify_child["role_instance"],
            task_id=task_id,
            correlation_id=workflow_run_id,
            payload={
                "fanout_id": verify_manifest["fanout_id"],
                "child_id": verify_child["child_id"],
                "run_id": verify_child["run_id"],
                "status": "completed",
                "evidence_refs": [f"mock://verify/{task_id}"],
                "report": {
                    "status": "passed",
                    "summary": "exact target and independent probe passed",
                    "findings": [],
                    "recommendation": "approve",
                    "evidence_refs": [f"mock://verify/{task_id}"],
                },
                "verification_result": {
                    "schema_version": "verification-result.v1",
                    **{
                        key: verify_payload[key]
                        for key in (
                            "workflow_run_id",
                            "task_id",
                            "contract_revision",
                            "task_map_generation",
                            "base_commit",
                            "task_ref",
                            "contract_snapshot_ref",
                            "contract_snapshot_digest",
                            "target_snapshot_ref",
                            "target_commit",
                            "target_snapshot_digest",
                        )
                    },
                    "execution_status": "completed",
                    "verdict": "passed",
                    "verification_owner": "task_verify",
                    "verification_tier": "task_non_smoke",
                    "summary": "verified",
                    "evidence_refs": [f"mock://verify/{task_id}"],
                    "reused_command_receipt_ids": [receipt_ids[task_id]],
                    "probe_receipts": [{
                        "probe_id": f"probe-{task_id}",
                        "status": "passed",
                        "evidence_refs": [f"mock://verify/{task_id}/probe"],
                    }],
                    "rework_items": [],
                    "requirement_results": [{
                        "acceptance_id": item["acceptance_id"],
                        "status": "passed",
                        "verification_owner": item["verification_owner"],
                        "verification_tier": item["verification_tier"],
                        "evidence_refs": [f"mock://verify/{task_id}"],
                        "findings": [],
                        "reproduction_commands": [],
                    } for item in verify_contract["acceptance_criteria"]],
                },
            },
        )
        log.append(verify_result)
        orchestrator.run_once(events=[verify_result])

    test_passed = _latest_event(log, "test.passed")
    orchestrator.run_once(events=[test_passed])
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
    semantic_result = json.loads(
        command[command.index("{"):].rsplit("\nZF_RESULT", 1)[0]
    )
    submitted = SemanticResultSubmitService(
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
    ).submit(
        operation_id=judge_child_payload["operation_id"],
        semantic_result=semantic_result,
        role_instance="judge-prd",
        credential=judge_token_path.read_text().strip(),
    )
    judged = next(
        item for item in log.read_all()
        if item.id == submitted.canonical_event_id
    )
    orchestrator.run_once(events=[judged])
    synthesized = _latest_event(log, "goal.closure.synthesized")
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
    completed = [item for item in events if item.type == "run.goal.completed"]
    assert len(completed) == 1
    assert verified_targets == {candidate.payload["candidate_head_commit"]}
    assert completed[0].payload["verified_target_commit"] == next(iter(verified_targets))
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
    self_check_event = _latest_event(log, "impl.self_check.completed")
    assert "impl_self_check" not in self_check_event.payload
    assert (state_dir / self_check_event.payload["impl_self_check_ref"]).is_file()
    assert _git(tmp_path, "status", "--porcelain") == ""
