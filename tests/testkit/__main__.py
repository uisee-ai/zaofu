"""CLI entry point: ``python -m tests.testkit``."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .native_acceptance import NativeAcceptanceError, build_native_acceptance
from .runner import run_pytest
from .suites import MAINLINE, SUITES

BROWSER_NODES = {
    "general": "tests/e2e/test_testkit_browser_live_runtime.py::test_browser_general_start_is_driven_by_live_zf_start",
    **{family: f"tests/e2e/test_testkit_browser_live_issue.py::test_browser_issue_start_is_driven_by_live_zf_start[{family}]"
       for family in ("issue", "prd", "refactor", "refactor-strong", "refactor-two-lane")},
}


def main() -> int:
    parser = argparse.ArgumentParser(description="run ZaoFu deterministic TestKit")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--stage", default="all", choices=("all", *SUITES))
    group.add_argument(
        "--profile",
        choices=(
            "mainline",
            "native-acceptance",
            "native-pipeline",
            "premerge-pipeline",
            "requirement-audit",
            "browser-live",
        ),
        help="mainline 确定性联调；native-acceptance/requirement-audit 只读汇总；native-pipeline 显式串行真跑",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float)
    parser.add_argument(
        "--manifest", type=Path, help="native-acceptance 或 requirement-audit 的 ref/SHA-256 清单"
    )
    parser.add_argument("--native-case", choices=("normal", "impl", "verify"))
    parser.add_argument("--browser-case", choices=tuple(BROWSER_NODES))
    parser.add_argument(
        "--require-source",
        action="store_true",
        help="原生汇总必须验证三条链的来源/phase证据",
    )
    args = parser.parse_args()
    profile = args.profile
    timeout = (
        args.timeout
        if args.timeout is not None
        else (120 if profile == "premerge-pipeline" else 900)
    )
    if args.require_source and profile != "native-acceptance":
        parser.error("--require-source requires --profile native-acceptance")
    if args.native_case is not None and profile != "native-pipeline":
        parser.error("--native-case requires --profile native-pipeline")
    if args.browser_case is not None and profile != "browser-live":
        parser.error("--browser-case requires --profile browser-live")
    if profile == "browser-live":
        if args.browser_case is None or args.manifest is not None:
            parser.error("browser-live requires --browser-case and does not accept --manifest")
        result = run_pytest(
            project_root=Path(__file__).resolve().parents[2], stage="BROWSER-LIVE",
            nodeids=(BROWSER_NODES[args.browser_case],), output_dir=args.output,
            timeout_seconds=timeout, bind_source=True, source_profile="browser",
            retain_case_artifacts=True,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return int(result.verdict != "pass")
    if profile == "requirement-audit":
        if args.manifest is None:
            parser.error("requirement-audit requires --manifest")
        from .requirement_audit import build_requirement_audit

        try:
            result = build_requirement_audit(manifest=args.manifest, output=args.output)
        except (NativeAcceptanceError, OSError, ValueError, TypeError, KeyError) as exc:
            parser.exit(1, f"requirement-audit failed closed: {exc}\n")
        print(json.dumps(result, ensure_ascii=False))
        return result["gate_exit"]
    if profile == "premerge-pipeline":
        if (
            args.manifest is not None
            or args.native_case is not None
            or args.require_source
        ):
            parser.error("premerge-pipeline accepts only --output and --timeout")
        from .premerge_run import run_premerge

        try:
            result = run_premerge(
                project_root=Path(__file__).resolve().parents[2],
                output=args.output,
                timeout_seconds=timeout,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            parser.exit(1, f"premerge-pipeline failed closed: {exc}\n")
        print(json.dumps(result, ensure_ascii=False))
        return result["gate_exit"]
    if profile == "native-pipeline":
        if args.native_case is None or args.manifest is not None:
            parser.error(
                "native-pipeline requires --native-case and does not accept --manifest"
            )
        from .native_run import run_native_pipeline

        try:
            native = run_native_pipeline(
                project_root=Path(__file__).resolve().parents[2],
                case=args.native_case,
                output=args.output,
                timeout_seconds=timeout,
            )
        except (OSError, ValueError) as exc:
            parser.exit(1, f"native-pipeline failed closed: {exc}\n")
        print(json.dumps(native, ensure_ascii=False))
        return native["gate_exit"]
    if profile == "native-acceptance":
        if args.manifest is None:
            parser.error("native-acceptance requires --manifest")
        try:
            result = build_native_acceptance(
                manifest=args.manifest,
                output=args.output,
                require_source=args.require_source,
            )
        except (NativeAcceptanceError, OSError, ValueError, KeyError, TypeError) as exc:
            parser.exit(1, f"native-acceptance failed closed: {exc}\n")
        print(json.dumps(result, ensure_ascii=False))
        return result["gate_exit"]
    if args.manifest is not None:
        parser.error("--manifest requires --profile native-acceptance")
    result = run_pytest(
        project_root=Path(__file__).resolve().parents[2],
        stage="MAINLINE-DETERMINISTIC" if profile else args.stage,
        nodeids=MAINLINE if profile == "mainline" else None,
        output_dir=args.output,
        timeout_seconds=timeout,
        bind_source=profile == "mainline",
        source_profile="acceptance" if profile == "mainline" else "backend",
    )
    print(result.to_dict())
    return 0 if result.verdict == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
