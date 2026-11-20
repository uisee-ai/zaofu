"""Docker browser acceptance against isolated, real Web/API fixtures.

Run after `npm --prefix web run build` with `.venv/bin/python`.
This verifies real API/actions; fixture data and workflow backends are not
real-provider evidence. Each scenario is independently seeded and cleaned.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from zf.core.config.loader import load_config


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    evidence = Path(f"/tmp/zf-inbox-review-evidence-{stamp}")
    evidence.mkdir()
    results = []
    for scenario, test in (("plan", "inbox-decision-real"), ("channel", "inbox-channel-real"),
                           ("kanban", "inbox-kanban-real")):
        state = Path(f"/tmp/zf-inbox-{scenario}-{stamp}")
        output = evidence / scenario
        output.mkdir()
        with socket.socket() as sock:
            sock.bind(("0.0.0.0", 0))
            port = sock.getsockname()[1]
        env = {**os.environ, "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}"}
        with (output / "web.log").open("w") as log:
            server = subprocess.Popen([
                sys.executable, str(ROOT / "tests/e2e/inbox_browser_fixture.py"),
                "--scenario", scenario, "--root", str(state), "--port", str(port),
            ], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(120):
                if server.poll() is not None:
                    raise RuntimeError(f"{scenario} server exited; see {output}")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/workspace/projects", timeout=1):
                        break
                except OSError:
                    time.sleep(0.25)
            else:
                raise RuntimeError(f"{scenario} server did not become ready")
            command = [
                "docker", "run", "--rm", "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}",
                "--entrypoint", "bash", "-v", f"{ROOT}:/work", "-v", f"{output}:/evidence",
                "-w", "/work/web", "-e", f"ZF_WEB_BASE_URL=http://127.0.0.1:{port}",
                "-e", f"ZF_INBOX_BROWSER_FIXTURE={scenario}", "-e", "ZF_E2E_VIDEO=on",
                "mcp/playwright:latest", "-c",
                "export ZF_E2E_CHROMIUM_EXECUTABLE_PATH=$(find /ms-playwright -type f -path '*/chrome-linux*/chrome' | sort | tail -1); "
                f"./node_modules/.bin/playwright test tests/{test}.spec.ts --workers=1 --reporter=line --output=/evidence/browser",
            ]
            print(f"RUN {scenario} at port {port}", flush=True)
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=240)
            (output / "browser.log").write_text(result.stdout + result.stderr)
            print(result.stdout, flush=True)
            results.append({"scenario": scenario, "exit_code": result.returncode})
            (evidence / "results.json").write_text(json.dumps(results, indent=2))
            if result.returncode:
                raise RuntimeError(f"{scenario} browser failed; see {output}")
        finally:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            config_path = state / "zf.yaml"
            state_name = load_config(config_path).project.state_dir if config_path.exists() else ".zf"
            ledger = state / state_name / "events.jsonl"
            if ledger.exists():
                shutil.copyfile(ledger, output / "events.jsonl")
            if state.exists():
                shutil.rmtree(state)
    print(f"PASS: {evidence}", flush=True)


if __name__ == "__main__":
    main()
