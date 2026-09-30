"""General browser start driven by a separate real zf start/EventWatcher."""

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

from tests.e2e.generic_workflow_complex_support import _execute_action
from tests.e2e.scripts.four_flow_e2e_support import summarize_flow_run
from tests.e2e.scripts.prod_flow_terminal_audit import audit_terminal_delivery
from tests.testkit.evidence import write_json_atomic
from tests.testkit.general_route import GeneralRoute
from tests.testkit.live_general_runtime import LiveGeneralRuntime
from tests.testkit.runner import _terminate
from zf.core.events import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.workflow_start import WorkflowStartService
from zf.runtime.workflow_anchor import mark_workflow_managed_task
from zf.runtime.orchestrator import Orchestrator


@pytest.mark.host
def test_browser_general_start_is_driven_by_live_zf_start(tmp_path: Path, monkeypatch) -> None:
    repo = Path(__file__).resolve().parents[2]
    project = tmp_path / "project"
    output = tmp_path / "live-browser"
    project.mkdir()
    output.mkdir()
    for key in ("ZF_PROJECT_ROOT", "ZF_STATE_DIR", "ZF_WORKFLOW_ACTION_TOKEN",
                "ZF_WEB_TRUSTED_SESSION", "ZF_WEB_PASSCODE"):
        monkeypatch.delenv(key, raising=False)
    for key in tuple(os.environ):
        if key.startswith(("ZF_OWNER_VISIBLE_", "FEISHU_", "LARK_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    route = GeneralRoute(project, monkeypatch)
    _, preview = route.prepare()
    proposal = preview["proposal"]
    applied = _execute_action(route.service, route.writer, "workflow-config-apply", {
        "proposal_id": proposal["proposal_id"], "proposal_ref": preview["proposal_ref"],
        "proposal_digest": proposal["proposal_digest"],
        "validation_result_ref": proposal["validation_result_ref"],
        "approval_ref": "owner:k529-live-browser",
        "idempotency_key": f"k529-live:{proposal['proposal_digest']}",
    })
    assert applied["ok"], applied
    route.reopen()
    task_id = "TASK-K529-LIVE-GENERAL"
    task = Task(
        id=task_id, title="K529 live browser workflow",
        contract=TaskContract(
            behavior="Deliver a report proving that fixture evidence is local.",
            verification="test -f README.md",
            acceptance_criteria=["the live scripted worker returns every stage result"],
        ),
    )
    TaskStore(route.state / "kanban.json").add(mark_workflow_managed_task(task))
    route.writer.append(ZfEvent(
        type="simulation.bootstrap", actor="testkit", payload={"purpose": "k529-live-browser"},
    ))
    container = f"zf-k529-live-browser-{uuid4().hex[:10]}"
    api = browser = None
    runtime = LiveGeneralRuntime(route, output, repo)
    port = None
    result = {"verdict": "fail", "scope": "browser_live_cli_tmux_scripted_general",
              "state_dir": str(route.state), "session": runtime.session}

    def forbidden_tick(*_args, **_kwargs):
        raise AssertionError("the test process must not drive the Kernel")

    monkeypatch.setattr(Orchestrator, "run_once", forbidden_tick)
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
        base = f"http://127.0.0.1:{port}"
        token = "k529-live-browser-token"
        env = {**os.environ, "ZF_TESTKIT_BROWSER_ROOT": str(project),
               "ZF_TESTKIT_BROWSER_DIST": str(output / "ui"),
               "ZF_WEB_ACTION_TOKEN": token, "PYTHONPATH": str(repo)}
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
                assert time.monotonic() < deadline
                time.sleep(0.1)
            assert client.post("/api/workspace/onboarding", json={"action": "skip"},
                               headers={"x-zf-web-token": token}).status_code == 200
            project_id = client.get("/api/snapshot").json()["project"]["project_id"]
        runtime.start()
        assert not route.events("workflow.invoke.requested")
        assert not route.events("fanout.child.dispatched")
        assert not route.events("task.dispatched")
        proposed = WorkflowStartService(route.state, route.config, project_root=project).propose(
            route.writer,
            {"task_id": task_id, "route_id": "general:scope",
             "objective": task.contract.behavior, "parameters": {"backend": "mock"},
             "project_id": project_id},
            actor="testkit-planner", origin="coding_agent",
        )
        assert proposed["ok"], proposed
        browser_cmd = [
            "docker", "run", "--rm", "--pull", "never", "--name", container, "--network", "host",
            "--user", f"{os.getuid()}:{os.getgid()}", "--entrypoint", "bash",
            "-v", f"{repo}:/work:ro", "-v", f"{output}:/zf-run", "-w", "/work/web",
            "-e", f"ZF_WEB_BASE_URL={base}", "-e", f"ZF_WEB_ACTION_TOKEN_FOR_TEST={token}",
            "-e", f"ZF_TESTKIT_PROJECT_ID={project_id}", "-e", "ZF_TESTKIT_BROWSER_RUN=1",
            "-e", f"ZF_TESTKIT_TASK_ID={task_id}", "-e", "ZF_TESTKIT_ROUTE_ID=general:scope",
            "mcp/playwright:latest", "-lc",
            "set -eu; browser_paths=(/ms-playwright/chromium-*/chrome-linux64/chrome); "
            "export ZF_E2E_CHROMIUM_EXECUTABLE_PATH=${browser_paths[-1]}; "
            "./node_modules/.bin/playwright test --config playwright.testkit.workflow-terminal.config.ts",
        ]
        with (output / "browser.log").open("w") as stream:
            browser = subprocess.Popen(browser_cmd, cwd=repo, stdout=stream, stderr=stream,
                                       start_new_session=True)
        deadline = time.monotonic() + 90
        while not route.events("workflow.invoke.requested"):
            assert browser.poll() is None, (output / "browser.log").read_text()
            assert time.monotonic() < deadline
            time.sleep(0.1)
        [invoke] = route.events("workflow.invoke.requested")
        assert invoke.task_id == task_id
        runtime.wait(lambda: route.events("run.goal.completed"))
        [terminal] = route.events("run.goal.completed")
        assert terminal.payload["workflow_run_id"] == invoke.payload["workflow_run_id"]
        assert terminal.origin == "kernel"
        runtime.wait(lambda: TaskStore(route.state / "kanban.json").get(task_id).status == "done")
        assert browser.wait(timeout=60) == 0, (output / "browser.log").read_text()
        owners = [event for event in route.events("owner.visible_message.requested")
                  if event.payload.get("terminal_event_id") == terminal.id]
        assert len(owners) == 1
        [owner] = owners
        truth = json.loads((output / "browser-truth.json").read_text())
        assert truth["dossier"]["terminal"]["event_id"] == terminal.id
        assert truth["invoke"]["id"] == invoke.id
        assert truth["request"]["payload"]["proposal_event_id"] == proposed["proposal_event_id"]
        assert truth["reload_completed"] and truth["legacy_completed"] and not truth["errors"]
        stats = json.loads((output / "playwright.json").read_text())["stats"]
        assert stats["expected"] == 1
        assert all(stats[key] == 0 for key in ("unexpected", "skipped", "flaky"))
        receipts = [json.loads(path.read_text())
                    for path in (route.state / "testkit-transport").glob("*.json")]
        assert len(receipts) == 5
        assert {item["stage_id"] for item in receipts} == {
            "scope", "collect-1", "collect-2", "synthesize", "verify",
        }
        dispatched = {event.id: event for event in route.events("fanout.child.dispatched")}
        assert set(dispatched) == {item["dispatch_event_id"] for item in receipts}
        admissions = {event.id for event in route.events("workflow.call.result.admitted")}
        for item in receipts:
            event = dispatched[item["dispatch_event_id"]]
            assert event.payload["workflow_run_id"] == invoke.payload["workflow_run_id"]
            assert event.payload["role_instance"] == item["role"]
            assert item["briefing_path"] in item["stdin_prompt"]
            assert item["briefing_sha256"] == hashlib.sha256(Path(item["briefing_path"]).read_bytes()).hexdigest()
            assert item["pid"] not in {os.getpid(), runtime.process.pid}
            assert item["submission"]["admitted_event_id"] in admissions
            runtime.pids.add(item["pid"])
        observation = summarize_flow_run(route.log.read_all(), invoke)
        delivery = audit_terminal_delivery(route.state)
        assert observation["admission_status"] == "admitted"
        assert observation["terminal_status"] == "completed"
        assert delivery["status"] == "passed", delivery
        assert delivery["terminal"]["event_id"] == terminal.id
        bodies = {}
        for key in ("dossier_ref", "completion_receipt_ref"):
            path = (route.state / owner.payload[key]).resolve()
            assert path.is_relative_to(route.state)
            bodies[path] = path.read_bytes()
            assert json.loads(bodies[path])["terminal"]["event_id"] == terminal.id
        receipt = json.loads(bodies[route.state / owner.payload["completion_receipt_ref"]])
        [artifact] = receipt["artifact_delivery"]["required_artifacts"]
        assert hashlib.sha256((route.state / artifact["ref"]).read_bytes()).hexdigest() == artifact["sha256"]
        assert not route.events("candidate.ready")
        kinds = {"workflow.invoke.requested", "fanout.child.dispatched", "workflow.call.result.admitted",
                 "run.goal.completed", "owner.visible_message.requested"}
        before = [event.to_json() for event in route.log.read_all() if event.type in kinds]
        runtime.stop()
        runtime.start()
        # Two real watcher heartbeat updates ensure the first idle tick has
        # returned before the second starts; never run a replacement tick.
        guard = route.state / "processes/watcher.pid.json"
        for _ in range(2):
            heartbeat = json.loads(guard.read_text())["heartbeat_at"]
            runtime.wait(lambda: json.loads(guard.read_text())["heartbeat_at"] != heartbeat, seconds=20)
        assert [event.to_json() for event in route.log.read_all() if event.type in kinds] == before
        assert {path: path.read_bytes() for path in bodies} == bodies
        assert not route.events("orchestrator.tick.failed")
        assert not route.events("kernel.housekeeping.failed")
        assert not (project / ".zf").exists()
        result.update(verdict="pass", observation=observation, delivery=delivery,
                      transport_receipts=receipts, invoke_event_id=invoke.id,
                      terminal_event_id=terminal.id, owner_request_event_id=owner.id,
                      restart_verified=True)
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
                                    payload={"purpose": "k529-live-browser"}))
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
