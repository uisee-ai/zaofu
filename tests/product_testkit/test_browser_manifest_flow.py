from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from .runner import run_manifest_and_collect


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.integration
def test_generic_manifest_runs_real_docker_playwright_journey(tmp_path: Path):
    """The browser contract is exercised through the same runner as products."""

    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("Docker daemon unavailable")
    if subprocess.run(["docker", "image", "inspect", "mcp/playwright:latest"], capture_output=True).returncode != 0:
        pytest.skip("mcp/playwright:latest image unavailable")
    project = tmp_path / "web-product"
    project.mkdir()
    port = _free_port()
    (project / "app.py").write_text(
        "from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer\n"
        "import os\n"
        "os.chdir(os.path.dirname(__file__))\n"
        "ThreadingHTTPServer(('0.0.0.0', int(os.environ['PORT'])), SimpleHTTPRequestHandler).serve_forever()\n",
        encoding="utf-8",
    )
    (project / "health.py").write_text(
        "import sys, urllib.request\n"
        "with urllib.request.urlopen(sys.argv[1], timeout=2) as r: raise SystemExit(0 if r.status == 200 else 1)\n",
        encoding="utf-8",
    )
    (project / "index.html").write_text(
        "<!doctype html><title>fixture</title><button id='submit'>Submit</button>"
        "<output id='status'>Pending</output><script>document.getElementById('submit').onclick=()=>document.getElementById('status').textContent='Submitted'</script>",
        encoding="utf-8",
    )
    (project / "smoke.spec.js").write_text(
        "const { test, expect } = require('@playwright/test');\n"
        "test('submit journey survives refresh', async ({ page }) => {\n"
        "  await page.goto('/');\n"
        "  await expect(page.locator('#status')).toHaveText('Pending');\n"
        "  await page.locator('#submit').click();\n"
        "  await expect(page.locator('#status')).toHaveText('Submitted');\n"
        "  await page.screenshot({path: `${process.env.ZF_BROWSER_ARTIFACT_DIR}/submit.png`});\n"
        "  await page.reload();\n"
        "  await expect(page.locator('#status')).toHaveText('Pending');\n"
        "});\n",
        encoding="utf-8",
    )
    (project / "package.json").write_text(
        json.dumps({"private": True, "devDependencies": {"@playwright/test": "1.63.0"}}),
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "."],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1", "project_type": "web",
        "source_root": ".", "target_commit": commit, "setup": [{
            "id": "browser-deps", "command": "npm install --ignore-scripts --no-audit --no-fund",
            "timeout_seconds": 120,
        }],
        "commands": [{
            "id": "foundation", "command": "true", "tier": "unit", "acceptance_ids": ["AC-1"],
            "domain_ids": ["P-01", "P-02", "P-03", "P-04", "P-05", "P-11", "P-16", "P-17", "P-18"],
            "mandatory": True, "deterministic": True, "timeout_seconds": 10,
        }],
        "browser_tests": [{
            "id": "browser-smoke", "entrypoint_id": "web", "spec": "smoke.spec.js",
            "base_url": f"http://127.0.0.1:{port}", "image": "mcp/playwright:latest", "tier": "e2e",
            "acceptance_ids": ["AC-1"], "domain_ids": ["P-08"], "mandatory": True,
            "deterministic": True, "timeout_seconds": 180, "install_timeout_seconds": 180,
            "install_browser": False,
        }],
        "acceptances": [{"id": "AC-1"}],
        "entrypoints": [{
            "entrypoint_id": "web", "start_command": f"env PORT={port} {sys.executable} app.py",
            "health_check": f"{sys.executable} health.py http://127.0.0.1:{port}/",
            "owner": "product-test", "timeout_seconds": 10,
        }],
        "capabilities": {"web": True}, "budgets": {},
        "not_applicable": {"cli": {"reason": "browser-only fixture", "source": "fixture profile"},
                            "api": {"reason": "browser-only fixture", "source": "fixture profile"},
                            "database": {"reason": "no persistence", "source": "fixture profile"},
                            "external_service": {"reason": "no dependency", "source": "fixture profile"},
                            "long_running": {"reason": "service lifecycle is browser-owned", "source": "fixture profile"},
                            "release": {"reason": "not release tier", "source": "fixture profile"}},
        "cleanup": [],
    }
    manifest_path = project / "product-test-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = run_manifest_and_collect(
        manifest_path, project_root=project, output_dir=tmp_path / "evidence", run_id="browser-run",
        env={"HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "", "http_proxy": "", "https_proxy": "",
             "all_proxy": "", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
    )
    assert result["verdict"] == "pass", result
    assert result["browser_tests"][0]["verdict"] == "pass"
    assert result["browser_tests"][0]["tests"] == 1
    assert result["services"][0]["stopped"] is True
    assert "playwright/browser-smoke/result.json" in {row["ref"] for row in result["artifact_index"]}
