from __future__ import annotations

import socket
import socketserver
import ssl
import subprocess
import sys
from types import SimpleNamespace
import threading
import time
from dataclasses import asdict
import json

import pytest

from tests.testkit.network_fault import FixedTargetTcpRelay
from tests.conformance.test_zaofu_testkit_s5_native_network import NativeNetworkCut, relay_provider_settings


class _Echo(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            while True:
                data = self.request.recv(65536)
                if not data:
                    return
                self.request.sendall(data)
        except OSError:
            return


@pytest.fixture
def echo_server():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Echo)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for relay evidence")
        time.sleep(0.01)


def _exchange(client, data):
    client.sendall(data)
    parts = bytearray()
    while len(parts) < len(data):
        chunk = client.recv(min(65536, len(data) - len(parts)))
        assert chunk, "unexpected EOF"
        parts.extend(chunk)
    assert parts == data


def test_trip_rejects_only_relay_connections_and_restore_allows_new_stream(echo_server) -> None:
    with FixedTargetTcpRelay(*echo_server) as relay, FixedTargetTcpRelay(*echo_server) as control:
        with (socket.create_connection(relay.endpoint, timeout=2) as first,
              socket.create_connection(control.endpoint, timeout=2) as other,
              socket.create_connection(echo_server, timeout=2) as direct):
            _exchange(first, b"Authorization: secret-test-only\r\nbody")
            relay.trip()
            # Restore immediately: a changed generation must kill the old socket
            # even when its worker observes only the already-restored state.
            relay.restore()
            assert first.recv(1) == b""
            _exchange(other, b"other-relay-unaffected")
            _exchange(direct, b"direct-unaffected")
            _wait_for(lambda: any(r.closed_by_fault for r in relay.records))
            with socket.create_connection(relay.endpoint, timeout=2) as second:
                _exchange(second, b"after")
            _wait_for(lambda: len(relay.records) == 2)
        evidence = json.dumps({"snapshot": relay.snapshot(),
                               "records": [asdict(r) for r in relay.records]})
        assert "secret-test-only" not in evidence and "Authorization" not in evidence
        assert relay.records[0].bytes_client_to_upstream > 0
        assert relay.records[0].bytes_upstream_to_client > 0
        assert relay.records[1].bytes_client_to_upstream == 5
        assert not relay.records[1].closed_by_fault


def test_fixed_target_rejects_connections_while_faulted(
    echo_server,
):
    host, port = echo_server
    with FixedTargetTcpRelay(host, port) as relay:
        relay.trip()
        rejected = socket.create_connection(relay.endpoint, timeout=2)
        rejected.settimeout(1)
        assert rejected.recv(1) == b""
        rejected.close()
        relay.restore()
        assert relay.records[-1].rejected
        with pytest.raises(ValueError):
            FixedTargetTcpRelay("127.0.0.1", 0)


def test_relay_connection_limits_and_cleanup(echo_server):
    relay = FixedTargetTcpRelay(*echo_server, max_connections=1, max_accepts=3)
    with relay:
        endpoint = relay.endpoint
        with socket.create_connection(endpoint, timeout=2) as first:
            _exchange(first, b"ready")
            for _ in range(2):
                with socket.create_connection(endpoint, timeout=2) as rejected:
                    assert rejected.recv(1) == b""
            _wait_for(lambda: relay.snapshot()["budget_exhausted"])
            assert len([r for r in relay.records if r.rejected]) == 2
            relay.stop()
            assert first.recv(1) == b""
    assert relay.snapshot()["active"] == 0
    with pytest.raises(OSError):
        socket.create_connection(endpoint, timeout=1)
    with pytest.raises(RuntimeError):
        relay.restore()


def test_relay_large_stream_is_byte_exact(echo_server):
    data = bytes(range(256)) * 2048
    with FixedTargetTcpRelay(*echo_server) as relay:
        with socket.create_connection(relay.endpoint, timeout=3) as client:
            _exchange(client, data)
        _wait_for(lambda: bool(relay.records))
        record = relay.records[0]
        assert record.bytes_client_to_upstream == record.bytes_upstream_to_client == len(data)
        assert not record.error


