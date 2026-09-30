from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import subprocess
import threading

import pytest

from .runner import run_manifest_and_collect


def _available() -> bool:
    return (
        subprocess.run(["docker", "info"], capture_output=True).returncode == 0
        and subprocess.run(
            ["docker", "image", "inspect", "grafana/k6:1.5.0"], capture_output=True
        ).returncode == 0
    )


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib protocol name
        payload = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.mark.skipif(not _available(), reason="k6 Docker fixture unavailable")
def test_manifest_runs_real_k6_and_applies_summary_threshold(tmp_path: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    project = tmp_path / "project"
    project.mkdir()
    port = server.server_port
    (project / "load.js").write_text(
        "import http from 'k6/http';\n"
        "import { check } from 'k6';\n"
        "export const options = { iterations: 1, vus: 1, thresholds: { 'http_req_duration': ['p(95)<1000000'] } };\n"
        "export default function () { const response = http.get(__ENV.ZF_BASE_URL + '/health'); check(response, {'status': (r) => r.status === 200}); }\n",
        encoding="utf-8",
    )
    (project / "smoke.py").write_text("print('smoke')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"], ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"], ["git", "add", "load.js", "smoke.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    domains = [f"P-{index:02d}" for index in range(1, 19)]
    manifest = {
        "schema_version": "product-test-manifest.v1", "project_type": "api", "source_root": ".",
        "target_commit": commit, "setup": [], "commands": [{
            "id": "smoke", "command": f"{sys.executable} smoke.py", "tier": "unit", "acceptance_ids": ["AC-1"],
            "domain_ids": domains, "mandatory": True, "deterministic": True, "timeout_seconds": 20,
        }], "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {}, "budgets": {},
        "not_applicable": {}, "cleanup": [], "performance_tests": [{
            "id": "load", "script": "load.js", "image": "grafana/k6:1.5.0", "tier": "perf",
            "acceptance_ids": ["AC-1"], "domain_ids": ["P-14"], "mandatory": True,
            "deterministic": True, "timeout_seconds": 30,
            "thresholds": [{"metric": "http_req_duration", "statistic": "p(95)", "operator": "<", "limit": 1000000}],
        }],
    }
    path = project / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    try:
        result = run_manifest_and_collect(
            path, project_root=project, output_dir=tmp_path / "out", run_id="k6-run",
            env={"ZF_BASE_URL": f"http://127.0.0.1:{port}"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result["verdict"] == "pass"
    [performance] = result["performance_tests"]
    assert performance["verdict"] == "pass"
    assert Path(performance["summary_ref"]).is_file()
