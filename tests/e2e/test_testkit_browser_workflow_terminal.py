"""Browser workflow-start to the same Run's real Kernel delivery.

The installable config and pending Task/proposal are fixture inputs. Worker
semantics and transport are deterministic; no terminal or delivery facts are seeded.
"""

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

from tests.e2e.generic_workflow_complex_support import _execute_action
from tests.e2e.scripts.four_flow_e2e_support import summarize_flow_run
from tests.e2e.scripts.prod_flow_terminal_audit import audit_terminal_delivery
from tests.testkit.evidence import write_json_atomic
from tests.testkit.general_route import GeneralRoute
from tests.testkit.issue_route import IssueRoute
from tests.testkit.prd_route import PrdRoute
from tests.testkit.refactor_route import RefactorRoute
from tests.testkit.runner import _terminate
from zf.core.events import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services
from zf.runtime.workflow_start import WorkflowStartService
from zf.runtime.workflow_anchor import mark_workflow_managed_task


@pytest.mark.host
@pytest.mark.parametrize("family", ["general", "issue", "prd", "refactor"])
def test_docker_browser_workflow_start_reaches_same_run_delivery(tmp_path: Path, monkeypatch, family: str) -> None:
    repo = Path(__file__).resolve().parents[2]
    project = tmp_path / "project"
    if family != "refactor":
        project.mkdir()
    output = tmp_path / "browser-workflow-terminal"
    output.mkdir()
    for key in ("ZF_PROJECT_ROOT", "ZF_STATE_DIR", "ZF_WORKFLOW_ACTION_TOKEN",
                "ZF_WEB_TRUSTED_SESSION", "ZF_WEB_PASSCODE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    if family == "general":
        route = GeneralRoute(project, monkeypatch)
        _, preview = route.prepare()
        proposal = preview["proposal"]
        applied = _execute_action(route.service, route.writer, "workflow-config-apply", {
            "proposal_id": proposal["proposal_id"], "proposal_ref": preview["proposal_ref"],
            "proposal_digest": proposal["proposal_digest"],
            "validation_result_ref": proposal["validation_result_ref"],
            "approval_ref": "owner:browser-testkit-install",
            "idempotency_key": f"browser-install:{proposal['proposal_digest']}",
        })
        assert applied["ok"], applied
        route.reopen()
    elif family == "issue":
        route = IssueRoute(project)
    elif family == "prd":
        route = PrdRoute(project, monkeypatch)
    else:
        route = RefactorRoute(project, monkeypatch)
    if family != "general":
        # Single-kind compiler fixtures omit the optional operator catalog.
        # Register their existing entry stage, without changing stage/gate policy.
        config_path = project / "zf.yaml"
        documents = list(yaml.safe_load_all(config_path.read_text()))
        entry = {"issue": "issue-lanes-impl", "prd": "prd-scan", "refactor": "flow-scan"}[family]
        for document in documents:
            if document["kind"] == "ZfConfig":
                document["spec"].setdefault("workflow", {})["kind_routes"] = {family: {"pattern_id": entry}}
        config_path.write_text(yaml.safe_dump_all(documents, sort_keys=False))
        route.reopen()
    task_id = f"TASK-BROWSER-{family.upper()}"
    route_id = "general:scope" if family == "general" else f"delivery:{family}:default"
    objective = ("Deliver a report proving that fixture evidence is local." if family == "general"
                 else "Set answer.txt to fixed and pass tests/verify_issue.py")
    task = Task(
        id=task_id, title=f"Deliver {family} fixture evidence",
        contract=TaskContract(
            behavior=objective,
            verification="test -f README.md" if family == "general" else route.command,
            verification_tiers=[] if family == "general" else ["e2e"],
            spec_ref="docs/intake/bug.md" if family != "general" else "",
            product_contract_ref="docs/intake/bug.md" if family != "general" else "",
            acceptance_criteria=[objective],
            scope=[] if family == "general" else ["answer.txt"],
        ),
    )
    if family != "general":
        task = mark_workflow_managed_task(task)
    TaskStore(route.state / "kanban.json").add(task)
    # Establish the runtime truth file before the read-oriented Web API opens;
    # this bootstrap fact is not a workflow result or terminal projection.
    route.writer.append(ZfEvent(
        type="simulation.bootstrap", actor="testkit",
        payload={"purpose": f"browser-{family}-workflow"},
    ))
    assert not route.events("workflow.invoke.requested")
    container = f"zf-testkit-workflow-{uuid4().hex[:12]}"
    api = browser = None
    docker_available = False
    port = None
    result = {"verdict": "fail", "task_id": task_id, "state_dir": str(route.state),
              "scope": f"{family}_browser_start_scripted_workers_kernel_terminal_local_owner_request"}

    def run(argv: list[str], name: str, *, cwd: Path = repo, timeout: float = 180) -> int:
        with (output / f"{name}.log").open("w") as stream:
            return subprocess.run(argv, cwd=cwd, stdout=stream, stderr=stream, timeout=timeout).returncode

    try:
        assert run(["docker", "info"], "docker-info", timeout=15) == 0
        docker_available = True
        assert run(["docker", "image", "inspect", "mcp/playwright:latest", "--format", "{{.Id}}"],
                   "docker-image", timeout=15) == 0
        result["image_id"] = (output / "docker-image.log").read_text().strip()
        assert run(["npm", "exec", "--", "vite", "build", "--outDir", str(output / "ui")],
                   "build", cwd=repo / "web") == 0
        with socket.socket() as bound:
            bound.bind(("0.0.0.0", 0))
            port = bound.getsockname()[1]
        assert port > 8001
        base = f"http://127.0.0.1:{port}"
        token = "testkit-workflow-terminal-token"
        env = {**os.environ, "ZF_TESTKIT_BROWSER_ROOT": str(project),
               "ZF_TESTKIT_BROWSER_DIST": str(output / "ui"), "ZF_WEB_ACTION_TOKEN": token,
               "PYTHONPATH": str(repo)}
        with (output / "api.log").open("w") as stream:
            api = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "tests.testkit.browser_app:create_app", "--factory",
                 "--host", "0.0.0.0", "--port", str(port)],
                cwd=repo, env=env, stdout=stream, stderr=stream, start_new_session=True,
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
                assert time.monotonic() < deadline, "isolated API startup timeout"
                time.sleep(0.1)
            onboarding = client.post("/api/workspace/onboarding", json={"action": "skip"},
                                     headers={"x-zf-web-token": token})
            assert onboarding.status_code == 200, onboarding.text
            snapshot = client.get("/api/snapshot")
            assert snapshot.status_code == 200, snapshot.text
            project_id = snapshot.json()["project"]["project_id"]
        parameters = {"backend": "mock"}
        if family in {"prd", "refactor"}:
            parameters["target_root"] = str(project)
        if family == "refactor":
            parameters["source_root"] = str(route.source_root)
        proposed = WorkflowStartService(route.state, route.config, project_root=project).propose(
            route.writer, {"task_id": task_id, "route_id": route_id, "objective": objective,
                           "parameters": parameters, "project_id": project_id},
            actor="testkit-planner", origin="coding_agent",
        )
        assert proposed["ok"], proposed
        assert not route.events("workflow.invoke.requested")
        command = [
            "docker", "run", "--rm", "--pull", "never", "--name", container, "--network", "host",
            "--user", f"{os.getuid()}:{os.getgid()}", "--entrypoint", "bash",
            "-v", f"{repo}:/work:ro", "-v", f"{output}:/zf-run", "-w", "/work/web",
            "-e", f"ZF_WEB_BASE_URL={base}", "-e", f"ZF_WEB_ACTION_TOKEN_FOR_TEST={token}",
            "-e", f"ZF_TESTKIT_PROJECT_ID={project_id}", "-e", "ZF_TESTKIT_BROWSER_RUN=1",
            "-e", f"ZF_TESTKIT_TASK_ID={task_id}", "-e", f"ZF_TESTKIT_ROUTE_ID={route_id}",
            "mcp/playwright:latest", "-lc",
            "set -eu; browser_paths=(/ms-playwright/chromium-*/chrome-linux64/chrome); "
            "export ZF_E2E_CHROMIUM_EXECUTABLE_PATH=${browser_paths[-1]}; "
            "test -x \"$ZF_E2E_CHROMIUM_EXECUTABLE_PATH\"; "
            "./node_modules/.bin/playwright test --config playwright.testkit.workflow-terminal.config.ts",
        ]
        with (output / "docker-browser.log").open("w") as stream:
            browser = subprocess.Popen(command, cwd=repo, stdout=stream, stderr=stream, start_new_session=True)
        deadline = time.monotonic() + 90
        while not route.events("workflow.invoke.requested"):
            assert browser.poll() is None, (output / "docker-browser.log").read_text()
            assert time.monotonic() < deadline, "browser workflow-start deadline"
            time.sleep(0.1)
        [route.invoke] = route.events("workflow.invoke.requested")
        assert route.invoke.task_id == task_id
        route.reopen()
        if family == "general":
            for stage in ("scope", "collect-1", "collect-2", "synthesize"):
                route.complete_reader(route.dispatch(stage))
            route.complete_verify(route.dispatch("verify"))
        else:
            writer = (route.dispatch("issue-lanes-impl") if family == "issue"
                      else route.continue_to_writer())
            route.complete_writer(writer)
            prefix = {"issue": "issue", "prd": "prd", "refactor": "flow"}[family]
            route.complete_verify(route.dispatch(f"{prefix}-lanes-verify"))
            if family == "prd":
                route.complete_reader(route.dispatch("prd-post-verify-discovery"))
            if family == "refactor":
                route.complete_reader(route.dispatch("flow-verify-bridge"))
                route.complete_reader(route.dispatch("flow-module-parity-scan"), extra={"open_p0_p1_gap_count": 0})
            route.complete_judge(route.dispatch("flow-final-judge" if family == "refactor" else f"{prefix}-lanes-final"))
        for _ in range(12):
            route.tick()
            if route.events("run.goal.completed") and route.runtime.task_store.get(task_id).status == "done":
                break
        [terminal] = route.events("run.goal.completed")
        assert terminal.origin == "kernel"
        assert route.runtime.task_store.get(task_id).status == "done"
        for _ in range(12):
            route.tick()
            run_standard_tick_services(route.runtime, state=TickServiceState(), now=1.0)
            if audit_terminal_delivery(route.state)["status"] != "pending":
                break
        observation = summarize_flow_run(route.log.read_all(), route.invoke)
        assert observation["admission_status"] == "admitted", observation
        assert observation["terminal_status"] == "completed", observation
        delivery = audit_terminal_delivery(route.state)
        assert delivery["status"] == "passed", delivery
        assert delivery["terminal"]["run_id"] == observation["run_id"]
        assert delivery["terminal"]["event_id"] == terminal.id
        [owner] = route.events("owner.visible_message.requested")
        bodies = {}
        for key in ("dossier_ref", "completion_receipt_ref"):
            path = (route.state / owner.payload[key]).resolve()
            assert path.is_relative_to(route.state)
            bodies[path] = path.read_bytes()
            assert json.loads(bodies[path])["terminal"]["event_id"] == terminal.id
        receipt = json.loads(bodies[route.state / owner.payload["completion_receipt_ref"]])
        if family == "general":
            [artifact] = receipt["artifact_delivery"]["required_artifacts"]
            assert hashlib.sha256((route.state / artifact["ref"]).read_bytes()).hexdigest() == artifact["sha256"]
        else:
            _, commit = route.candidate()
            assert terminal.payload["target_commit"] == terminal.payload["verified_target_commit"] == commit
            assert route.store.get(writer.payload["task_id"]).status == "done"
            result.update(candidate_commit=commit, candidate_ref=route.events("candidate.ready")[0].payload["candidate_ref"])
        kinds = {"workflow.invoke.requested", "fanout.child.dispatched", "workflow.call.result.admitted",
                 "candidate.ready", "candidate.quality.passed", "task.status_changed",
                 "run.goal.completed", "owner.visible_message.requested"}
        before = [row.to_json() for row in route.log.read_all() if row.type in kinds]
        route.reopen()
        for _ in range(3):
            route.tick()
            run_standard_tick_services(route.runtime, state=TickServiceState(), now=1.0)
        assert [row.to_json() for row in route.log.read_all() if row.type in kinds] == before
        assert {path: path.read_bytes() for path in bodies} == bodies
        assert browser.wait(timeout=60) == 0, (output / "docker-browser.log").read_text()
        report = json.loads((output / "playwright.json").read_text())
        assert report["stats"]["expected"] == 1
        assert all(report["stats"][key] == 0 for key in ("unexpected", "skipped", "flaky"))
        truth = json.loads((output / "browser-truth.json").read_text())
        assert truth["invoke"]["id"] == route.invoke.id
        assert truth["request"]["payload"]["task_id"] == task_id
        assert truth["request"]["payload"]["proposal_event_id"] == proposed["proposal_event_id"]
        assert truth["reload_completed"] and truth["legacy_completed"] and not truth["errors"]
        assert truth["dossier"]["terminal"]["event_id"] == terminal.id
        assert len(route.events("fanout.child.dispatched")) == {"general": 5, "issue": 3, "prd": 7, "refactor": 9}[family]
        if family == "general":
            assert not route.events("candidate.ready")  # Artifact delivery, not a code candidate.
        else:
            assert len(route.events("candidate.ready")) == 1
        assert not (project / ".zf").exists()
        result.update(verdict="pass", observation=observation, delivery=delivery,
                      invoke_event_id=route.invoke.id, owner_request_event_id=owner.id)
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        if browser is not None:
            _terminate(browser)
        if api is not None:
            _terminate(api)
        cleanup_ok = api is None or api.poll() is not None
        if docker_available:
            run(["docker", "rm", "-f", container], "container-cleanup", timeout=15)
            cleanup_ok = cleanup_ok and run(["docker", "container", "inspect", container],
                                            "container-check", timeout=15) != 0
        if port is not None:
            with socket.socket() as probe:
                cleanup_ok = cleanup_ok and probe.connect_ex(("127.0.0.1", port)) != 0
        route.writer.append(ZfEvent(type="simulation.done", actor="zf-cli",
                                   payload={"purpose": "testkit-workflow-terminal"}))
        result["cleanup_ok"] = cleanup_ok
        if not cleanup_ok:
            result["verdict"] = "harness_failure"
        write_json_atomic(output / "browser-gate.json", result)
        assert cleanup_ok, "owned API/container/port remained after cleanup"
