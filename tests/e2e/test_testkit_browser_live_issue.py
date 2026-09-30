"""Delivery browser workflows driven by a separate live zf runtime."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
import yaml

from tests.e2e.scripts.four_flow_e2e_support import summarize_flow_run
from tests.e2e.scripts.prod_flow_terminal_audit import audit_terminal_delivery
from tests.testkit.evidence import write_json_atomic
from tests.testkit.issue_route import IssueRoute, git
from tests.testkit.live_general_runtime import LiveGeneralRuntime
from tests.testkit.prd_route import PrdRoute
from tests.testkit.refactor_route import RefactorRoute
from tests.testkit.refactor_strong_route import StrongRefactorRoute
from tests.testkit.refactor_two_lane_route import TwoLaneStrongRefactorRoute
from tests.testkit.runner import _terminate
from zf.core.events import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.workflow_anchor import mark_workflow_managed_task
from zf.runtime.workflow_start import WorkflowStartService


@pytest.mark.host
@pytest.mark.parametrize("family", ["issue", "prd", "refactor", "refactor-strong", "refactor-two-lane"])
def test_browser_issue_start_is_driven_by_live_zf_start(tmp_path: Path, monkeypatch, family: str) -> None:
    repo = Path(__file__).resolve().parents[2]
    two_lane = family == "refactor-two-lane"
    strong = family == "refactor-strong" or two_lane
    worker_family = family.replace("-", "_")
    family = "refactor" if strong else family
    project, output = tmp_path / "project", tmp_path / "live-browser"
    if family != "refactor":
        project.mkdir()
    output.mkdir()
    for key in tuple(os.environ):
        if key.startswith(("ZF_OWNER_VISIBLE_", "FEISHU_", "LARK_", "ZF_PROJECT_ROOT",
                           "ZF_STATE_DIR", "ZF_WORKFLOW_ACTION_TOKEN", "ZF_WEB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    route = (IssueRoute(project) if family == "issue" else
             PrdRoute(project, monkeypatch) if family == "prd" else
             TwoLaneStrongRefactorRoute(project, monkeypatch) if two_lane else
             StrongRefactorRoute(project, monkeypatch) if strong else RefactorRoute(project, monkeypatch))
    prefix = "flow" if family == "refactor" else family
    source_before = {}
    if family == "refactor":
        assert not route.source_root.is_relative_to(project)
        assert not project.is_relative_to(route.source_root)
        source_before = {path.name: path.read_bytes() for path in route.source_root.iterdir()}
    route_id = f"delivery:{family}:default"
    config_path = project / "zf.yaml"
    documents = list(yaml.safe_load_all(config_path.read_text()))
    for document in documents:
        if document["kind"] == "ZfConfig":
            document["spec"].setdefault("workflow", {})["kind_routes"] = {
                family: {"pattern_id": "issue-lanes-impl" if family == "issue" else f"{prefix}-scan"},
            }
    config_path.write_text(yaml.safe_dump_all(documents, sort_keys=False))
    route.reopen()
    task_id = f"TASK-LIVE-{family.upper()}"
    task = mark_workflow_managed_task(Task(
        id=task_id, title=f"Live {family} workflow",
        contract=TaskContract(
            behavior=("Set both answer.txt and answer-second.txt to fixed" if two_lane else
                      "Set answer.txt to fixed and pass tests/verify_issue.py"),
            verification=(route.probe_command(route.product_probe) if two_lane else route.command),
            verification_tiers=["e2e"],
            spec_ref="docs/intake/bug.md", product_contract_ref="docs/intake/bug.md",
            scope=["answer.txt", "answer-second.txt"] if two_lane else ["answer.txt"],
            acceptance_criteria=["Both answers contain fixed" if two_lane else "answer.txt contains fixed"],
        ),
    ))
    TaskStore(route.state / "kanban.json").add(task)
    route.writer.append(ZfEvent(type="simulation.bootstrap", actor="testkit",
                                payload={"purpose": f"live-{family}"}))
    container = f"zf-live-{family}-{uuid4().hex[:10]}"
    runtime = LiveGeneralRuntime(route, output, repo,
                                 worker_module=f"tests.testkit.tmux_{worker_family}_worker")
    if strong:
        # Finalize the isolated Given before ship_candidate's clean-baseline gate.
        assert project.is_relative_to(tmp_path) and project != repo
        assert git(project, "diff", "--name-only") == "zf.yaml"
        assert git(project, "diff", "--cached", "--name-only") == ""
        git(project, "add", "zf.yaml")
        git(project, "commit", "-q", "-m", "fixture live runtime baseline", "--", "zf.yaml")
        route.base = git(project, "rev-parse", "main")
        assert git(project, "status", "--porcelain") == ""
    api = browser = None
    port = None
    result = {"verdict": "fail", "scope": f"browser_live_cli_tmux_scripted_{worker_family}",
              "state_dir": str(route.state), "session": runtime.session}
    monkeypatch.setattr(Orchestrator, "run_once",
                        lambda *_args, **_kwargs: pytest.fail("test process drove Kernel"))
    try:
        assert subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
        assert subprocess.run(["docker", "image", "inspect", "mcp/playwright:latest"],
                              capture_output=True, timeout=15).returncode == 0
        assert subprocess.run(["npm", "exec", "--", "vite", "build", "--outDir", str(output / "ui")],
                              cwd=repo / "web", capture_output=True, timeout=180).returncode == 0
        with socket.socket() as bound:
            bound.bind(("0.0.0.0", 0))
            port = bound.getsockname()[1]
        assert port > 8001
        base, token = f"http://127.0.0.1:{port}", "k530-live-issue-token"
        env = {**os.environ, "ZF_TESTKIT_BROWSER_ROOT": str(project),
               "ZF_TESTKIT_BROWSER_DIST": str(output / "ui"), "ZF_WEB_ACTION_TOKEN": token,
               "PYTHONPATH": str(repo)}
        with (output / "api.log").open("w") as stream:
            api = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "tests.testkit.browser_app:create_app", "--factory",
                 "--host", "0.0.0.0", "--port", str(port)], cwd=repo, env=env,
                stdout=stream, stderr=stream, start_new_session=True,
            )
        with httpx.Client(base_url=base, trust_env=False, timeout=5) as client:
            deadline = time.monotonic() + 30
            while True:
                assert api.poll() is None, (output / "api.log").read_text()
                try:
                    if client.get("/api/state").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline
                time.sleep(0.1)
            assert client.post("/api/workspace/onboarding", json={"action": "skip"},
                               headers={"x-zf-web-token": token}).status_code == 200
            project_id = client.get("/api/snapshot").json()["project"]["project_id"]
        proposed = WorkflowStartService(route.state, route.config, project_root=project).propose(
            route.writer,
            {"task_id": task_id, "route_id": route_id,
             "objective": task.contract.behavior, "parameters": {
                 "backend": "mock", **({"target_root": str(project)} if family != "issue" else {}),
                 **({"source_root": str(route.source_root)} if family == "refactor" else {}),
             },
             "project_id": project_id}, actor="testkit-planner", origin="coding_agent",
        )
        assert proposed["ok"], proposed
        runtime.start()
        assert not route.events("workflow.invoke.requested")
        assert not route.events("fanout.child.dispatched")
        assert not route.events("task.dispatched")
        browser_cmd = [
            "docker", "run", "--rm", "--pull", "never", "--name", container, "--network", "host",
            "--user", f"{os.getuid()}:{os.getgid()}", "--entrypoint", "bash",
            "-v", f"{repo}:/work:ro", "-v", f"{output}:/zf-run", "-w", "/work/web",
            "-e", f"ZF_WEB_BASE_URL={base}", "-e", f"ZF_WEB_ACTION_TOKEN_FOR_TEST={token}",
            "-e", f"ZF_TESTKIT_PROJECT_ID={project_id}", "-e", "ZF_TESTKIT_BROWSER_RUN=1",
            "-e", "ZF_TESTKIT_LIVE_DELIVERY=1",
            "-e", f"ZF_TESTKIT_TASK_ID={task_id}", "-e", f"ZF_TESTKIT_ROUTE_ID={route_id}",
            "mcp/playwright:latest", "-lc",
            "set -eu; browser_paths=(/ms-playwright/chromium-*/chrome-linux64/chrome); "
            "export ZF_E2E_CHROMIUM_EXECUTABLE_PATH=${browser_paths[-1]}; "
            "./node_modules/.bin/playwright test --config playwright.testkit.workflow-terminal.config.ts",
        ]
        with (output / "browser.log").open("w") as stream:
            browser = subprocess.Popen(browser_cmd, cwd=repo, stdout=stream, stderr=stream,
                                       start_new_session=True)
        def terminal_ready():
            assert browser.poll() in (None, 0), (output / "browser.log").read_text()
            return route.events("run.goal.completed")

        runtime.wait(terminal_ready, seconds=180)
        [terminal] = route.events("run.goal.completed")
        [invoke] = route.events("workflow.invoke.requested")
        assert invoke.task_id == task_id
        assert terminal.origin == "kernel"
        assert terminal.payload["workflow_run_id"] == invoke.payload["workflow_run_id"]
        runtime.wait(lambda: TaskStore(route.state / "kanban.json").get(task_id).status == "done")
        assert browser.wait(timeout=60) == 0, (output / "browser.log").read_text()
        truth = json.loads((output / "browser-truth.json").read_text())
        assert truth["request"]["payload"]["task_id"] == task_id
        assert truth["request"]["payload"]["proposal_event_id"] == proposed["proposal_event_id"]
        assert truth["invoke"]["id"] == invoke.id
        assert truth["dossier"]["terminal"]["event_id"] == terminal.id
        assert truth["reload_completed"] and truth["legacy_completed"] and not truth["errors"]
        stats = json.loads((output / "playwright.json").read_text())["stats"]
        assert stats["expected"] == 1
        assert all(stats[key] == 0 for key in ("unexpected", "skipped", "flaky"))
        receipts = [json.loads(path.read_text())
                    for path in (route.state / "testkit-transport").glob("*.json")]
        assert len(receipts) == ({"issue": 3, "prd": 8, "refactor": 10}[family] + (2 if two_lane else 0))
        assert {item["stage_id"] for item in receipts} == {
            "issue": {"issue-lanes-impl", "issue-lanes-verify", "issue-lanes-final"},
            "prd": {"prd-scan", "prd-plan", "prd-lanes-impl", "prd-lanes-verify",
                    "prd-post-verify-discovery", "prd-lanes-final"},
            "refactor": {"flow-scan", "flow-plan", "flow-lanes-impl", "flow-lanes-verify",
                         "flow-verify-bridge", "flow-module-parity-scan", "flow-final-judge"},
        }[family]
        synths = route.events("fanout.synth.dispatched")
        assert len(synths) == (0 if family == "issue" else 1)
        dispatched = {event.id: event for event in [*route.events("fanout.child.dispatched"), *synths]}
        assert set(dispatched) == {item["dispatch_event_id"] for item in receipts}
        admissions = route.events("workflow.call.result.admitted")
        assert len(admissions) == ({"issue": 3, "prd": 9, "refactor": 11}[family] + (2 if two_lane else 0))
        events = {event.id: event for event in route.log.read_all()}
        for admission in admissions:
            assert admission.payload["workflow_run_id"] == invoke.payload["workflow_run_id"]
            for key in ("envelope_ref", "control_result_ref"):
                ref = admission.payload[key]
                path = (route.state / ref["ref"]).resolve()
                assert path.is_relative_to(route.state)
                assert hashlib.sha256(path.read_bytes()).hexdigest() == ref["sha256"]
        for item in receipts:
            dispatch = dispatched[item["dispatch_event_id"]]
            assert dispatch.payload["workflow_run_id"] == invoke.payload["workflow_run_id"]
            assert dispatch.payload["role_instance"] == item["role"]
            assert item["briefing_path"] in item["stdin_prompt"]
            assert item["briefing_sha256"] == hashlib.sha256(Path(item["briefing_path"]).read_bytes()).hexdigest()
            assert item["pid"] not in {os.getpid(), runtime.process.pid}
            source = events[item["submission"]["canonical_event_id"]]
            assert source.type == item["submission"]["canonical_event_type"]
            assert source.actor == item["role"]
            [admission] = [event for event in admissions
                           if event.payload["source_event_id"] == source.id]
            if strong or dispatch.type == "fanout.synth.dispatched":
                assert admission.actor == "zf-cli"
                assert admission.payload["mode"] == "blocking"
                assert item["submission"]["admitted_event_id"] == admission.id
                assert source.payload["semantic_submit_admission_event_id"] == admission.id
            else:
                assert source.origin == "worker"
                assert admission.origin == "kernel"
            assert admission.payload["operation_id"] == dispatch.payload["operation_id"]
            ref = admission.payload["read_ledger_ref"]
            path = (route.state / ref["ref"]).resolve()
            assert path.is_relative_to(route.state)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == ref["sha256"]
            runtime.pids.add(item["pid"])
        [candidate] = route.events("candidate.ready")
        assert candidate.payload["workflow_run_id"] == terminal.payload["workflow_run_id"]
        commit = candidate.payload["candidate_head_commit"]
        assert git(project, "rev-parse", candidate.payload["candidate_ref"]) == commit
        if not strong:
            assert route.candidate()[1] == commit
        assert terminal.payload["target_commit"] == commit
        assert terminal.payload["verified_target_commit"] == commit
        assert terminal.payload["candidate_head_commit"] == commit
        verification = events[terminal.payload["verification_event_id"]]
        assert verification.type == "fanout.child.completed"
        assert verification.payload["stage_id"] == f"{prefix}-lanes-verify"
        assert verification.payload["target_commit"] == commit
        assert verification.payload["admitted_call_result_ref"] == terminal.payload["verification_admitted_call_result_ref"]
        if family != "issue":
            sequence = {
                "prd": ["prd.scan.completed", "task_map.ready", "candidate.ready", "test.passed",
                        "flow.discovery.completed", "flow.goal.closed", "goal.closure.synthesized",
                        "run.goal.completed"],
                "refactor": ["zaofu.refactor.review.ready", "zaofu.refactor.plan.ready", "task_map.ready",
                             "candidate.ready", "test.passed", "verify.passed", "module.parity.closed",
                             "goal.closure.synthesized", "run.goal.completed"],
            }[family]
            positions = []
            event_ids = list(events)
            for kind in sequence:
                [event] = route.events(kind)
                assert event.origin == "kernel"
                positions.append(event_ids.index(event.id))
            assert positions == sorted(positions)
        writers = [event for event in dispatched.values()
                   if event.payload["stage_id"] == f"{prefix}-lanes-impl"]
        assert len(writers) == (2 if two_lane else 1)
        child_tasks = {event.payload["task_id"] for event in writers}
        assert len(child_tasks) == len(writers)
        assert set(terminal.payload["completed_task_ids"]) == child_tasks
        assert all(TaskStore(route.state / "kanban.json").get(key).status == "done" for key in child_tasks)
        if two_lane:
            verifiers = [event for event in dispatched.values() if event.payload["stage_id"] == "flow-lanes-verify"]
            assert len(verifiers) == 2
            assert {event.payload["role_instance"] for event in writers} == {"dev-lane-0", "dev-lane-1"}
            assert {event.payload["role_instance"] for event in verifiers} == {"verify-lane-0", "verify-lane-1"}
            assert len({event.payload["workdir"] for event in writers}) == 2
            assert {event.payload["task_id"] for event in verifiers} == child_tasks
            assert {event.payload["target_commit"] for event in verifiers} == {commit}
            lanes = {event.payload["task_id"]: event.payload["role_instance"].rsplit("-", 1)[1] for event in writers}
            assert all(lanes[event.payload["task_id"]] == event.payload["role_instance"].rsplit("-", 1)[1]
                       for event in verifiers)
        [owner] = [event for event in route.events("owner.visible_message.requested")
                   if event.payload.get("terminal_event_id") == terminal.id]
        assert owner.payload["run_id"] == invoke.payload["workflow_run_id"]
        observation = summarize_flow_run(route.log.read_all(), invoke)
        delivery = audit_terminal_delivery(route.state)
        assert observation["admission_status"] == "admitted"
        assert observation["terminal_status"] == "completed"
        assert delivery["status"] == "passed", delivery
        assert delivery["terminal"]["event_id"] == terminal.id
        bodies = {}
        for key in ("dossier_ref", "completion_receipt_ref"):
            assert owner.payload[key] == delivery["delivery"][key]
            path = (route.state / owner.payload[key]).resolve()
            assert path.is_relative_to(route.state)
            bodies[path] = path.read_bytes()
            document = json.loads(bodies[path])
            assert document["terminal"]["event_id"] == terminal.id
            assert document["source_fingerprint"] == owner.payload[
                "dossier_source_fingerprint" if key == "dossier_ref" else "completion_receipt_fingerprint"
            ]
        if strong:
            [settled] = route.events("run.delivery.settled")
            [ship] = route.events("ship.completed")
            [requested] = route.events("run.delivery.requested")
            [package] = route.events("plan.artifact_package.admitted")
            assert package.payload["mode"] == "blocking"
            assert terminal.payload["delivery_status"] == "settled"
            assert terminal.payload["delivery_phase"] == "ship_delivered"
            assert terminal.payload["delivery_event_id"] == settled.id
            assert ship.causation_id == settled.causation_id == requested.id
            assert settled.payload["run_id"] == invoke.payload["workflow_run_id"]
            assert ship.payload["source_commit"] == settled.payload["target_commit"] == commit
            assert ship.payload["original_head"] == route.base
            assert ship.payload["target_branch"] == "main"
            landed = git(project, "rev-parse", "main")
            assert landed == ship.payload["final_commit"] != route.base
            assert git(project, "rev-parse", ship.payload["final_tag"]) == landed
            assert git(project, "merge-base", "--is-ancestor", commit, landed) == ""
            assert git(project, "show", f"{landed}:answer.txt") == "fixed"
            if two_lane:
                assert git(project, "show", f"{landed}:answer-second.txt") == "fixed"
            assert terminal.payload["product_acceptance_required"] is True
            assert terminal.payload["product_acceptance_verdict"] == "passed"
            spec = route.body(terminal.payload["product_acceptance_spec_ref"],
                              terminal.payload["product_acceptance_spec_digest"])
            report = route.body(terminal.payload["product_acceptance_report_ref"],
                                terminal.payload["product_acceptance_report_digest"])
            assert spec["workflow_run_id"] == report["workflow_run_id"] == invoke.payload["workflow_run_id"]
            assert spec["task_map_generation"] == report["task_map_generation"] == terminal.payload["task_map_generation"]
            assert report["target_commit"] == commit
            assert report["candidate_ref"] == candidate.payload["candidate_ref"]
            assert report["product_acceptance_spec_digest"] == terminal.payload["product_acceptance_spec_digest"]
            [journey] = report["journey_results"]
            [assertion] = journey["assertion_results"]
            probes = [route.body(ref["ref"], ref["sha256"]) for ref in assertion["evidence_refs"]]
            assert len(probes) == (2 if two_lane else 1)
            command = route.probe_command(route.product_probe) if two_lane else route.command
            [probe] = [item for item in probes if item["command"] == command]
            assert probe["target_commit"] == commit
            assert probe["exit_code"] == 0
            assert probe["stdout"] == ("two-lane product passed\n" if two_lane else "issue regression passed\n")
        else:
            assert not route.events("ship.requested")
        kinds = {"workflow.invoke.requested", "fanout.child.dispatched", "fanout.synth.dispatched",
                 "workflow.call.result.admitted",
                 "candidate.ready", "candidate.quality.passed", "run.goal.completed",
                 "owner.visible_message.requested"}
        if strong:
            kinds.update({"run.delivery.requested", "run.delivery.settled", "ship.started", "ship.completed"})
        before = [event.to_json() for event in route.log.read_all() if event.type in kinds]
        runtime.stop()
        runtime.start()
        guard = route.state / "processes/watcher.pid.json"
        for _ in range(2):
            heartbeat = json.loads(guard.read_text())["heartbeat_at"]
            runtime.wait(lambda: json.loads(guard.read_text())["heartbeat_at"] != heartbeat, seconds=20)
        assert [event.to_json() for event in route.log.read_all() if event.type in kinds] == before
        assert {path: path.read_bytes() for path in bodies} == bodies
        if strong:
            assert git(project, "rev-parse", "main") == landed
            assert git(project, "rev-parse", candidate.payload["candidate_ref"]) == commit
        else:
            assert route.candidate()[1] == commit
        assert all(TaskStore(route.state / "kanban.json").get(key).status == "done"
                   for key in (task_id, *child_tasks))
        assert not route.events("orchestrator.tick.failed")
        assert not route.events("kernel.housekeeping.failed")
        assert not route.events("discriminator.failed")
        assert not route.events("worker.runner.failed")
        assert not route.events("worker.respawned")
        assert not (project / ".zf").exists()
        if family == "refactor":
            assert terminal.payload["delivery_policy"] == ("ship_candidate" if strong else "report_only")
            assert {path.name: path.read_bytes() for path in route.source_root.iterdir()} == source_before
        result.update(verdict="pass", terminal_event_id=terminal.id,
                      invoke_event_id=invoke.id, candidate_commit=commit,
                      receipts=receipts, observation=observation, delivery=delivery,
                      owner_request_event_id=owner.id, restart_verified=True)
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        if browser is not None:
            _terminate(browser)
        cleanup_error = None
        try:
            runtime.stop()
        except Exception as exc:
            cleanup_error = repr(exc)
        if api is not None:
            _terminate(api)
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False, timeout=15)
        route.writer.append(ZfEvent(type="simulation.done", actor="testkit",
                                    payload={"purpose": f"live-{family}"}))
        cleanup_ok = cleanup_error is None and (api is None or api.poll() is not None)
        cleanup_ok = cleanup_ok and subprocess.run(
            ["docker", "container", "inspect", container], capture_output=True, timeout=15,
        ).returncode != 0
        if port is not None:
            with socket.socket() as probe:
                cleanup_ok = cleanup_ok and probe.connect_ex(("127.0.0.1", port)) != 0
        result.update(cleanup_ok=cleanup_ok, cleanup_error=cleanup_error,
                      owned_pids=sorted(runtime.pids))
        if not cleanup_ok:
            result["verdict"] = "harness_failure"
        write_json_atomic(output / "browser-gate.json", result)
        assert cleanup_ok, cleanup_error
