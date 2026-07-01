"""轻量 coding check lane — preflight evidence(X16)。

做法:diff 摘要 + 配置的 required_checks
命令逐个跑,产 evidence dict。**只产 evidence,不裁决**:不得 emit
terminal、不得弱化 workflow gate(strict/release 下它只是前置)。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def run_check_preflight(
    *,
    workdir: Path,
    checks: list[str],
    timeout_s: int = 300,
) -> dict[str, Any]:
    diff_stat = ""
    try:
        diff_stat = subprocess.run(
            ["git", "diff", "--stat"], cwd=workdir,
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()[-2000:]
    except Exception:
        pass
    results = []
    passed = True
    for cmd in checks:
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=workdir,
                capture_output=True, text=True, timeout=timeout_s,
            )
            ok = proc.returncode == 0
        except subprocess.TimeoutExpired:
            ok, proc = False, None
        passed = passed and ok
        results.append({
            "command": cmd,
            "ok": ok,
            "tail": (proc.stdout + proc.stderr)[-500:] if proc else "timeout",
        })
    return {
        "schema_version": "check-preflight.v1",
        "passed": passed,
        "diff_stat": diff_stat,
        "checks": results,
        # 边界声明:本 lane 不产 terminal —— 完成事件仍归完成协议/gate。
        "terminal_authority": "workflow gates (review/test/judge), not this lane",
    }
