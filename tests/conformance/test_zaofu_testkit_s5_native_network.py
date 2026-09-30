"""Opt-in real Claude network fault probe; not yet a full CLI candidate chain."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

from tests.testkit.network_fault import FixedTargetTcpRelay
from tests.testkit.runner import _terminate
from zf.core.state.task_attempts import TaskAttemptStore


def relay_provider_settings(base_url: str, endpoint: tuple[str, int]) -> dict:
    """Only process-local endpoint settings; never copy authentication."""
    base = urlsplit(base_url)
    if (base.scheme != "http" or not base.hostname or base.username or base.password
            or base.query or base.fragment):
        raise ValueError("native relay requires an HTTP base URL without credentials/query/fragment")
    url = urlunsplit((base.scheme, f"{endpoint[0]}:{endpoint[1]}", base.path, "", ""))
    return {"env": {"ANTHROPIC_BASE_URL": url, "NO_PROXY": "127.0.0.1,localhost",
                    "no_proxy": "127.0.0.1,localhost"}}


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_real_claude_request_survives_owned_network_cut(record_property):
    if os.environ.get("ZF_RUN_NATIVE_NETWORK") != "1":
        pytest.skip("set ZF_RUN_NATIVE_NETWORK=1 for real network interruption")
    executable = shutil.which("claude")
    assert executable, "Claude CLI required"
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    relay_provider_settings(base_url, ("127.0.0.1", 1))  # Validate before starting anything.
    base = urlsplit(base_url)
    marker = f"ZF_NETWORK_RESTORED_{uuid4().hex[:12]}"
    observation = {"cut": False, "restored": False, "completed": False}
    deadline = time.monotonic() + 240
    with (TemporaryDirectory(prefix="zf-k548-network-20260924-") as directory,
          FixedTargetTcpRelay(base.hostname, base.port or 80, lifetime_seconds=270) as relay):
        root = Path(directory)
        settings = relay_provider_settings(base_url, relay.endpoint)
        # Keep auth in its existing provider-owned location. Extra settings are
        # supplied only to this child, not written into any shared config.
        command = [executable, "--settings", json.dumps(settings), "-p",
                   "--output-format", "json", "--tools", "", "--max-turns", "1",
                   "--session-id", str(uuid4()), "--effort", "low",
                   f"Reply with exactly {marker}. Do not use tools or perform any other action."]
        process = None
        try:
            with (root / "stdout").open("w") as output, (root / "stderr").open("w") as error:
                process = subprocess.Popen(command, cwd=root, env=dict(os.environ),
                                           stdout=output, stderr=error, start_new_session=True)
            while True:
                snapshot = relay.snapshot()
                if any(counts[0] > 0 for counts in snapshot["active_bytes"].values()):
                    observation["before_cut"] = snapshot
                    relay.trip()
                    observation["cut"] = True
                    break
                assert process.poll() is None, "Provider exited without observed relay traffic"
                assert time.monotonic() < deadline, "Provider did not use the isolated relay"
                time.sleep(0.01)
            # A real retry reaching the still-faulted relay proves a failed
            # network interval, rather than a close after the result was done.
            while not any(r.rejected and r.closed_by_fault for r in relay.records):
                assert process.poll() is None, "Provider exited before a network retry"
                assert time.monotonic() < deadline, "No network retry before deadline"
                time.sleep(0.05)
            observation["during_fault"] = relay.snapshot()
            relay.restore()
            observation["restored"] = True
            assert process.wait(timeout=max(1, deadline - time.monotonic())) == 0
            result = json.loads((root / "stdout").read_text())
            assert result.get("type") == "result" and not result.get("is_error")
            assert result["result"].strip() == marker
            assert any(not r.closed_by_fault and not r.rejected and r.bytes_upstream_to_client > 0
                       for r in relay.records), "no successful post-restore response through relay"
            observation.update(completed=True, session_id=result.get("session_id"),
                               result_matches=True, exit_code=process.returncode)
        finally:
            if process is not None and process.poll() is None:
                _terminate(process)
            relay.stop()
            observation.update(snapshot=relay.snapshot(), records=[asdict(r) for r in relay.records],
                               pid=process.pid if process else None,
                               process_exited=process is None or process.poll() is not None)
            # Neither raw stdout/stderr nor the underlying HTTP headers/bodies
            # enter the report. Retain only explicit numeric/identity evidence.
            record_property("native_network", json.dumps(observation))


class NativeNetworkCut:
    """Inject network loss only; retain the existing CLI recovery assertions."""

    def __init__(self, relay, base_url):
        self.relay, self.base_url = relay, base_url
        self.evidence = {}

    def prepare(self, cli, root, monkeypatch):
        bin_dir = root / "network-bin"
        bin_dir.mkdir()
        real_claude, real_tmux = shutil.which("claude"), shutil.which("tmux")
        assert real_claude and real_tmux
        self.tmux, self.socket = real_tmux, root / "network-tmux.sock"
        settings = relay_provider_settings(self.base_url, self.relay.endpoint)
        # No semantic worker replacement: wrappers only prepend CLI options and
        # exec the installed binaries, preserving PID, args and all runtime paths.
        for name, executable, prefix in (
            ("claude", real_claude, []),
            ("tmux", real_tmux, ["-S", str(self.socket), "-f", "/dev/null"]),
        ):
            wrapper = bin_dir / name
            body = f"#!{sys.executable}\nimport os,sys\n"
            if name == "claude":
                body += (f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n"
                         "from tests.testkit.network_fault import merge_claude_network_settings\n"
                         f"args = merge_claude_network_settings(sys.argv[1:], {settings!r})\n")
            else:
                body += f"args = [*{prefix!r}, *sys.argv[1:]]\n"
            wrapper.write_text(body + f"os.execv({executable!r}, [{executable!r}, *args])\n")
            wrapper.chmod(0o700)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
        cli.env["PATH"] = os.environ["PATH"]
        self.evidence.update(private_tmux_socket=str(self.socket), wrappers="options-only execv")

    def interrupt(self, cli, dispatched, deadline):
        operation = dispatched.payload["operation_id"]
        attempt = dispatched.payload["attempt_id"]

        def traffic(events):
            assert not any(e.type == "workflow.operation.settled" and
                           e.payload.get("operation_id") == operation for e in events)
            assert TaskAttemptStore(cli.runtime.state_dir / "task_attempts.json").get(attempt)["status"] == "sent"
            snap = self.relay.snapshot()
            return snap if any(v[0] > 0 for v in snap["active_bytes"].values()) else None

        before = cli.wait(traffic, min(deadline, time.monotonic() + 90))
        self.relay.trip()
        self.evidence.update(before_cut=before, operation_id=operation, attempt_id=attempt,
                             dispatched_event_id=dispatched.id)

        def rejected(events):
            assert not any(e.type == "workflow.operation.settled" and
                           e.payload.get("operation_id") == operation for e in events)
            return any(r.rejected and r.closed_by_fault for r in self.relay.records)

        cli.wait(rejected, min(deadline, time.monotonic() + 90))
        self.evidence["fault_verified"] = self.relay.snapshot()

    def restore(self):
        assert self.relay.snapshot()["faulted"]
        self.evidence["restore_after_connection_id"] = self.relay.snapshot()["accepted"]
        self.relay.restore()

    def assert_recovered(self):
        floor = self.evidence["restore_after_connection_id"]
        records = self.relay.records
        assert any(r.closed_by_fault and not r.rejected and r.bytes_client_to_upstream > 0 for r in records)
        assert any(r.rejected and r.closed_by_fault for r in records)
        assert any(r.connection_id > floor and not r.closed_by_fault and not r.rejected
                   and r.bytes_upstream_to_client > 0 for r in records)
        assert not self.relay.snapshot()["budget_exhausted"]
        self.evidence["recovered_traffic"] = True


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_native_network_cut_cli_recovery_to_candidate(monkeypatch, record_property):
    if os.environ.get("ZF_RUN_NATIVE_NETWORK") != "1":
        pytest.skip("set ZF_RUN_NATIVE_NETWORK=1 for full CLI network recovery")
    from tests.conformance.test_zaofu_testkit_s5_native_cli_lifecycle import _exercise_native_cli_lifecycle

    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    relay_provider_settings(base_url, ("127.0.0.1", 1))
    base = urlsplit(base_url)
    with FixedTargetTcpRelay(base.hostname, base.port or 80) as relay:
        fault = NativeNetworkCut(relay, base_url)
        try:
            _exercise_native_cli_lifecycle(monkeypatch, record_property, stage="impl", network=fault)
        finally:
            relay.stop()
            record_property("native_network_cli", json.dumps({
                **fault.evidence, "snapshot": relay.snapshot(),
                "records": [asdict(r) for r in relay.records],
            }))
