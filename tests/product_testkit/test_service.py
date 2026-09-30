import json
from pathlib import Path
import sys

import pytest

from .service import ServiceError, start_entrypoint, start_service


def test_service_lifecycle_uses_health_and_cleans(tmp_path: Path):
    script = tmp_path / "server.py"
    script.write_text(
        "from http.server import BaseHTTPRequestHandler,HTTPServer\n"
        "class H(BaseHTTPRequestHandler):\n"
        " def do_GET(self): self.send_response(200); self.end_headers()\n"
        " def log_message(self,*a): pass\n"
        "HTTPServer(('127.0.0.1', 0), H).serve_forever()\n",
        encoding="utf-8",
    )
    # Use a deterministic fixed free port selected by the test process.
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    script.write_text(script.read_text().replace("('127.0.0.1', 0)", f"('127.0.0.1', {port})"), encoding="utf-8")
    handle = start_service(command=f"python {script}", cwd=tmp_path, health_url=f"http://127.0.0.1:{port}/health")
    assert handle.health and handle.health.ok
    handle.stop()
    assert handle.process.poll() is not None


def test_empty_service_command_fails_closed(tmp_path: Path):
    with pytest.raises(ServiceError, match="empty"):
        start_service(command="", cwd=tmp_path, health_url="http://127.0.0.1:1")


def _entrypoint(*, start_command: str, health_check: str, timeout_seconds: float = 3) -> dict:
    return {
        "entrypoint_id": "worker",
        "start_command": start_command,
        "health_check": health_check,
        "owner": "product-test",
        "timeout_seconds": timeout_seconds,
    }


def test_declared_entrypoint_runs_command_health_and_persists_cleanup_evidence(tmp_path: Path):
    marker = tmp_path / "ready"
    service = tmp_path / "service.py"
    service.write_text(
        "from pathlib import Path\n"
        "import sys, time\n"
        "Path(sys.argv[1]).write_text('ready', encoding='utf-8')\n"
        "print('service-ready', flush=True)\n"
        "while True: time.sleep(1)\n",
        encoding="utf-8",
    )
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "print('healthy') if Path(sys.argv[1]).is_file() else sys.exit(7)\n",
        encoding="utf-8",
    )
    output = tmp_path / "evidence"

    handle = start_entrypoint(
        _entrypoint(
            start_command=f"{sys.executable} {service} {marker}",
            health_check=f"{sys.executable} {probe} {marker}",
        ),
        cwd=tmp_path,
        output_dir=output,
    )
    process = handle.process
    receipt = handle.stop()

    assert receipt.passed is True
    assert receipt.healthy is True
    assert receipt.stopped is True
    assert receipt.health_attempts >= 1
    assert process.poll() is not None
    assert (output / "entrypoint.worker.stdout.log").read_text(encoding="utf-8") == "service-ready\n"
    assert "healthy" in (output / "entrypoint.worker.health.stdout.log").read_text(encoding="utf-8")
    persisted = json.loads((output / "entrypoint.worker.receipt.json").read_text(encoding="utf-8"))
    assert persisted["entrypoint_id"] == "worker"
    assert persisted["passed"] is True
    assert len(persisted["start_command_digest"]) == 64
    assert len(persisted["health_check_digest"]) == 64


def test_declared_entrypoint_health_timeout_kills_service_and_persists_failure(tmp_path: Path):
    service = tmp_path / "service.py"
    service.write_text("import time\nwhile True: time.sleep(1)\n", encoding="utf-8")
    output = tmp_path / "evidence"

    with pytest.raises(ServiceError, match="health check timed out") as caught:
        start_entrypoint(
            _entrypoint(
                start_command=f"{sys.executable} {service}",
                health_check=f"{sys.executable} -c 'raise SystemExit(9)'",
                timeout_seconds=0.2,
            ),
            cwd=tmp_path,
            output_dir=output,
            probe_interval_seconds=0.01,
        )

    receipt = caught.value.receipt
    assert receipt.passed is False
    assert receipt.healthy is False
    assert receipt.timed_out is True
    assert receipt.stopped is True
    assert receipt.health_attempts >= 1
    assert (output / "entrypoint.worker.receipt.json").is_file()


def test_declared_entrypoint_missing_executable_is_environment_failure(tmp_path: Path):
    output = tmp_path / "evidence"
    with pytest.raises(ServiceError, match="service start failed") as caught:
        start_entrypoint(
            _entrypoint(
                start_command="definitely-missing-product-command",
                health_check=f"{sys.executable} -c 'raise SystemExit(0)'",
            ),
            cwd=tmp_path,
            output_dir=output,
        )

    receipt = caught.value.receipt
    assert receipt.environment_error is True
    assert receipt.stopped is True
    assert receipt.passed is False


def test_declared_entrypoint_that_dies_after_health_cannot_pass_cleanup(tmp_path: Path):
    marker = tmp_path / "ready"
    service = tmp_path / "short_lived.py"
    service.write_text(
        "from pathlib import Path\n"
        "import sys, time\n"
        "Path(sys.argv[1]).write_text('ready', encoding='utf-8')\n"
        "time.sleep(0.15)\n",
        encoding="utf-8",
    )
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "raise SystemExit(0 if Path(sys.argv[1]).is_file() else 8)\n",
        encoding="utf-8",
    )

    handle = start_entrypoint(
        _entrypoint(
            start_command=f"{sys.executable} {service} {marker}",
            health_check=f"{sys.executable} {probe} {marker}",
        ),
        cwd=tmp_path,
        output_dir=tmp_path / "evidence",
    )
    handle.process.wait(timeout=2)
    receipt = handle.stop()

    assert receipt.exited_before_stop is True
    assert receipt.passed is False
    assert receipt.error == "service exited before TestKit cleanup"