def test_relay_forwards_verified_tls_without_terminating_it(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
        "-keyout", str(key), "-out", str(cert),
    ], check=True, capture_output=True, timeout=10)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    client_context = ssl.create_default_context(cafile=str(cert))
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Echo)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        with FixedTargetTcpRelay(*server.server_address) as relay:
            raw = socket.create_connection(relay.endpoint, timeout=3)
            with client_context.wrap_socket(raw, server_hostname="localhost") as client:
                _exchange(client, b"tls-secret-test-only")
                relay.trip()
                assert client.recv(1) == b""
            relay.restore()
            raw = socket.create_connection(relay.endpoint, timeout=3)
            with client_context.wrap_socket(raw, server_hostname="localhost") as client:
                _exchange(client, b"restored-tls")
            _wait_for(lambda: len(relay.records) == 2)
            records = {record.connection_id: record for record in relay.records}
            assert records[1].closed_by_fault
            assert not records[2].closed_by_fault
            assert records[1].bytes_client_to_upstream > len(b"tls-secret-test-only")
            assert "tls-secret" not in repr(relay.records)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_cut_during_connect_cannot_escape_through_restore(monkeypatch, echo_server):
    connect = socket.create_connection
    entered, release = threading.Event(), threading.Event()

    def delayed(address, *args, **kwargs):
        if address == echo_server:
            entered.set()
            assert release.wait(2)
        return connect(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", delayed)
    with FixedTargetTcpRelay(*echo_server) as relay:
        with connect(relay.endpoint, timeout=2) as client:
            try:
                assert entered.wait(2)
                relay.trip()
                relay.restore()
            finally:
                release.set()
            assert client.recv(1) == b""
            _wait_for(lambda: bool(relay.records))
            assert relay.records[0].closed_by_fault and relay.records[0].rejected
        with connect(relay.endpoint, timeout=2) as fresh:
            _exchange(fresh, b"new-generation")


def test_relay_lifetime_closes_idle_connections(echo_server):
    with FixedTargetTcpRelay(*echo_server, lifetime_seconds=0.3) as relay:
        with socket.create_connection(relay.endpoint, timeout=2) as client:
            _exchange(client, b"ready")
            assert client.recv(1) == b""
        _wait_for(lambda: relay.snapshot()["active"] == 0)
        assert relay.snapshot()["expired"]


@pytest.mark.parametrize("base", ["https://127.0.0.1:80", "http://user:secret@127.0.0.1:80",
                                 "http://127.0.0.1:80?token=secret", "http://127.0.0.1:80#fragment"])
def test_provider_override_rejects_unsafe_or_unsupported_urls(base):
    with pytest.raises(ValueError):
        relay_provider_settings(base, ("127.0.0.1", 12345))


def test_native_wrapper_keeps_real_argv_and_private_tmux_only(tmp_path, monkeypatch, echo_server):
    installed = tmp_path / "installed"
    installed.write_text(f"#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
    installed.chmod(0o700)
    monkeypatch.setattr("shutil.which", lambda _: str(installed))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:23456")
    with FixedTargetTcpRelay(*echo_server) as relay:
        fault = NativeNetworkCut(relay, "http://127.0.0.1:23456")
        cli = SimpleNamespace(env={})
        fault.prepare(cli, tmp_path, monkeypatch)
        hooks = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]},
                 "env": {"KEEP_TEST_ENV": "preserved"}}
        settings_file = tmp_path / "hooks.json"
        settings_file.write_text(json.dumps(hooks))
        args = ["--resume", "provider-session", "--allowedTools", "Read", "--settings", str(settings_file)]
        output = subprocess.check_output([str(tmp_path / "network-bin/claude"), *args], text=True, timeout=3)
        actual = json.loads(output)
        assert actual.count("--settings") == 1, "duplicate --settings can discard network override"
        offset = actual.index("--settings")
        merged = json.loads(actual[offset + 1])
        assert actual[:offset] + actual[offset + 2:] == args[:-2]
        assert merged["hooks"] == hooks["hooks"]
        assert merged["env"] == {**hooks["env"], **relay_provider_settings(fault.base_url, relay.endpoint)["env"]}
        output = subprocess.check_output([str(tmp_path / "network-bin/claude"), "auth", "status"],
                                         text=True, timeout=3)
        auth_args = json.loads(output)
        assert auth_args[0] == "--settings" and auth_args[2:] == ["auth", "status"]
        output = subprocess.check_output([str(tmp_path / "network-bin/tmux"), "new-session"], text=True, timeout=3)
        assert json.loads(output) == ["-S", str(tmp_path / "network-tmux.sock"), "-f", "/dev/null", "new-session"]
        assert cli.env["PATH"].startswith(str(tmp_path / "network-bin") + ":")
        import os
        assert os.environ["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:23456"


@pytest.mark.parametrize("missing", [None, "cut", "rejected", "response"])
def test_native_recovery_requires_cut_rejection_and_fresh_response(missing):
    rows = {
        "cut": SimpleNamespace(connection_id=1, closed_by_fault=True, rejected=False,
                               bytes_client_to_upstream=20, bytes_upstream_to_client=0),
        "rejected": SimpleNamespace(connection_id=2, closed_by_fault=True, rejected=True,
                                    bytes_client_to_upstream=0, bytes_upstream_to_client=0),
        "response": SimpleNamespace(connection_id=3, closed_by_fault=False, rejected=False,
                                    bytes_client_to_upstream=20, bytes_upstream_to_client=10),
    }
    relay = SimpleNamespace(records=[v for k, v in rows.items() if k != missing],
                            snapshot=lambda: {"budget_exhausted": False})
    fault = NativeNetworkCut(relay, "http://127.0.0.1:23456")
    fault.evidence["restore_after_connection_id"] = 2
    if missing is None:
        fault.assert_recovered()
    else:
        with pytest.raises(AssertionError):
            fault.assert_recovered()
