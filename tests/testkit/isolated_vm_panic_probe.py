"""Capability probe for a private guest-kernel panic path.

This is an explicit host-tier probe, not a ZaoFu recovery acceptance test.  It
boots a disposable Ubuntu image, asks the guest to trigger ``sysrq-trigger``
and records whether QEMU's ``pvpanic`` device reports the panic before the
owned emulator exits.  No host sysrq/reboot operation is ever used.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
from uuid import UUID, uuid4

import yaml

from tests.testkit.evidence import sha256_file, write_json_atomic
from tests.testkit.isolated_vm_run import BASE_SHA, NETWORK_CONFIG, resource_guard, markers
from tests.testkit.isolated_vm_supervisor import OwnedProcessSupervisor


def _tool(root: Path, name: str, *args: object) -> list[str]:
    libs = root / "root/usr/lib/x86_64-linux-gnu"
    return [
        "/lib64/ld-linux-x86-64.so.2",
        "--library-path",
        str(libs),
        str(root / "root/usr/bin" / name),
        *(str(arg) for arg in args),
    ]


def _seed_user_data(nonce: str) -> str:
    # Keep the fault injection entirely inside the disposable guest.  The
    # serial markers are observation only; the panic itself is the kernel
    # response to the guest's sysrq control file.
    program = f'''
import json, pathlib
assert pathlib.Path('/sys/class/dmi/id/product_uuid').read_text().strip().replace('-', '').lower() == {nonce!r}
assert sorted(p.name for p in pathlib.Path('/sys/class/net').iterdir()) == ['lo']
assert pathlib.Path('/sys/kernel/kexec_crash_loaded').read_text().strip() == '0'
boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
assert boot
pathlib.Path('/proc/sys/kernel/panic').write_text('0')
print('ZF_K590_PANIC_ARMED ' + json.dumps({{'nonce': {nonce!r}, 'boot_id': boot, 'interfaces': ['lo'], 'panic_timeout': 0, 'crash_loaded': False}}), flush=True)
pathlib.Path('/proc/sysrq-trigger').write_text('c')
raise RuntimeError('sysrq crash unexpectedly returned')
'''
    config = {
        "package_update": False,
        "package_upgrade": False,
        "users": [],
        "ssh_genkeytypes": [],
        "disable_root": True,
        "bootcmd": [
            [
                "sh",
                "-c",
                'exec python3 -c "$1" > /dev/ttyS0 2>&1',
                "k590-guest-only",
                program,
            ]
        ],
    }
    return "#cloud-config\n" + yaml.safe_dump(config, sort_keys=True)


class _QmpRecorder:
    def __init__(self, path: Path, output: Path) -> None:
        self.path = path
        self.output = output
        self.lines: list[str] = []
        self.connection: socket.socket | None = None
        self.buffer = b""
        self.sent_capabilities = False

    def start(self) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(0.1)
            try:
                connection.connect(str(self.path))
                self.connection = connection
                return
            except (FileNotFoundError, ConnectionRefusedError):
                connection.close()
                time.sleep(0.05)
            except BaseException:
                connection.close()
                raise
        raise TimeoutError("private QMP connection unavailable")

    def stop(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.output.write_text("".join(self.lines), encoding="utf-8")

    def poll(self) -> bool:
        assert self.connection is not None
        try:
            chunk = self.connection.recv(4096)
        except socket.timeout:
            return True
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if not line.strip():
                continue
            row = json.loads(line)
            self.lines.append(line.decode("utf-8") + "\n")
            if "QMP" in row and not self.sent_capabilities:
                self.connection.sendall(b'{"execute":"qmp_capabilities","id":"caps"}\r\n')
                self.sent_capabilities = True
        return bool(chunk)


def panic_evidence(serial: str, qmp: str, nonce: str) -> bool:
    identities = markers(serial, "ZF_K590_PANIC_ARMED ")
    if len(identities) != 1:
        return False
    identity = identities[0]
    try:
        rows = [json.loads(line) for line in qmp.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return False
    if not all(isinstance(row, dict) for row in rows):
        return False
    return (
        identity.get("nonce") == nonce and bool(identity.get("boot_id"))
        and identity.get("interfaces") == ["lo"]
        and identity.get("panic_timeout") == 0 and identity.get("crash_loaded") is False
        and "Kernel panic - not syncing: sysrq triggered crash" in serial
        and any("QMP" in row for row in rows)
        and any(row.get("id") == "caps" and row.get("return") == {} for row in rows)
        and sum(row.get("event") == "GUEST_PANICKED" for row in rows) == 1
        and any(row.get("event") == "SHUTDOWN" and
                row.get("data", {}).get("reason") == "guest-panic" for row in rows)
        and not any("error" in row for row in rows)
    )


def run_panic_probe(root: Path, tools: Path) -> dict:
    """Run one disposable panic probe and return a fail-closed report."""

    root = Path(root).absolute()
    tools = Path(tools).resolve()
    if root.parent != Path("/tmp") or not root.name.startswith("zf-k590-panic-"):
        raise ValueError("panic probe root must be a unique /tmp/zf-k590-panic-* directory")
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    resource_guard(root)
    base = tools / "image/ubuntu-24.04-minimal-cloudimg-amd64.img"
    firmware = tools / "root/usr/share/OVMF/OVMF_CODE_4M.fd"
    vars_source = tools / "root/usr/share/OVMF/OVMF_VARS_4M.fd"
    if sha256_file(base) != BASE_SHA:
        raise ValueError("unexpected base image digest")
    for path in (firmware, vars_source, tools / "root/usr/bin/qemu-system-x86_64"):
        if not path.is_file():
            raise FileNotFoundError(path)

    nonce = uuid4().hex
    overlay = root / "overlay.qcow2"
    variables = root / "OVMF_VARS.fd"
    seed_dir = root / "seed"
    seed_dir.mkdir(mode=0o700)
    (seed_dir / "meta-data").write_text(
        f"instance-id: {nonce}\nlocal-hostname: zf-k590-panic\n", encoding="utf-8"
    )
    (seed_dir / "network-config").write_text(
        yaml.safe_dump(NETWORK_CONFIG), encoding="utf-8"
    )
    (seed_dir / "user-data").write_text(_seed_user_data(nonce), encoding="utf-8")
    shutil.copyfile(vars_source, variables)
    subprocess.run(
        _tool(tools, "qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", base, overlay),
        check=True,
        capture_output=True,
        timeout=15,
    )
    seed_iso = root / "seed.iso"
    subprocess.run(
        _tool(tools, "genisoimage", "-quiet", "-output", seed_iso, "-volid", "CIDATA",
              "-joliet", "-rock", seed_dir),
        check=True,
        timeout=30,
    )
    qmp_path = root / "qmp.sock"
    serial_path = root / "serial.log"
    qmp_log = root / "qmp.jsonl"
    command = _tool(
        tools,
        "qemu-system-x86_64",
        "-machine", "q35", "-accel", "kvm", "-cpu", "host", "-smp", "1", "-m", "1024",
        "-nodefaults", "-display", "none", "-vga", "none", "-monitor", "none", "-nic", "none",
        "-L", tools / "root/usr/share/qemu", "-serial", f"file:{serial_path}",
        "-qmp", f"unix:{qmp_path},server=on,wait=off",
        "-uuid", str(UUID(hex=nonce)),
        "-device", "pvpanic,events=1", "-action", "panic=shutdown",
        "-drive", f"if=pflash,format=raw,readonly=on,file={firmware}",
        "-drive", f"if=pflash,format=raw,file={variables}",
        "-drive", f"if=virtio,format=qcow2,cache=none,file={overlay}",
        "-drive", f"if=virtio,format=raw,readonly=on,file={seed_iso}",
        "-sandbox", "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny",
    )
    supervisor = OwnedProcessSupervisor(root / "owned", command, cleanup_timeout=5)
    qmp = _QmpRecorder(qmp_path, qmp_log)
    report: dict[str, object] = {
        "schema_version": "zf-testkit-k590-panic-probe.v1",
        "fault_model": "guest-kernel-panic-sysrq-pvpanic",
        "verdict": "harness_failure",
        "nonce": nonce,
        "base_sha256": BASE_SHA,
        "qemu_device": "pvpanic,events=1",
        "panic_action": "shutdown",
        "qemu_binary_sha256": sha256_file(tools / "root/usr/bin/qemu-system-x86_64"),
        "scope": "capability-only; no ZaoFu Run or recovery",
        "probe_source_sha256": sha256_file(Path(__file__)),
        "samples": [],
    }
    started = time.monotonic()
    process = None
    previous = {sig: signal.signal(sig, supervisor.note_signal)
                for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        process = supervisor.start()
        qmp.start()
        deadline = started + 180
        last_sample = 0.0
        while process.poll() is None and time.monotonic() < deadline:
            if supervisor.stopping:
                raise InterruptedError(f"probe interrupted: {supervisor.stopping}")
            qmp.poll()
            if time.monotonic() - last_sample >= 1 and process.poll() is None:
                report["samples"].append(resource_guard(root, process.pid))
                last_sample = time.monotonic()
        if process.poll() is not None:
            drain_deadline = time.monotonic() + 2
            while time.monotonic() < drain_deadline and qmp.poll():
                pass
        serial = serial_path.read_text(encoding="utf-8", errors="replace") if serial_path.exists() else ""
        report.update(
            returncode=process.poll(),
            serial_sha256=sha256_file(serial_path) if serial_path.exists() else None,
            panic_armed=len(markers(serial, "ZF_K590_PANIC_ARMED ")) == 1,
            kernel_panic=("Kernel panic - not syncing: sysrq triggered crash" in serial),
            qmp_path=str(qmp_log),
            elapsed_seconds=time.monotonic() - started,
        )
        if process.poll() is None:
            report["blocker"] = "guest did not terminate within 180s after panic trigger"
        elif process.returncode == 0 and panic_evidence(serial, "".join(qmp.lines), nonce):
            report["verdict"] = "pass"
        else:
            report["blocker"] = "guest identity, kernel panic or QMP handshake/panic/shutdown evidence missing"
    except BaseException as exc:
        report["error"] = repr(exc)
        raise
    finally:
        try:
            if process is not None and supervisor.process is not None:
                report["cleanup"] = supervisor.cleanup(reason="probe_finished")
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            qmp.stop()
        report["cleanup_ok"] = bool(report.get("cleanup", {}).get("cleanup_ok"))
        report["qmp_sha256"] = sha256_file(qmp_log)
        report["base_unchanged"] = sha256_file(base) == BASE_SHA
        if not report["cleanup_ok"] or not report["base_unchanged"]:
            report["verdict"] = "harness_failure"
        report["elapsed_seconds"] = time.monotonic() - started
        write_json_atomic(root / "panic-probe.json", report)
    return report


__all__ = ["run_panic_probe"]
