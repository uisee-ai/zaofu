"""Explicit Docker browser tier; never calls a real Provider or dev Web.

Run: uv run pytest tests/e2e/test_testkit_browser_truth_chain.py -q -m host --no-cov
"""

from __future__ import annotations

from dataclasses import asdict
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

from tests.testkit.evidence import evidence_ref, write_json_atomic
from tests.testkit.runner import _terminate
from tests.testkit.web_truth import assert_sse_event_once, assert_status_convergence
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore


@pytest.mark.host
def test_docker_drag_matches_canonical_store_event_api_sse_and_dom(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    output = tmp_path / "browser-evidence"
    output.mkdir()
    project = output / "project"
    project.mkdir()
    (project / "zf.yaml").write_text(
        "project:\n  name: testkit-browser\n  state_dir: runtime-state\nroles: []\nworkflow:\n  dag:\n    stage_order: [tasks]\n",
        encoding="utf-8",
    )
    state_dir = project / "runtime-state"
    store = TaskStore(state_dir / "kanban.json")
    task = store.add(Task(id="TASK-TESTKIT-BROWSER", title="TestKit browser truth"))
    regression_task = store.add(
        Task(
            id="TASK-TESTKIT-BROWSER-REGRESSION",
            title="TestKit browser regression",
            status="blocked",
            contract=TaskContract(feature_id="F-TESTKIT-BROWSER", phase="tasks"),
        )
    )
    # Seed one representative task for every board projection.  Terminal
    # records are transitioned through TaskStore so the browser also proves
    # that archive_tasks is merged back into the board projection.
    status_tasks = {
        "TASK-TESTKIT-TESTING": ("review", "test"),
        "TASK-TESTKIT-BLOCKED": ("blocked", None),
        "TASK-TESTKIT-DONE": ("done", None),
        "TASK-TESTKIT-CANCELLED": ("cancelled", None),
    }
    for task_id, (status, assigned_to) in status_tasks.items():
        seeded = store.add(
            Task(
                id=task_id,
                title=f"TestKit {status} projection",
                status="backlog" if status in {"done", "cancelled"} else status,
                assigned_to=assigned_to,
            )
        )
        if status in {"done", "cancelled"}:
            assert store.update(seeded.id, status=status) is not None
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(type="task.created", task_id=task.id, actor="zf-cli"))
    writer.append(ZfEvent(type="task.created", task_id=regression_task.id, actor="zf-cli"))
    writer.append(ZfEvent(
        type="task.dispatched",
        task_id=regression_task.id,
        actor="zf-kernel",
        payload={"dispatch_id": "dispatch-testkit-regression"},
    ))
    writer.append(ZfEvent(type="task.blocked", task_id=regression_task.id, actor="zf-kernel"))
    write_json_atomic(output / "store-before.json", asdict(task))
    result = {"verdict": "harness_failure", "phase": "setup"}
    container = f"zf-testkit-browser-{uuid4().hex[:12]}"
    api = None
    docker_available = False
    port = None
    sse_disconnects = os.environ.get("ZF_TESTKIT_SSE_DISCONNECTS", "1").strip()
    assert sse_disconnects.isdigit() and int(sse_disconnects) >= 0

    def run(argv: list[str], name: str, *, cwd: Path = repo, timeout: float = 120) -> int:
        with (output / f"{name}.log").open("w") as stream:
            completed = subprocess.run(argv, cwd=cwd, stdout=stream, stderr=stream, timeout=timeout)
        return completed.returncode

    try:
        result["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        result["dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=repo))
        assert run(["docker", "info"], "docker-info", timeout=15) == 0
        docker_available = True
        result["image_id"] = subprocess.check_output(
            ["docker", "image", "inspect", "mcp/playwright:latest", "--format", "{{.Id}}"],
            text=True, timeout=15,
        ).strip()
        assert run(["npm", "exec", "--", "vite", "build", "--outDir", str(output / "ui")],
                   "build", cwd=repo / "web") == 0
        with socket.socket() as bound:
            bound.bind(("0.0.0.0", 0))
            port = bound.getsockname()[1]
        assert port > 8001
        base = f"http://127.0.0.1:{port}"
        result["service_url"] = base
        token = "testkit-local-only"
        env = {**os.environ, "ZF_WORKSPACE_HOME": str(output / "workspace"),
               "ZF_TESTKIT_BROWSER_ROOT": str(project), "ZF_TESTKIT_BROWSER_DIST": str(output / "ui"),
               "ZF_WEB_ACTION_TOKEN": token, "PYTHONPATH": str(repo)}
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
                assert time.monotonic() < deadline, "isolated API startup timeout"
                time.sleep(0.1)
            onboarding = client.post("/api/workspace/onboarding", json={"action": "skip"},
                                     headers={"x-zf-web-token": token})
            assert onboarding.status_code == 200, onboarding.text
        command = [
            "docker", "run", "--rm", "--pull", "never", "--name", container, "--network", "host",
            "--user", f"{os.getuid()}:{os.getgid()}", "--entrypoint", "bash",
            "-v", f"{repo}:/work:ro", "-v", f"{output}:/zf-run", "-w", "/work/web",
            "-e", f"ZF_WEB_BASE_URL={base}", "-e", f"ZF_WEB_ACTION_TOKEN_FOR_TEST={token}",
            "-e", "ZF_TESTKIT_BROWSER_RUN=1", "-e", f"ZF_TESTKIT_SSE_DISCONNECTS={sse_disconnects}",
            "mcp/playwright:latest", "-lc",
            "set -eu; browser_paths=(/ms-playwright/chromium-*/chrome-linux64/chrome); "
            "export ZF_E2E_CHROMIUM_EXECUTABLE_PATH=${browser_paths[-1]}; "
            "test -x \"$ZF_E2E_CHROMIUM_EXECUTABLE_PATH\"; "
            "./node_modules/.bin/playwright test --config playwright.testkit.config.ts",
        ]
        result.update(phase="browser", command=command)
        code = run(command, "docker-browser", timeout=180)
        result["exit_code"] = code
        report = json.loads((output / "playwright.json").read_text())
        result.update(phase="assertions", verdict="fail", stats=report["stats"])
        assert code == 0, (output / "docker-browser.log").read_text() + str(report["stats"])
        assert report["stats"]["expected"] == 2
        assert all(report["stats"][key] == 0 for key in ("unexpected", "skipped", "flaky"))
        truth = json.loads((output / "browser-truth.json").read_text())
        assert truth["injected_disconnects"] == int(sse_disconnects)
        assert truth["unauthorized_status"] in {401, 403}
        assert truth["unauthorized_create_status"] in {401, 403}
        regression = truth["regression"]
        assert regression["denied_status"] in {401, 403}
        assert regression["capture"]["result"]["case_id"] == "REG-TESTKIT-BROWSER"
        assert any(
            item["case_id"] == "REG-TESTKIT-BROWSER"
            and item["source_task_id"] == regression_task.id
            for item in regression["cases"]["cases"]
        )
        assert regression["replay"]["result"]["passed"] is True
        assert truth["authorized_create_status"] == 201
        assert truth["stream_attempts"] >= int(sse_disconnects) + 1
        assert [item["id"] for item in truth["status_matrix"]] == [
            "TASK-TESTKIT-TESTING",
            "TASK-TESTKIT-BLOCKED",
            "TASK-TESTKIT-DONE",
            "TASK-TESTKIT-CANCELLED",
        ]
        with httpx.Client(trust_env=False, timeout=5) as snapshot_client:
            snapshot = snapshot_client.get(f"{base}/api/state")
        assert snapshot.status_code == 200
        snapshot_body = snapshot.json()
        archive_statuses = {
            item["id"]: item["status"] for item in snapshot_body["archive_tasks"]
        }
        assert archive_statuses["TASK-TESTKIT-DONE"] == "done"
        assert archive_statuses["TASK-TESTKIT-CANCELLED"] == "cancelled"
        current = TaskStore(state_dir / "kanban.json").get(task.id)
        assert current is not None and current.status == "in_progress"
        write_json_atomic(output / "store-after.json", asdict(current))
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert (state_dir / "artifacts" / "regression" / "REG-TESTKIT-BROWSER.json").is_file()
        captured_events = [
            event for event in events
            if event.type == "regression.case.captured"
            and event.payload.get("case_id") == "REG-TESTKIT-BROWSER"
        ]
        replayed_events = [
            event for event in events
            if event.type == "regression.case.replayed"
            and event.payload.get("case_id") == "REG-TESTKIT-BROWSER"
        ]
        assert len(captured_events) == 1
        assert len(replayed_events) == 1
        assert replayed_events[0].payload["passed"] is True
        assert {item.id for item in TaskStore(state_dir / "kanban.json").list_all_with_archive()} == {
            "TASK-TESTKIT-BROWSER",
            "TASK-TESTKIT-BROWSER-REGRESSION",
            "TASK-TESTKIT-TESTING",
            "TASK-TESTKIT-BLOCKED",
            "TASK-TESTKIT-DONE",
            "TASK-TESTKIT-CANCELLED",
            "TASK-TESTKIT-CREATED",
        }
        updates = [event for event in events if event.type == "task.updated" and event.task_id == task.id]
        assert len(updates) == 1
        created = [
            event
            for event in events
            if event.type == "task.created"
            and event.task_id == "TASK-TESTKIT-CREATED"
        ]
        assert len(created) == 1
        update = updates[0]
        assert update.id == truth["action"]["event_id"]
        assert update.payload["task"]["status"] == truth["after"]["task"]["status"] == current.status
        assert_sse_event_once(update.id, truth["sse"])
        assert_sse_event_once(update.id, truth["observer_sse"])
        assert update.causation_id in {event.id for event in events}
        assert not any(event.type in {"run.goal.completed", "task.done"} for event in events)
        assert_status_convergence(
            current.status,
            {
                "dom": truth["dom_status"],
                "observer_dom": truth["observer_dom_status"],
                "reload": truth["reload_status"],
                "api": truth["after"]["task"]["status"],
            },
        )
        assert not (project / ".zf").exists()
        assert (output / "before.png").stat().st_size > 1000
        assert (output / "after.png").stat().st_size > 1000
        assert list((output / "browser-artifacts").rglob("trace.zip"))
        result["verdict"] = "pass"
    finally:
        if api is not None:
            _terminate(api)
        # Address only our uniquely named container; never affect dev services.
        cleanup_ok = api is None or api.poll() is not None
        if port is not None:
            with socket.socket() as probe:
                cleanup_ok = cleanup_ok and probe.connect_ex(("127.0.0.1", port)) != 0
        if docker_available:
            run(["docker", "rm", "-f", container], "container-cleanup", timeout=15)
            cleanup_ok = cleanup_ok and run(
                ["docker", "container", "inspect", container], "container-check", timeout=15,
            ) != 0
        result["cleanup_ok"] = cleanup_ok
        if not cleanup_ok:
            result["verdict"] = "harness_failure"
        writer.append(ZfEvent(type="simulation.done", actor="zf-cli", payload={"purpose": "testkit-browser"}))
        log.close()
        result["evidence"] = [evidence_ref(path, root=output) for path in sorted(output.rglob("*")) if path.is_file()]
        write_json_atomic(output / "browser-gate.json", result)
        print(f"Browser evidence: {output}")
        assert cleanup_ok, "test-owned process, port or container was not cleaned"
