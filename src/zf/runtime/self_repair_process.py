"""Durable process wrapper for one authorized self-repair provider call."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.repair_process_environment import isolated_repair_environment
from zf.runtime.repair_process_supervisor import RepairProcessCleanupError, run_repair_command


LAUNCH_SCHEMA = "self-repair.process-launch.v1"
RESULT_SCHEMA = "self-repair.process-result.v1"


def run_launch(launch_path: Path, result_path: Path) -> dict[str, Any]:
    started_at = _utc_now()
    try:
        launch = json.loads(Path(launch_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return _write_result(result_path, {
            "schema_version": RESULT_SCHEMA,
            "operation_id": "",
            "status": "failed",
            "returncode": 2,
            "reason": f"launch_manifest_invalid:{exc}",
            "started_at": started_at,
            "completed_at": _utc_now(),
        })
    if not isinstance(launch, dict) or launch.get("schema_version") != LAUNCH_SCHEMA:
        return _write_result(result_path, {
            "schema_version": RESULT_SCHEMA,
            "operation_id": str(launch.get("operation_id") or "")
            if isinstance(launch, dict) else "",
            "status": "failed",
            "returncode": 2,
            "reason": "launch_manifest_schema_invalid",
            "started_at": started_at,
            "completed_at": _utc_now(),
        })
    argv = launch.get("argv")
    cwd = Path(str(launch.get("cwd") or ""))
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
        or not cwd.is_dir()
    ):
        return _write_result(result_path, _result(
            launch,
            status="failed",
            returncode=2,
            reason="launch_manifest_command_invalid",
            started_at=started_at,
        ))
    timeout_seconds = max(1, int(launch.get("timeout_seconds") or 1800))
    try:
        with TemporaryDirectory(prefix='zf-repair-worker-') as workspace:
            completed = run_repair_command(
                argv,
                cwd=str(cwd),
                env=isolated_repair_environment(workspace),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
            )
        return _write_result(result_path, _result(
            launch,
            status="completed" if completed.returncode == 0 else "failed",
            returncode=int(completed.returncode),
            reason="" if completed.returncode == 0 else "provider_nonzero_exit",
            process_cleanup=completed.cleanup_receipt,
            started_at=started_at,
        ))
    except subprocess.TimeoutExpired as exc:
        return _write_result(result_path, _result(
            launch,
            status="timeout",
            returncode=124,
            reason="provider_timeout",
            process_cleanup=exc.cleanup_receipt,
            started_at=started_at,
        ))
    except RepairProcessCleanupError as exc:
        return _write_result(result_path, _result(
            launch, status="failed", returncode=125,
            reason=f"repair_process_cleanup_unknown:{exc}", started_at=started_at,
            process_cleanup={"cleanup": "unknown"},
        ))
    except OSError as exc:
        return _write_result(result_path, _result(
            launch,
            status="failed",
            returncode=127,
            reason=f"provider_spawn_failed:{exc}",
            started_at=started_at,
        ))


def _result(
    launch: dict[str, Any],
    *,
    status: str,
    returncode: int,
    reason: str,
    started_at: str,
    process_cleanup: dict | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA,
        "operation_id": str(launch.get("operation_id") or ""),
        "backend": str(launch.get("backend") or ""),
        "repair_contract_digest": str(
            launch.get("repair_contract_digest") or ""
        ),
        "status": status,
        "returncode": returncode,
        "reason": reason,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "process_cleanup": process_cleanup or {},
    }


def _write_result(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    atomic_write_text(
        Path(path),
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
    )
    return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    result = run_launch(Path(args.launch), Path(args.result))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
