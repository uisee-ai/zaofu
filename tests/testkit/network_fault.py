"""Process-owned, fixed-target TCP fault injector for provider tests.

This helper is intentionally a test seam, not a generic proxy.  It binds only
to loopback, forwards bytes to one fixed target, records no payload or headers,
and can only close connections owned by the test process.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
import json
from pathlib import Path
import select
import socket
import threading
import time
from typing import Final


_BUFFER_SIZE: Final = 64 * 1024


def merge_claude_network_settings(argv: list[str], override: dict) -> list[str]:
    """Keep production hooks while supplying exactly one process-local settings flag."""
    args = list(argv)
    original = {}
    if "--settings" in args:
        if args.count("--settings") != 1:
            raise ValueError("ambiguous original settings flags")
        offset = args.index("--settings")
        value = args[offset + 1]
        original = json.loads(value if value.lstrip().startswith("{") else Path(value).read_text())
        del args[offset:offset + 2]
    merged = {**original, "env": {**original.get("env", {}), **override["env"]}}
    return ["--settings", json.dumps(merged), *args]


@dataclass(frozen=True)
class RelayConnection:
    """Non-sensitive evidence for one relay connection."""

    connection_id: int
    accepted_at: float
    closed_at: float
    bytes_client_to_upstream: int
    bytes_upstream_to_client: int
    closed_by_fault: bool
    rejected: bool
    error: str = ""


class FixedTargetTcpRelay:
    """Forward a client stream to one fixed upstream with controlled cuts."""

    def __init__(
        self,
        upstream_host: str,
        upstream_port: int,
        *,
        max_connections: int = 8,
        max_accepts: int = 512,
        lifetime_seconds: float = 1800,
    ) -> None:
        if not upstream_host or not (1 <= int(upstream_port) <= 65535):
            raise ValueError("a valid fixed upstream host and port are required")
        ip_address(upstream_host)  # No DNS call outside the bounded connect timeout.
        if not 1 <= max_connections <= 32 or not 1 <= max_accepts <= 4096:
            raise ValueError("connection budgets out of bounds")
        if not 0 < lifetime_seconds <= 1800:
            raise ValueError("lifetime must be in (0, 1800]")
        self._upstream = (upstream_host, int(upstream_port))
        self._max_connections = max_connections
        self._max_accepts = max_accepts
        self._lifetime = lifetime_seconds
        self._deadline = 0.0
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._connections: dict[int, tuple[socket.socket, socket.socket]] = {}
        self._threads: set[threading.Thread] = set()
        self._records: list[RelayConnection] = []
        self._next_id = 0
        self._fault_generation = 0
        self._faulted = False
        self._fault_times: list[tuple[str, float, int]] = []
        self._counts: dict[int, list[int]] = {}
        self._stopping = threading.Event()
        self._lock = threading.RLock()

    @property
    def endpoint(self) -> tuple[str, int]:
        listener = self._listener
        if listener is None:
            raise RuntimeError("relay is not started")
        host, port = listener.getsockname()[:2]
        return str(host), int(port)

    @property
    def records(self) -> tuple[RelayConnection, ...]:
        with self._lock:
            return tuple(self._records)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "accepted": self._next_id, "active": len(self._connections),
                "faulted": self._faulted, "generation": self._fault_generation,
                "expired": bool(self._deadline and time.monotonic() >= self._deadline),
                "budget_exhausted": self._next_id >= self._max_accepts,
                "fault_times": list(self._fault_times),
                "active_bytes": {key: list(value) for key, value in self._counts.items()},
            }

    def start(self) -> None:
        if self._listener is not None or self._stopping.is_set():
            raise RuntimeError("relay already started or closed")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(self._max_connections)
        listener.settimeout(0.1)
        self._listener = listener
        self._deadline = time.monotonic() + self._lifetime
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="zf-testkit-relay", daemon=True
        )
        self._accept_thread.start()

    def trip(self) -> None:
        """Cut active connections and reject new ones until ``restore``."""
        with self._lock:
            self._fault_generation += 1
            self._faulted = True
            self._fault_times.append(("trip", time.monotonic(), len(self._connections)))
            sockets = tuple(self._connections.values())
            for pair in sockets:
                self._close_pair(pair)

    def restore(self) -> None:
        with self._lock:
            if self._stopping.is_set():
                raise RuntimeError("relay is stopping")
            self._faulted = False
            self._fault_times.append(("restore", time.monotonic(), len(self._connections)))

    def stop(self) -> None:
        self._stopping.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._lock:
            sockets = tuple(self._connections.values())
        for pair in sockets:
            self._close_pair(pair)
        thread = self._accept_thread
        if thread is not None:
            thread.join(timeout=4)
            assert not thread.is_alive(), "relay accept thread did not exit"
        with self._lock:
            workers = tuple(self._threads)
        for worker in workers:
            worker.join(timeout=2)
            assert not worker.is_alive(), "relay worker did not exit"
        assert not self._connections, "relay left owned sockets alive"
        self._listener = None
        self._accept_thread = None

    def __enter__(self) -> "FixedTargetTcpRelay":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            listener = self._listener
            if listener is None:
                return
            if time.monotonic() >= self._deadline:
                self._stopping.set()
                listener.close()
                with self._lock:
                    for pair in tuple(self._connections.values()):
                        self._close_pair(pair)
                return
            if self._next_id >= self._max_accepts:
                listener.close()
                return
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                if self._stopping.is_set():
                    client.close()
                    return
                self._next_id += 1
                connection_id = self._next_id
                faulted = self._faulted
                if faulted or len(self._connections) >= self._max_connections:
                    self._records.append(RelayConnection(
                        connection_id, time.time(), time.time(), 0, 0,
                        closed_by_fault=faulted, rejected=True,
                    ))
                    client.close()
                    continue
                generation = self._fault_generation
            try:
                upstream = socket.create_connection(self._upstream, timeout=3)
            except OSError:
                client.close()
                with self._lock:
                    self._records.append(RelayConnection(
                        connection_id, time.time(), time.time(), 0, 0, False, True, "connect_failed",
                    ))
                continue
            worker = threading.Thread(
                target=self._relay_loop,
                args=(connection_id, client, upstream, generation),
                name=f"zf-testkit-relay-{connection_id}", daemon=True,
            )
            with self._lock:
                if self._stopping.is_set() or generation != self._fault_generation:
                    self._close_pair((client, upstream))
                    self._records.append(RelayConnection(
                        connection_id, time.time(), time.time(), 0, 0,
                        generation != self._fault_generation, True,
                    ))
                    continue
                self._connections[connection_id] = (client, upstream)
                self._counts[connection_id] = [0, 0]
                self._threads.add(worker)
                worker.start()

    @staticmethod
    def _close_pair(pair: tuple[socket.socket, socket.socket]) -> None:
        for sock in pair:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _relay_loop(
        self,
        connection_id: int,
        client: socket.socket,
        upstream: socket.socket,
        generation: int,
    ) -> None:
        accepted_at = time.time()
        totals = [0, 0]
        error = ""
        try:
            client.settimeout(1)
            upstream.settimeout(1)
            sockets = [client, upstream]
            while not self._stopping.is_set():
                with self._lock:
                    interrupted = self._fault_generation != generation
                if interrupted:
                    break
                readable, _, _ = select.select(sockets, [], [], 0.1)
                if not readable:
                    continue
                for source in readable:
                    destination = upstream if source is client else client
                    data = source.recv(_BUFFER_SIZE)
                    if not data:
                        return
                    index = 0 if source is client else 1
                    destination.sendall(data)
                    totals[index] += len(data)
                    with self._lock:
                        self._counts[connection_id][index] = totals[index]
        except (OSError, ValueError) as exc:
            error = type(exc).__name__  # Never include exception text/payload.
        finally:
            with self._lock:
                self._close_pair((client, upstream))
                cut = self._fault_generation != generation
                self._connections.pop(connection_id, None)
                self._counts.pop(connection_id, None)
                self._threads.discard(threading.current_thread())
                self._records.append(RelayConnection(
                    connection_id, accepted_at, time.time(), totals[0], totals[1],
                    closed_by_fault=cut, rejected=False, error=error if not cut else "",
                ))
