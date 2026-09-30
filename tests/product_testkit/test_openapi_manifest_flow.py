from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys

from .runner import run_manifest_and_collect


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_manifest_runner_executes_real_openapi_contract_inside_service_lifecycle(tmp_path: Path):
    project = tmp_path / "product"
    project.mkdir()
    port = _free_port()
    (project / "service.py").write_text(
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "import json, sys\n"
        "class H(BaseHTTPRequestHandler):\n"
        "  def do_GET(self):\n"
        "    if self.path == '/health': body, status = b'{}', 200\n"
        "    elif self.path == '/items': body, status = json.dumps([{'id': 1}]).encode(), 200\n"
        "    else: body, status = b'{}', 404\n"
        "    self.send_response(status); self.send_header('Content-Type', 'application/json')\n"
        "    self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)\n"
        "  def log_message(self, *_): pass\n"
        "ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()\n",
        encoding="utf-8",
    )
    (project / "health.py").write_text(
        "import sys, urllib.request\n"
        "with urllib.request.urlopen(sys.argv[1], timeout=1) as response:\n"
        "  raise SystemExit(0 if response.status == 200 else 1)\n",
        encoding="utf-8",
    )
    (project / "foundation.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (project / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "generic fixture", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "ok",
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "required": ["id"],
                                                    "properties": {"id": {"type": "integer"}},
                                                },
                                            }
                                        }
                                    },
                                }
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "service.py", "health.py", "foundation.py", "openapi.json"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "api",
        "source_root": ".",
        "target_commit": commit,
        "setup": [],
        "commands": [
            {
                "id": "foundation",
                "command": f"{sys.executable} foundation.py",
                "tier": "unit",
                "acceptance_ids": ["AC-1"],
                "domain_ids": ["P-01", "P-02", "P-03", "P-04", "P-05", "P-17", "P-18"],
                "mandatory": True,
                "deterministic": True,
                "timeout_seconds": 10,
            }
        ],
        "api_contracts": [
            {
                "id": "openapi-contract",
                "entrypoint_id": "api",
                "schema": "openapi.json",
                "base_url": f"http://127.0.0.1:{port}",
                "tool_version": "4.26.1",
                "tier": "integration",
                "acceptance_ids": ["AC-1"],
                "domain_ids": ["P-07", "P-11"],
                "mandatory": True,
                "deterministic": True,
                "timeout_seconds": 30,
                "phases": ["fuzzing"],
                "mode": "positive",
                "checks": [
                    "not_a_server_error",
                    "status_code_conformance",
                    "response_schema_conformance",
                ],
                "workers": 1,
                "seed": 1729,
                "max_examples": 1,
                "max_time_seconds": 1,
                "request_timeout_seconds": 1,
                "request_retries": 0,
            }
        ],
        "acceptances": [{"id": "AC-1"}],
        "entrypoints": [
            {
                "entrypoint_id": "api",
                "start_command": f"{sys.executable} service.py {port}",
                "health_check": f"{sys.executable} health.py http://127.0.0.1:{port}/health",
                "owner": "product-test",
                "timeout_seconds": 5,
            }
        ],
        "capabilities": {"api": True},
        "budgets": {},
        "not_applicable": {},
        "cleanup": [],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "evidence"
    no_proxy = {
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
        "http_proxy": "",
        "https_proxy": "",
        "all_proxy": "",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }

    result = run_manifest_and_collect(
        manifest_path,
        project_root=project,
        output_dir=output,
        run_id="generic-openapi-run",
        env=no_proxy,
    )

    assert result["verdict"] == "pass"
    assert result["domain_coverage"]["missing"] == []
    assert "P-07" in result["domain_coverage"]["executed"]
    [api_contract] = result["api_contracts"]
    assert api_contract["contract_id"] == "openapi-contract"
    assert api_contract["operations_tested"] == 1
    assert api_contract["verdict"] == "pass"
    assert result["services"][0]["stopped"] is True
    refs = {row["ref"] for row in result["artifact_index"]}
    assert {
        "schemathesis/openapi-contract/result.json",
        "schemathesis/openapi-contract/junit.xml",
        "schemathesis.openapi-contract.receipt.json",
        "schemathesis.openapi-contract.version.receipt.json",
    } <= refs
