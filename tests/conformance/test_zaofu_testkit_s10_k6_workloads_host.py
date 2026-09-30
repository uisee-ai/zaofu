"""Explicit real k6 workloads against an isolated ZaoFu Web instance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

import pytest

from tests.testkit.cleanup import terminate_process_group
from tests.testkit.command_runner import run_command
from tests.testkit.evidence import evidence_ref, sha256_file, write_json_atomic
from tests.testkit.performance import PerformanceThreshold, evaluate_k6_summary


pytestmark = pytest.mark.host
_K6_IMAGE = (
    "grafana/k6:1.5.0@sha256:2072ea9eafa596532d9aee0cc0e0a50cfb0e7fb703981a46179af5f4f22c5ea4"
)
_RUN = os.environ.get("ZF_RUN_K6_WORKLOADS", "").strip().lower() in {"1", "true", "yes"}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_health(url: str, process: subprocess.Popen[object]) -> None:
    deadline = time.monotonic() + 30
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"zf web exited before health: rc={process.returncode}")
        try:
            # The development shell may export an HTTP proxy.  Loopback
            # readiness must always reach the owned process directly.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise AssertionError(f"zf web health did not become ready: {last_error}")


def _resource_snapshot(pid: int) -> dict[str, int]:
    """Read bounded Linux process resource counters without extra dependencies."""

    rss_kib = 0
    status = Path(f"/proc/{pid}/status")
    if status.is_file():
        for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("VmRSS:"):
                fields = line.split()
                if len(fields) >= 2 and fields[1].isdigit():
                    rss_kib = int(fields[1])
                break
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        fd_count = sum(1 for _ in fd_dir.iterdir())
    except OSError:
        fd_count = 0
    return {"rss_kib": rss_kib, "fd_count": fd_count}


def _sample_resources(
    pid: int,
    stop: threading.Event,
    samples: list[dict[str, int]],
) -> None:
    while not stop.is_set():
        snapshot = _resource_snapshot(pid)
        snapshot["monotonic_ns"] = time.monotonic_ns()
        samples.append(snapshot)
        stop.wait(5.0)


def _write_project(project: Path) -> Path:
    project.mkdir(parents=True)
    (project / "zf.yaml").write_text(
        "version: '1.0'\n"
        "preset: k6-workload\n"
        "project:\n"
        "  name: zf-k6-workload\n"
        "  state_dir: .zf\n"
        "session:\n"
        "  tmux_session: zf-k6-workload\n"
        "  tmux_layout: pane_grid\n"
        "orchestrator:\n"
        "  backend: python\n"
        "  permission_mode: bypass\n",
        encoding="utf-8",
    )
    return project


@pytest.mark.skipif(not _RUN, reason="set ZF_RUN_K6_WORKLOADS=1 for real k6 workloads")
@pytest.mark.perf
@pytest.mark.serial
@pytest.mark.parametrize("workload", ["pressure", "spike", "stress", "soak"])
def test_real_k6_workload_matrix_against_zf_web(tmp_path: Path, workload: str) -> None:
    """Exercise real ZaoFu HTTP projections with explicit performance budgets."""

    repo_root = Path(__file__).resolve().parents[2]
    project = _write_project(tmp_path / "project")
    receipts = tmp_path / "receipts"
    init = run_command(
        ["uv", "run", "--project", str(repo_root), "zf", "init", "--no-workspace-register", "--no-git-hooks", "--skip-instruction-docs"],
        command_id="init",
        cwd=project,
        output_dir=receipts,
        timeout_seconds=60,
    )
    assert init.passed, init

    port = _free_port()
    web_log = tmp_path / "zf-web.log"
    web_file = web_log.open("w", encoding="utf-8")
    web = subprocess.Popen(
        ["uv", "run", "--project", str(repo_root), "zf", "web", "--host", "127.0.0.1", "--port", str(port), "--state-dir", str(project / ".zf")],
        cwd=project,
        stdout=web_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    output = tmp_path / "k6-out"
    output.mkdir()
    output.chmod(0o777)
    script = repo_root / "tests/testkit/k6_zaofu_workload.js"
    container = f"zf-k6-workload-{uuid.uuid4().hex[:12]}"
    soak_seconds = os.environ.get("ZF_K6_SOAK_SECONDS", "30").strip()
    assert soak_seconds.isdigit() and int(soak_seconds) > 0, soak_seconds
    command_timeout = max(120, int(soak_seconds) + 90) if workload == "soak" else 120
    resource_samples: list[dict[str, int]] = []
    resource_stop = threading.Event()
    resource_thread: threading.Thread | None = None
    try:
        _wait_health(f"http://127.0.0.1:{port}/health", web)
        if workload == "soak":
            resource_thread = threading.Thread(
                target=_sample_resources,
                args=(web.pid, resource_stop, resource_samples),
                daemon=True,
            )
            resource_thread.start()
        command = [
            "docker", "run", "--rm", "--pull=never", "--name", container,
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--network=host", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--mount", f"type=bind,src={script},dst=/scripts/workload.js,readonly",
            "--mount", f"type=bind,src={output},dst=/out",
            "-e", f"ZF_BASE_URL=http://127.0.0.1:{port}",
            "-e", f"ZF_K6_WORKLOAD={workload}",
            "-e", f"ZF_K6_SOAK_SECONDS={soak_seconds}",
            _K6_IMAGE, "run", "--summary-mode=full",
            "--summary-trend-stats=avg,min,med,max,p(90),p(95),p(99),count",
            "--summary-export=/out/summary.json", "/scripts/workload.js",
        ]
        receipt = run_command(command, command_id=f"k6-{workload}", cwd=repo_root, output_dir=receipts, timeout_seconds=command_timeout)
        assert receipt.exit_code == 0, receipt
        summary_path = output / "summary.json"
        assert summary_path.is_file() and summary_path.stat().st_size > 0
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        verdict = evaluate_k6_summary(
            summary,
            (
                PerformanceThreshold("checks", "rate", ">", 0.99),
                PerformanceThreshold("http_req_failed", "rate", "<", 0.05),
                PerformanceThreshold("testkit_requests", "count", ">", 0),
                PerformanceThreshold("testkit_latency", "p(95)", "<", 2000),
                PerformanceThreshold("testkit_latency", "p(99)", "<", 5000),
            ),
            exit_code=receipt.exit_code,
        )
        assert verdict.status == "pass", verdict
        if workload == "soak" and int(soak_seconds) >= 60:
            assert len(resource_samples) >= 2, resource_samples
            first = resource_samples[0]
            peak_rss = max(sample["rss_kib"] for sample in resource_samples)
            peak_fds = max(sample["fd_count"] for sample in resource_samples)
            max_rss_growth_kib = int(
                os.environ.get("ZF_K6_MAX_RSS_GROWTH_MIB", "128")
            ) * 1024
            max_fd_growth = int(os.environ.get("ZF_K6_MAX_FD_GROWTH", "64"))
            assert peak_rss - first["rss_kib"] <= max_rss_growth_kib, resource_samples
            assert peak_fds - first["fd_count"] <= max_fd_growth, resource_samples
        verdict_path = output / f"{workload}.verdict.json"
        write_json_atomic(
            verdict_path,
            {
                "workload": workload,
                "status": verdict.status,
                "checked": list(verdict.checked),
                "command_receipt": evidence_ref(receipts / f"k6-{workload}.receipt.json", root=tmp_path),
                "summary": evidence_ref(summary_path, root=tmp_path),
                "script": {"ref": str(script), "digest": sha256_file(script)},
                "image": _K6_IMAGE,
                "base_url": f"http://127.0.0.1:{port}",
                "resource_samples": resource_samples,
            },
        )
        assert verdict_path.is_file()
    finally:
        resource_stop.set()
        if resource_thread is not None:
            resource_thread.join(timeout=10)
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, text=True, timeout=15, check=False)
        terminate_process_group(web, timeout=5)
        web_file.close()
        assert web.poll() is not None
        assert not list(project.glob("**/*.tmp"))
        # ``*.lock`` files are durable lock identities created by the
        # canonical stores (and may be created lazily by a read projection),
        # not leaked processes or temporary files.
