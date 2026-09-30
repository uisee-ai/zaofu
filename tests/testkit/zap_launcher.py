"""Opt-in owned-target ZAP AF launcher, with deterministic executor injection.

Only a fixed read-only profile is implemented. No free target URL, remote
daemon, host-network fallback, automatic image pull, or production state mount.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import ipaddress
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable
from uuid import uuid4

from .command_runner import CommandReceipt, run_command
from .evidence import evidence_ref, sha256_file, write_json_atomic
from .runner import _git_identity
from .zap_protocol import PATHS, PORT, access_counts, checked_origin, fixed_plan, owned_report_verdict, read_regular, strict_json
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


REPO = Path(__file__).resolve().parents[2]
SOCKET = "unix:///var/run/docker.sock"
LABEL = "zf.testkit.run"
IMAGE = re.compile(r"[a-z0-9][a-z0-9./_-]*(?::[A-Za-z0-9_.-]+)?@sha256:[0-9a-f]{64}\Z")
OBJECT_ID = re.compile(r"[0-9a-f]{64}\Z")
READY_SCRIPT = '''import json, sys, time, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
deadline = time.monotonic() + 20
while True:
    try:
        for path in ("/health", "/api/state"):
            with opener.open("http://127.0.0.1:8002" + path, timeout=2) as response:
                assert response.status == 200
                body = json.load(response)
                if path == "/api/state":
                    assert any(task.get("id") == sys.argv[1] for task in body["tasks"])
        print(json.dumps({"task_id": sys.argv[1], "paths": ["/health", "/api/state"]}))
        break
    except Exception:
        if time.monotonic() >= deadline:
            raise
        time.sleep(0.1)
'''


def seed_target(root: Path, run_id: str) -> None:
    """Build synthetic canonical state through existing owners, never a dev root."""
    write_json_atomic(root / "zf.yaml", {
        "project": {"name": "zap-fixture", "state_dir": "runtime-state"},
        "roles": [], "workflow": {"dag": {"stage_order": ["tasks"]}},
    })
    state = root / "runtime-state"
    task = TaskStore(state / "kanban.json").add(Task(id=f"TASK-ZAP-{run_id}", title="Owned scan fixture"))
    EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(type="task.created", task_id=task.id, actor="testkit"))
    (root / "workspace").mkdir()
    (root / "empty-ui").mkdir()


def source_identity() -> dict[str, str]:
    paths = [REPO / "tests/testkit/browser_app.py", *sorted((REPO / "src").rglob("*"))]
    result = {}
    for path in paths:
        if "__pycache__" in path.parts:
            continue
        if path.is_symlink():
            raise ValueError("scan source mount refuses symlinks")
        if path.is_file():
            result[path.relative_to(REPO).as_posix()] = sha256_file(path)
    return result


class _Docker:
    def __init__(self, output: Path, run_id: str, executor: Callable[..., CommandReceipt]) -> None:
        self.output, self.run_id, self.executor = output, run_id, executor
        self.receipts: list[dict[str, Any]] = []
        # Intent precedes create; a lost create reply must not orphan resources.
        self.intents: dict[str, dict[str, str]] = {"container": {}, "network": {}}

    def call(self, args: list[str], *, timeout: float = 15, required: bool = True) -> CommandReceipt:
        receipt = self.executor(
            ["docker", "--host", SOCKET, "--config", str(self.output / "docker-config"), *args],
            command_id=f"docker-{len(self.receipts):03d}", cwd=self.output,
            output_dir=self.output / "commands", timeout_seconds=timeout,
            env={"DOCKER_HOST": SOCKET, "DOCKER_CONTEXT": "", "DOCKER_TLS_VERIFY": "",
                 "DOCKER_CERT_PATH": "", "DOCKER_API_VERSION": "", "NO_PROXY": "*"},
        )
        self.receipts.append(asdict(receipt))
        if required and (not receipt.passed or receipt.environment_error):
            raise ValueError(f"{args[0]} {args[1]} failed: exit={receipt.exit_code}, timeout={receipt.timed_out}, {receipt.error}")
        return receipt

    def json(self, args: list[str]) -> Any:
        return strict_json(read_regular(Path(self.call(args).stdout_ref)))

    def inspect(self, kind: str, identity: str) -> dict[str, Any]:
        rows = self.json([kind, "inspect", identity])
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError(f"invalid {kind} inspect receipt")
        return rows[0]

    def owned(self, kind: str, identity: str, name: str) -> dict[str, Any]:
        row = self.inspect(kind, identity)
        labels = row.get("Config", {}).get("Labels", {}) if kind == "container" else row.get("Labels", {})
        if row.get("Id") != identity or row.get("Name", "").lstrip("/") != name or labels.get(LABEL) != self.run_id:
            raise ValueError(f"{kind} ownership mismatch")
        return row

    def create(self, kind: str, name: str, args: list[str]) -> str:
        self.intents[kind][name] = ""
        write_json_atomic(self.output / "ownership.json", {"run_id": self.run_id, "resources": self.intents})
        identity = read_regular(Path(self.call([kind, "create", *args]).stdout_ref)).strip()
        if not OBJECT_ID.fullmatch(identity):
            raise ValueError("create returned invalid resource identity")
        self.intents[kind][name] = identity
        write_json_atomic(self.output / "ownership.json", {"run_id": self.run_id, "resources": self.intents})
        self.owned(kind, identity, name)
        return identity

    def list_owned(self, kind: str) -> list[dict[str, Any]]:
        args = [kind, "ls", "--no-trunc", "--filter", f"label={LABEL}={self.run_id}", "--format", "{{json .}}"]
        if kind == "container":
            args.append("--all")
        receipt = self.call(args)
        text = Path(receipt.stdout_ref).read_text(encoding="utf-8")
        if len(text) > 1024 * 1024:
            raise ValueError("oversized resource listing")
        return [strict_json(line) for line in text.splitlines() if line.strip()]

    def cleanup(self) -> list[str]:
        errors = []
        for kind in ("container", "network"):
            if not self.intents[kind]:
                continue
            try:
                rows = self.list_owned(kind)  # Empty output with exit 0 is meaningful; daemon errors are not.
            except Exception as error:
                errors.append(f"list {kind}: {error}")
                continue
            for row in reversed(rows):
                try:
                    name = row.get("Names") if kind == "container" else row.get("Name")
                    identity = row.get("ID", "")
                    if name not in self.intents[kind] or not OBJECT_ID.fullmatch(identity):
                        raise ValueError("unexpected resource in owned listing; not deleting")
                    expected_id = self.intents[kind][name]
                    if expected_id and identity != expected_id:
                        raise ValueError("created resource identity changed; not deleting")
                    owned = self.owned(kind, identity, name)
                    if kind == "container":
                        self.call([kind, "logs", identity], required=False, timeout=5)
                        if owned.get("State", {}).get("Running"):
                            self.call([kind, "stop", "--time", "3", identity], required=False, timeout=5)
                        self.call([kind, "rm", "--force", identity], timeout=5)
                    else:
                        self.call([kind, "rm", identity], timeout=5)
                except Exception as error:
                    errors.append(f"cleanup {kind}: {error}")
            try:
                if self.list_owned(kind):
                    errors.append(f"owned {kind} resources remain")
            except Exception as error:
                errors.append(f"verify cleanup {kind}: {error}")
        return errors


def _network(docker: _Docker, identity: str, name: str, allowed: set[str]) -> None:
    row = docker.owned("network", identity, name)
    opts = row.get("Options", {})
    if row.get("Driver") != "bridge" or row.get("Internal") is not True or any(
        opts.get(f"com.docker.network.bridge.gateway_mode_{version}") != "isolated" for version in ("ipv4", "ipv6")
    ) or not set(row.get("Containers", {})) <= allowed:
        raise ValueError("network is not isolated or contains foreign members")


def _container(docker: _Docker, identity: str, name: str, network: str) -> dict[str, Any]:
    row = docker.owned("container", identity, name)
    host = row.get("HostConfig", {})
    if host.get("NetworkMode") != network or host.get("PortBindings") or host.get("Privileged") or host.get("ReadonlyRootfs") is not True:
        raise ValueError("unsafe inspected container configuration")
    networks = row.get("NetworkSettings", {}).get("Networks", {})
    if len(networks) != 1 or next(iter(networks.values())).get("NetworkID") != network:
        raise ValueError("container has an unexpected network attachment")
    return row


def _common(name: str, run_id: str, network: str) -> list[str]:
    return ["--name", name, "--label", f"{LABEL}={run_id}", "--network", network,
            "--pull=never", "--user", f"{os.getuid()}:{os.getgid()}", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=256m",
            "--memory=2g", "--cpus=2", "--pids-limit=256", "--dns", "127.0.0.1",
            "--env", "HTTP_PROXY=", "--env", "HTTPS_PROXY=", "--env", "ALL_PROXY=",
            "--env", "http_proxy=", "--env", "https_proxy=", "--env", "all_proxy=", "--env", "NO_PROXY=*"]


def _mount(source: Path, destination: str, *, readonly: bool = True) -> list[str]:
    if any(character in str(source) for character in (",", "\n", "\r")):
        raise ValueError("unsupported mount path")
    return ["--mount", f"type=bind,src={source},dst={destination}" + (",readonly" if readonly else "")]


def run_zap(*, output_dir: Path, zap_image: str, app_image: str, timeout_seconds: float = 180,
            max_risk: int = 1, executor: Callable[..., CommandReceipt] | None = None) -> dict[str, Any]:
    """Run against a newly created target; injected executors always say fake."""
    output = output_dir.absolute()
    if output.is_symlink() or output.exists() or output.resolve() != output or not output.is_relative_to(Path(tempfile.gettempdir())):
        raise ValueError("ZAP output must be a new non-symlink path under the temporary root")
    output.mkdir(parents=True, mode=0o700)
    run_id = uuid4().hex
    docker = _Docker(output, run_id, executor or run_command)
    result: dict[str, Any] = {
        "schema_version": "zf-testkit-zap-run.v1", "run_id": run_id,
        "execution_kind": "fake" if executor is not None else "docker-zap",
        "profile": "fixed-get-passive", "scope": list(PATHS), "status": "harness_failure",
        "started_at": datetime.now(timezone.utc).isoformat(), "max_risk": max_risk,
    }
    fatal = None
    try:
        if not IMAGE.fullmatch(zap_image) or not zap_image.startswith("ghcr.io/zaproxy/zaproxy:") or not IMAGE.fullmatch(app_image):
            raise ValueError("scanner and target require explicit digest-pinned images")
        if isinstance(max_risk, bool) or not isinstance(max_risk, int) or max_risk not in range(4):
            raise ValueError("invalid risk budget")
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 600:
            raise ValueError("invalid bounded scan timeout")
        if os.getuid() == 0 or os.environ.get("DOCKER_HOST", SOCKET) != SOCKET or os.environ.get("DOCKER_CONTEXT"):
            raise ValueError("requires non-root user and the declared local Docker daemon")
        head, dirty = _git_identity(REPO)
        result.update(git_head=head, git_dirty=dirty, source_digests=source_identity())
        result["harness_digests"] = {name: sha256_file(Path(__file__).parent / name) for name in
                                     ("zap_launcher.py", "zap_protocol.py", "zap.py", "command_runner.py")}
        write_json_atomic(output / "docker-config/config.json", {})
        info = docker.json(["info", "--format", "{{json .}}"])
        if int(str(info.get("ServerVersion", "0")).split(".")[0]) < 28:
            raise ValueError("isolated gateway mode requires Docker Engine 28+")
        result["images"] = {}
        for label, image in (("app", app_image), ("zap", zap_image)):
            row = docker.inspect("image", image)
            repository, digest = image.split("@")
            expected = repository.split(":")[0] + "@" + digest
            if expected not in row.get("RepoDigests", []) or not re.fullmatch(r"sha256:[0-9a-f]{64}", row.get("Id", "")) or row.get("Os") != "linux":
                raise ValueError("local image identity does not match pinned digest/platform")
            result["images"][label] = row
        fixture = output / "fixture"
        seed_target(fixture, run_id)
        result["config"] = evidence_ref(fixture / "zf.yaml")
        net_name, app_name, zap_name = (f"zf-zap-{run_id}-{suffix}" for suffix in ("net", "app", "scanner"))
        network = docker.create("network", net_name, ["--driver", "bridge", "--internal", "--label", f"{LABEL}={run_id}",
            "--opt", "com.docker.network.bridge.gateway_mode_ipv4=isolated",
            "--opt", "com.docker.network.bridge.gateway_mode_ipv6=isolated", net_name])
        _network(docker, network, net_name, set())
        app_args = _common(app_name, run_id, network)
        app_args += _mount(REPO / "src", "/zf-code/src") + _mount(REPO / "tests/testkit/browser_app.py", "/zf-code/browser_app.py")
        app_args += _mount(fixture, "/zf-fixture", readonly=False)
        app_args += ["--workdir", "/zf-code", "--env", "PYTHONPATH=/zf-code/src:/zf-code", "--env", "PYTHONDONTWRITEBYTECODE=1",
                     "--env", "ZF_TESTKIT_BROWSER_ROOT=/zf-fixture", "--env", "ZF_TESTKIT_BROWSER_DIST=/zf-fixture/empty-ui",
                     "--env", "ZF_WORKSPACE_HOME=/zf-fixture/workspace", "--env", f"ZF_WEB_ACTION_TOKEN={run_id}",
                     "--entrypoint", "python", app_image, "-m", "uvicorn", "browser_app:create_app", "--factory", "--host", "0.0.0.0", "--port", str(PORT)]
        app = docker.create("container", app_name, app_args)
        docker.call(["container", "start", app])
        app_info = _container(docker, app, app_name, network)
        address = next(iter(app_info["NetworkSettings"]["Networks"].values()))["IPAddress"]
        origin = checked_origin(f"http://{ipaddress.IPv4Address(address)}:{PORT}")
        result.update(origin=origin, target=app_info, network_id=network)
        ready = docker.call(["container", "exec", app, "python", "-c", READY_SCRIPT, f"TASK-ZAP-{run_id}"], timeout=25)
        if strict_json(read_regular(Path(ready.stdout_ref))) != {"task_id": f"TASK-ZAP-{run_id}", "paths": list(PATHS)}:
            raise ValueError("readiness nonce or mandatory endpoints do not match")
        before_logs = read_regular(Path(docker.call(["container", "logs", app]).stdout_ref))
        before_counts = access_counts(before_logs)
        canonical = {name: sha256_file(fixture / "runtime-state" / name) for name in ("kanban.json", "events.jsonl")}
        plan_path = output / "input/plan.yaml"
        plan_digest = write_json_atomic(plan_path, fixed_plan(origin, run_id))  # JSON is a YAML subset.
        raw = output / "raw"
        raw.mkdir()
        report_path = raw / f"zap-{run_id}.json"
        result["plan"] = evidence_ref(plan_path)
        zap_args = _common(zap_name, run_id, network) + _mount(plan_path.parent, "/zap/input") + _mount(raw, "/zap/wrk", readonly=False)
        zap_args += ["--entrypoint", "zap.sh", zap_image, "-cmd", "-silent", "-dir", f"/tmp/zap-home-{run_id}", "-autorun", "/zap/input/plan.yaml"]
        scanner = docker.create("container", zap_name, zap_args)
        _network(docker, network, net_name, {app, scanner})
        scan = docker.call(["container", "start", "--attach", scanner], timeout=timeout_seconds, required=False)
        result["scan"] = asdict(scan)
        state = _container(docker, scanner, zap_name, network)["State"]
        if not scan.passed or scan.environment_error or state.get("Running") is not False or state.get("OOMKilled") is not False or state.get("ExitCode") != 0:
            raise ValueError(f"AF execution incomplete: exit={scan.exit_code}, timed_out={scan.timed_out}, state={state}")
        _network(docker, network, net_name, {app, scanner})
        if sha256_file(plan_path) != plan_digest or source_identity() != result["source_digests"]:
            raise ValueError("plan or tested source changed during scan")
        after_counts = access_counts(read_regular(Path(docker.call(["container", "logs", app]).stdout_ref)))
        if any(after_counts[path] - before_counts[path] != 1 for path in PATHS):
            raise ValueError("mandatory single GET requests were not observed in target access logs")
        if canonical != {name: sha256_file(fixture / "runtime-state" / name) for name in canonical}:
            raise ValueError("read-only scan mutated canonical fixture state")
        result["coverage_proof"] = {"method": "fixed AF URL tests + zero exit + target access log delta",
                                    "requests": {path: 1 for path in PATHS}, "passive_completion": "fixed plan ordering, not sampled queue size"}
        verdict = owned_report_verdict(report_path, origin=origin, max_risk=max_risk)
        result.update(status=verdict.status, reason=verdict.reason, violations=list(verdict.violations))
        if report_path.is_file() and not report_path.is_symlink():
            result["report"] = evidence_ref(report_path)
    except BaseException as error:
        result.update(status="harness_failure", reason=f"{type(error).__name__}: {error}")
        if not isinstance(error, Exception):
            fatal = error
    finally:
        # Persist the original outcome before cleanup; failed evidence writes
        # must never bypass resource cleanup or turn the outcome green.
        try:
            write_json_atomic(output / "scan-result.json", result | {"ownership": docker.intents, "commands": docker.receipts})
        except Exception as error:
            result.update(status="harness_failure", evidence_error=f"{type(error).__name__}: {error}")
        errors = docker.cleanup()
        result.update(cleanup_ok=not errors, cleanup_errors=errors, commands=docker.receipts,
                      ownership=docker.intents, finished_at=datetime.now(timezone.utc).isoformat())
        if errors:
            result["scan_status_before_cleanup"] = result["status"]
            result["status"] = "harness_failure"
        write_json_atomic(output / "zap-run.json", result)
    if fatal is not None:
        raise fatal
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicit owned-fixture ZAP passive tier (never a production URL)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zap-image", required=True)
    parser.add_argument("--app-image", required=True)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--max-risk", type=int, default=1)
    args = parser.parse_args()
    result = run_zap(output_dir=args.output, zap_image=args.zap_image, app_image=args.app_image,
                     timeout_seconds=args.timeout, max_risk=args.max_risk)
    print(f"{result['status']}: {result.get('reason', '')}; {args.output / 'zap-run.json'}")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
