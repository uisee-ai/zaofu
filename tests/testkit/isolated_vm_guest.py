"""Minimal first-boot/restore launcher for the K571 isolated guest.

The launcher owns only test-observation markers.  ZaoFu canonical state is
created and recovered by the command supplied by the caller (normally the
real CLI), never by this module.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Mapping, Sequence


SCHEMA = "zf-testkit-k571-guest.v1"


def _boot_id() -> str:
    value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("guest boot_id is empty")
    return value


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with temporary.open("rb") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"guest marker must be an object: {path}")
    return value


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class GuestResult:
    mode: str
    returncode: int
    report: dict


def run_guest(
    *,
    mode: str,
    root: Path,
    nonce: str,
    command: Sequence[str],
    extra_env: Mapping[str, str] | None = None,
) -> GuestResult:
    """Run one explicit firstboot or restore command.

    The state marker is a test sidecar under ``root``.  In restore mode the
    marker proves that the same guest identity completed firstboot and that a
    new kernel boot was observed.  A control-plane-only command is rejected so
    the recovery path cannot silently skip the real startup reconciler.
    """
    if mode not in {"firstboot", "restore"}:
        raise ValueError("mode must be firstboot or restore")
    root = Path(root).absolute()
    marker_dir = root / "guest-observation"
    state_path = marker_dir / "state.json"
    report_path = marker_dir / f"{mode}-report.json"
    if not root.is_dir():
        raise ValueError(f"guest root must already exist: {root}")
    if not nonce or not command or any(not isinstance(part, str) or not part for part in command):
        raise ValueError("nonce and command are required")
    if not Path(command[0]).is_absolute():
        raise ValueError("guest command executable must be an absolute path")
    if mode == "restore" and "--control-plane-only" in command:
        raise ValueError("restore requires the full startup command")
    if not _under(marker_dir, root):
        raise ValueError("guest observation must be under guest root")

    current_boot = _boot_id()
    previous = _read_json(state_path) if state_path.exists() else None
    if mode == "firstboot":
        if previous is not None:
            raise RuntimeError("firstboot marker already exists; refusing bootstrap replay")
        state = {
            "schema_version": SCHEMA,
            "nonce": nonce,
            "first_boot_id": current_boot,
            "phase": "firstboot_started",
            "started_at_ns": time.time_ns(),
        }
    else:
        if previous is None or previous.get("schema_version") != SCHEMA:
            raise RuntimeError("restore requires a valid firstboot marker")
        if previous.get("nonce") != nonce:
            raise RuntimeError("restore nonce does not match firstboot")
        if previous.get("phase") != "firstboot_complete":
            raise RuntimeError("restore requires completed firstboot")
        if previous.get("first_boot_id") == current_boot:
            raise RuntimeError("restore requires a new guest boot_id")
        state = {
            **previous,
            "phase": "restore_started",
            "restore_boot_id": current_boot,
            "restore_started_at_ns": time.time_ns(),
        }
    _write_json(state_path, state)
    print(json.dumps({"zf_k571_guest": "started", "mode": mode, "nonce": nonce}, sort_keys=True), flush=True)

    environment = os.environ.copy()
    environment.update(extra_env or {})
    environment.update({"ZF_K571_GUEST_MODE": mode, "ZF_K571_GUEST_NONCE": nonce})
    started_ns = time.time_ns()
    completed = subprocess.run(
        list(command), cwd=root, env=environment, check=False,
    )
    report = {
        "schema_version": SCHEMA,
        "mode": mode,
        "nonce": nonce,
        "boot_id": current_boot,
        "command": list(command),
        "command_sha256": hashlib.sha256("\0".join(command).encode()).hexdigest(),
        "returncode": completed.returncode,
        "started_at_ns": started_ns,
        "finished_at_ns": time.time_ns(),
    }
    final_phase = "firstboot_complete" if mode == "firstboot" and completed.returncode == 0 else (
        "restore_complete" if mode == "restore" and completed.returncode == 0 else f"{mode}_failed"
    )
    _write_json(state_path, {**state, "phase": final_phase, "last_report": str(report_path)})
    _write_json(report_path, report)
    print(json.dumps({"zf_k571_guest": "finished", **report}, sort_keys=True), flush=True)
    return GuestResult(mode=mode, returncode=completed.returncode, report=report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("firstboot", "restore"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    environment = {}
    for item in args.env:
        if "=" not in item or not item.split("=", 1)[0]:
            parser.error(f"invalid --env: {item!r}")
        key, value = item.split("=", 1)
        environment[key] = value
    result = run_guest(mode=args.mode, root=args.root, nonce=args.nonce,
                       command=command, extra_env=environment)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
