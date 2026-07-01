"""CLI wrapper for ZaoFu full-stack validation scorecards."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tests.e2e.full_stack_scorecard import build_scorecard, write_markdown_report


def _run_cmd(argv: list[str], *, cwd: Path, timeout: int = 30) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "command": argv, "error": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "command": argv,
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "")[-2000:],
        "stderr": (proc.stderr or "")[-2000:],
    }


def build_preflight_report(
    *,
    repo_root: Path,
    require_real_codex: bool,
    require_docker: bool,
) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "repo_root": {"ok": repo_root.exists(), "path": str(repo_root)},
        "zf_yaml": {"ok": (repo_root / "zf.yaml").exists(), "path": str(repo_root / "zf.yaml")},
        "uv": {"ok": shutil.which("uv") is not None, "path": shutil.which("uv") or ""},
        "docker": {"ok": True, "required": require_docker},
        "codex": {"ok": True, "required": require_real_codex},
    }
    if require_docker:
        checks["docker"] = _run_cmd(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            cwd=repo_root,
        )
        checks["docker"]["required"] = True
    if require_real_codex:
        codex = shutil.which("codex")
        if not codex:
            checks["codex"] = {
                "ok": False,
                "required": True,
                "error": "codex not found on PATH",
            }
        else:
            checks["codex"] = _run_cmd([codex, "--version"], cwd=repo_root)
            checks["codex"]["required"] = True
    failed = [
        name for name, result in checks.items()
        if result.get("required", True) and not result.get("ok")
    ]
    return {
        "schema_version": "zaofu.full_stack_preflight.v1",
        "passed": not failed,
        "checks": checks,
        "failed_required_checks": failed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="full_stack_validation")
    parser.add_argument("--state-dir", required=True, help="Path to .zf runtime state")
    parser.add_argument("--repo-root", default=str(Path.cwd()))
    parser.add_argument("--output", default="", help="Write JSON scorecard")
    parser.add_argument("--markdown", default="", help="Write Markdown report")
    parser.add_argument("--preflight-output", default="", help="Write JSON preflight")
    parser.add_argument("--require-real-codex", action="store_true")
    parser.add_argument("--require-docker", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    preflight = build_preflight_report(
        repo_root=repo_root,
        require_real_codex=args.require_real_codex,
        require_docker=args.require_docker,
    )
    if args.preflight_output:
        out = Path(args.preflight_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.preflight_only:
        print(json.dumps(preflight, ensure_ascii=False, indent=2))
        return 0 if preflight["passed"] else 1

    scorecard = build_scorecard(
        Path(args.state_dir),
        require_real_codex=args.require_real_codex,
    )
    scorecard["preflight"] = preflight
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        write_markdown_report(scorecard, Path(args.markdown))
    print(json.dumps(scorecard, ensure_ascii=False, indent=2))
    return 0 if scorecard["passed"] and preflight["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
