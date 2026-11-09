"""Isolated Docker Playwright -> Web -> real Codex model/effort proof.

Run with `.venv/bin/python tests/e2e/kanban_model_real_e2e.py`. `--api-only`
checks the project Web API and real provider without claiming browser proof.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from zf.web.agent_turn_store import AgentTurnStore  # noqa: E402
from zf.web.headless_agent import (  # noqa: E402
    CodexHeadlessBackend,
    HeadlessThreadStore,
    _CodexRpcClient,
)


def _port() -> int:
    for port in range(8004, 9000):
        with socket.socket() as sock:
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free test port")


def _json(url: str, token: str, *, payload: dict | None = None, timeout: int = 15) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, headers={
        "X-ZF-Web-Token": token, "Authorization": f"Bearer {token}", "Content-Type": "application/json",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _observed_config(root: Path, session_id: str) -> tuple[str, str]:
    rpc = _CodexRpcClient(CodexHeadlessBackend().build_args(), cwd=root, timeout_s=15, tool_timeout_s=15)
    try:
        rpc.start()
        rpc.request("initialize", {"clientInfo": {"name": "zf-model-e2e", "version": "1"}, "capabilities": {"experimentalApi": True}})
        rpc.notify("initialized")
        result = rpc.request("thread/resume", {"threadId": session_id, "cwd": str(root)})
        return str(result.get("model") or ""), str(result.get("reasoningEffort") or "")
    finally:
        rpc.close()


def main() -> int:
    if sys.argv[1:] not in ([], ["--api-only"]):
        raise RuntimeError("usage: kanban_model_real_e2e.py [--api-only]")
    api_only = sys.argv[1:] == ["--api-only"]
    if not api_only:
        if not shutil.which("docker"):
            raise RuntimeError("Docker is required for Playwright")
        probe = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=15)
        if probe.returncode:
            raise RuntimeError(f"Docker is unavailable: {probe.stderr.strip()}")
    python = ROOT / ".venv/bin/python"
    zf = ROOT / ".venv/bin/zf"
    modules = ROOT / "web/node_modules"
    if not zf.exists() or not python.exists() or (not api_only and not (modules / ".bin/playwright").exists()):
        raise RuntimeError("install project dependencies: uv sync --extra dev --extra web; npm ci --prefix web")
    if not api_only:
        subprocess.run(["npm", "--prefix", str(ROOT / "web"), "run", "build"], cwd=ROOT, check=True)

    run_root = Path(tempfile.mkdtemp(prefix="zf-kanban-model-e2e-", dir="/tmp"))
    project = run_root / "project"
    project.mkdir()
    shutil.copyfile(ROOT / "tests/e2e/fixtures/kanban-real-coding/zf.yaml", project / "zf.yaml")
    state = project / ".zf"
    token = secrets.token_hex(24)
    port = _port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"), "ZF_STATE_DIR": str(state),
        "ZF_WORKSPACE_HOME": str(run_root / "workspace"), "ZF_WEB_ACTION_TOKEN": token,
        "ZF_KANBAN_AGENT_BACKEND": "codex-headless", "ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S": "180",
    })
    server: subprocess.Popen | None = None
    initialized = False
    try:
        subprocess.run([str(zf), "init", "--no-workspace-register", "--skip-instruction-docs", "--no-git-hooks"], cwd=project, env=env, check=True)
        initialized = True
        with (run_root / "web.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen([str(zf), "web", "--host", "0.0.0.0", "--port", str(port), "--state-dir", str(state)],
                                      cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(90):
            if server.poll() is not None:
                raise RuntimeError("isolated Web server exited before becoming ready")
            try:
                info = _json(f"{base}/api/workspace/projects", token)
                if info.get("server_default_project_id"):
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("isolated Web server did not become ready")
        _json(f"{base}/api/workspace/onboarding", token, payload={"action": "skip"})
        project_id = info["server_default_project_id"]
        catalog_info = _json(f"{base}/api/projects/{project_id}/webchat/model-catalog", token)
        catalog = catalog_info["models"]
        defaults = catalog_info.get("session_defaults") or {}
        if not defaults.get("model") or not defaults.get("effort"):
            raise RuntimeError("Codex did not expose restorable default model and effort")
        choice = next((item for item in catalog if item["model"] == "gpt-6.1-sol" and "xhigh" in item["efforts"] and len(set(item["efforts"])) > 1), None)
        choice = choice or next((item for item in catalog if len(set(item["efforts"])) > 1), None)
        if not choice:
            raise RuntimeError("Codex did not advertise a selectable model and effort")
        effort = "xhigh" if "xhigh" in choice["efforts"] else choice["efforts"][-1]
        second_effort = "low" if "low" in choice["efforts"] and effort != "low" else next(value for value in choice["efforts"] if value != effort)
        if api_only:
            turn_ids = [f"model-proof-{secrets.token_hex(6)}" for _ in range(3)]
            for turn_id, selected_effort in zip(turn_ids, (effort, second_effort, ""), strict=True):
                payload = {"backend": "codex-headless",
                           "permission_profile": "read_only", "project_id": project_id, "conversation_id": f"kanban:{project_id}",
                           "thread_key": "main", "turn_id": turn_id, "sync": True, "message": "Reply exactly: ZF_MODEL_PROOF_OK"}
                if selected_effort:
                    payload.update({"model": choice["model"], "thinking_level": selected_effort})
                result = _json(f"{base}/api/projects/{project_id}/actions/chat-orchestrator", token,
                               payload={"project_id": project_id, "idempotency_key": turn_id, "actor": "web", "payload": payload}, timeout=180)
                if result.get("ok") is False:
                    raise RuntimeError(f"Web action failed: {result.get('reason') or result.get('status')}")
            proof = {"model": choice["model"], "effort": effort, "secondEffort": second_effort,
                     "turnId": turn_ids[0], "secondTurnId": turn_ids[1], "defaultTurnId": turn_ids[2],
                     "defaultModel": defaults["model"], "defaultEffort": defaults["effort"], "threadKey": "main"}
        else:
            proof = _run_browser(ROOT, run_root, modules, base, token, choice["model"], effort, second_effort, env)
        if proof["model"] != choice["model"] or proof["effort"] != effort or proof["secondEffort"] != second_effort:
            raise RuntimeError("recorded proof differs from the advertised selection")
        for turn_id, selected_effort in ((proof["turnId"], effort), (proof["secondTurnId"], second_effort)):
            turn = AgentTurnStore(state).get(turn_id)
            submitted = turn["request"]["payload"]
            if turn["state"] != "completed" or submitted.get("model") != choice["model"] or submitted.get("thinking_level") != selected_effort:
                raise RuntimeError(f"durable turn {turn_id} did not retain the selected settings")
        default_turn = AgentTurnStore(state).get(proof["defaultTurnId"])
        default_payload = default_turn["request"]["payload"]
        if default_turn["state"] != "completed" or default_payload.get("model") or default_payload.get("thinking_level"):
            raise RuntimeError("reset turn did not preserve the default selection")
        if (proof["defaultModel"], proof["defaultEffort"]) != (defaults["model"], defaults["effort"]):
            raise RuntimeError("browser default differs from the provider configuration")
        thread = HeadlessThreadStore(state_dir=state, project_root=project).load(thread_key=proof["threadKey"])
        session = thread["providers"]["codex-headless"]["provider_session_id"]
        actual_model, actual_effort = _observed_config(project, session)
        if (actual_model, actual_effort) != (defaults["model"], defaults["effort"]):
            raise RuntimeError("Codex session did not return to its displayed default")
        mode = "project Web API" if api_only else "browser selection"
        print(f"PASS: {mode}, selected {choice['model']} / {effort} then {second_effort}; default restored to {actual_model} / {actual_effort}")
        return 0
    finally:
        if initialized:
            subprocess.run([str(zf), "emit", "simulation.done", "--actor", "model-e2e", "--payload", '{"purpose":"kanban-model-playwright"}', "--state-dir", str(state)],
                           cwd=project, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if server and server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait()
        shutil.rmtree(run_root)


def _run_browser(root: Path, run_root: Path, modules: Path, base: str, token: str,
                 model: str, effort: str, second_effort: str, env: dict[str, str]) -> dict:
    env.update({"ZF_WEB_BASE_URL": base, "ZF_WEB_ACTION_TOKEN_FOR_TEST": token,
                "ZF_MODEL_E2E_MODEL": model, "ZF_MODEL_E2E_EFFORT": effort, "ZF_MODEL_E2E_SECOND_EFFORT": second_effort,
                "ZF_MODEL_E2E_ARTIFACT": "/zf-run/browser.json"})
    resolved_modules = modules.resolve()
    command = [
        "docker", "run", "--rm", "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}",
        "--entrypoint", "bash", "-v", f"{root}:/work", "-v", f"{run_root}:/zf-run",
        "-v", f"{resolved_modules}:{resolved_modules}:ro", "-w", "/work/web",
    ]
    for name in ("ZF_WEB_BASE_URL", "ZF_WEB_ACTION_TOKEN_FOR_TEST", "ZF_MODEL_E2E_MODEL", "ZF_MODEL_E2E_EFFORT", "ZF_MODEL_E2E_SECOND_EFFORT", "ZF_MODEL_E2E_ARTIFACT"):
        command.extend(["-e", name])
    command.extend(["-e", "HOME=/tmp", "-e", "PLAYWRIGHT_BROWSERS_PATH=0", "mcp/playwright:latest", "-lc",
                    'set -euo pipefail; chromium="$(find /ms-playwright -type f -path "*/chrome-linux64/chrome" | sort | tail -1)"; '
                    'test -n "$chromium"; export ZF_E2E_CHROMIUM_EXECUTABLE_PATH="$chromium"; '
                    './node_modules/.bin/playwright test tests/kanban-model-real-provider.spec.ts --project=chromium --workers=1 --reporter=line --output=/zf-run/test-results'])
    subprocess.run(command, env=env, check=True, timeout=420)
    return json.loads((run_root / "browser.json").read_text())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
