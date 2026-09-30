"""Raw Task Map → Docker browser approval → blocking candidate → board DOM.

Only worker activation/transport is inert, as in the S3 planning fixture.
Approval, canonical Tasks, completion, and candidate facts are never seeded.
"""

from __future__ import annotations

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

from tests.conformance.test_zaofu_testkit_s3_plan_approval import planning_runtime  # noqa: F401
from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import _submit_generation_stage
from tests.test_task_pipeline_generation import _git
from tests.testkit.evidence import write_json_atomic
from tests.testkit.runner import _terminate
from zf.core.config.loader import load_config
from zf.core.config.render import renderable_config_to_primitive
from zf.core.events import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.mark.host
def test_docker_browser_plan_approval_reaches_candidate(tmp_path: Path, planning_runtime) -> None:
    repo = Path(__file__).resolve().parents[2]
    output = tmp_path / "browser-plan-approval"
    output.mkdir()
    runtime, transport, trigger = planning_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    primitive = renderable_config_to_primitive(config)
    primitive["workflow"]["stages"][0]["task_map"] = "artifacts/task-map.json"
    (project / "zf.yaml").write_text(yaml.safe_dump(primitive), encoding="utf-8")
    reloaded = load_config(project / "zf.yaml")
    assert reloaded.workflow.plan_approval_enabled is True
    assert reloaded.workflow.flow_metadata == config.workflow.flow_metadata
    config = reloaded
    runtime = Orchestrator(state, config, transport, project_root=project)
    runtime.run_once()
    [pending] = [e for e in runtime.event_log.read_all() if e.type == "plan.approval.requested"]
    assert pending.payload["plan_id"] == trigger.id
    assert not transport.contexts
    assert runtime.task_store.get("TASK-BLOCKING") is None
    (state / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state / "feature_list.json").write_text("[]\n", encoding="utf-8")

    container = f"zf-testkit-plan-approval-{uuid4().hex[:12]}"
    api = None
    browser = None
    docker_available = False
    port = None
    result = {"verdict": "fail", "plan_id": trigger.id, "state_dir": str(state)}

    def run(argv: list[str], name: str, *, cwd: Path = repo, timeout: float = 180) -> int:
        with (output / f"{name}.log").open("w") as stream:
            return subprocess.run(argv, cwd=cwd, stdout=stream, stderr=stream, timeout=timeout).returncode

    try:
        assert run(["docker", "info"], "docker-info", timeout=15) == 0
        docker_available = True
        assert run(["docker", "image", "inspect", "mcp/playwright:latest", "--format", "{{.Id}}"],
                   "docker-image", timeout=15) == 0
        result["image_id"] = (output / "docker-image.log").read_text().strip()
        result["commit"] = _git(repo, "rev-parse", "HEAD")
        result["dirty"] = bool(_git(repo, "status", "--porcelain"))
        assert run(["npm", "exec", "--", "vite", "build", "--outDir", str(output / "ui")],
                   "build", cwd=repo / "web") == 0
        with socket.socket() as bound:
            bound.bind(("0.0.0.0", 0))
            port = bound.getsockname()[1]
        assert port > 8001
        base = f"http://127.0.0.1:{port}"
        result["service_url"] = base
        token = "testkit-plan-approval-token"
        env = {
            **os.environ,
            "ZF_WORKSPACE_HOME": str(output / "workspace"),
            "ZF_TESTKIT_BROWSER_ROOT": str(project),
            "ZF_TESTKIT_BROWSER_DIST": str(output / "ui"),
            "ZF_WEB_ACTION_TOKEN": token,
            "PYTHONPATH": str(repo),
        }
        for key in ("ZF_WEB_TRUSTED_SESSION", "ZF_WEB_PASSCODE"):
            env.pop(key, None)
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
            state_response = client.get("/api/snapshot")
            assert state_response.status_code == 200, state_response.text
            state_payload = state_response.json()
            project_id = str(state_payload["project"]["project_id"])

        command = [
            "docker", "run", "--rm", "--pull", "never", "--name", container,
            "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}", "--entrypoint", "bash",
            "-v", f"{repo}:/work:ro", "-v", f"{output}:/zf-run", "-w", "/work/web",
            "-e", f"ZF_WEB_BASE_URL={base}", "-e", f"ZF_WEB_ACTION_TOKEN_FOR_TEST={token}",
            "-e", f"ZF_TESTKIT_PROJECT_ID={project_id}",
            "-e", f"ZF_TESTKIT_PLAN_ID={trigger.id}",
            "-e", "ZF_TESTKIT_BROWSER_RUN=1", "mcp/playwright:latest", "-lc",
            "set -eu; browser_paths=(/ms-playwright/chromium-*/chrome-linux64/chrome); "
            "export ZF_E2E_CHROMIUM_EXECUTABLE_PATH=${browser_paths[-1]}; "
            "test -x \"$ZF_E2E_CHROMIUM_EXECUTABLE_PATH\"; "
            "./node_modules/.bin/playwright test --config playwright.testkit.plan-approval.config.ts",
        ]
        with (output / "docker-browser.log").open("w") as stream:
            browser = subprocess.Popen(command, cwd=repo, stdout=stream, stderr=stream,
                                       start_new_session=True)
        deadline = time.monotonic() + 90
        # Wait for the real browser action; no test-only HTTP routes or route mocks.
        while not any(e.type == "plan.approved" for e in runtime.event_log.read_all()):
            assert browser.poll() is None, (output / "docker-browser.log").read_text()
            assert time.monotonic() < deadline, "browser approval deadline"
            time.sleep(0.1)
        runtime = Orchestrator(state, config, transport, project_root=project)
        tokens = {role: provision_role_submit_credential(state, role)
                  for role in ("impl-1", "verify-1")}
        runtime.run_once()
        assert len(transport.contexts) == 1
        _, body = _submit_generation_stage(runtime, transport.contexts[-1], tokens["impl-1"], repair=True)
        target = body["target_commit"]
        runtime.run_once()
        assert len(transport.contexts) == 2
        _submit_generation_stage(runtime, transport.contexts[-1], tokens["verify-1"])
        runtime.run_once()
        assert runtime.task_store.get("TASK-BLOCKING").status == "done"
        events = runtime.event_log.read_all()
        [ready] = [e for e in events if e.type == "candidate.ready"]
        [integrated] = [e for e in events if e.type == "integration.queue.integrated"]
        receipt = hydrate_sidecar_ref(state, integrated.payload["receipt_ref"]).payload
        freeze = hydrate_sidecar_ref(state, ready.payload["freeze_receipt_ref"]).payload
        assert receipt["task_commit"] == target
        assert freeze["status"] == "frozen"
        assert _git(project, "show", f"{ready.payload['commit']}:README.md") == "K462 repaired"
        result.update(candidate_commit=ready.payload["commit"], target_commit=target,
                      integration_receipt=receipt, freeze_receipt=freeze)
        assert browser.wait(timeout=60) == 0, (output / "docker-browser.log").read_text()
        report = json.loads((output / "playwright.json").read_text())
        assert report["stats"]["expected"] == 1
        assert all(report["stats"][key] == 0 for key in ("unexpected", "skipped", "flaky"))
        events = runtime.event_log.read_all()
        approvals = [event for event in events if event.type == "plan.approved"]
        assert len(approvals) == 1
        assert approvals[0].actor == "operator"
        assert approvals[0].payload["plan_id"] == trigger.id
        truth = json.loads((output / "browser-truth.json").read_text())
        assert truth["action"]["event_id"] == approvals[0].id
        assert truth["request"]["payload"]["plan_id"] == trigger.id
        assert truth["request"]["project_id"] == project_id
        assert truth["dom_status"] == truth["reload_status"] == "done"
        runtime = Orchestrator(state, config, transport, project_root=project)
        runtime.run_once()
        assert len(transport.contexts) == 2
        assert sum(e.type == "candidate.ready" for e in runtime.event_log.read_all()) == 1
        assert (output / "before.png").stat().st_size > 1000
        assert (output / "after.png").stat().st_size > 1000
        assert list((output / "browser-artifacts").rglob("trace.zip"))
        assert not (project / ".zf").exists()
        result["verdict"] = "pass"
    finally:
        if browser is not None:
            _terminate(browser)
        if api is not None:
            _terminate(api)
        cleanup_ok = api is None or api.poll() is not None
        if docker_available:
            run(["docker", "rm", "-f", container], "container-cleanup", timeout=15)
            cleanup_ok = cleanup_ok and run(
                ["docker", "container", "inspect", container], "container-check", timeout=15,
            ) != 0
        if port is not None:
            with socket.socket() as probe:
                cleanup_ok = cleanup_ok and probe.connect_ex(("127.0.0.1", port)) != 0
        runtime.event_writer.append(ZfEvent(type="simulation.done", actor="zf-cli",
                                           payload={"purpose": "testkit-plan-approval"}))
        result["cleanup_ok"] = cleanup_ok
        if not cleanup_ok:
            result["verdict"] = "harness_failure"
        write_json_atomic(output / "browser-gate.json", result)
        assert cleanup_ok, "owned API/container/port remained after cleanup"
