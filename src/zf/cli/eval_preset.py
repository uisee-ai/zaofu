"""zf eval preset — static `zf.yaml` evaluation before real E2E (doc 68 S4)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("eval", help="Static evaluation of zaofu config")
    sub = parser.add_subparsers(dest="eval_cmd")

    preset = sub.add_parser("preset", help="Statically evaluate a zf.yaml preset")
    preset.add_argument("yaml_path", help="Path to a zf.yaml to evaluate")
    preset.add_argument("--format", choices=["text", "json"], default="text")
    preset.set_defaults(func=run_eval_preset)

    parser.set_defaults(func=lambda args: _show_help(parser))


def _show_help(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 0


def run_eval_preset(args: argparse.Namespace) -> int:
    from zf.core.config.loader import ConfigError, load_config
    from zf.core.config.preset_eval import evaluate_preset

    path = Path(args.yaml_path)
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        return 1
    try:
        config = load_config(path)
    except ConfigError as e:
        print(f"Error: invalid config: {e}", file=sys.stderr)
        return 1

    report = evaluate_preset(config)
    if getattr(args, "format", "text") == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Preset Evaluation: {path}\n")
        for c in report["checks"]:
            mark = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL"}.get(c["status"], c["status"])
            line = f"{mark} {c['name']}"
            if c["detail"]:
                line += f" — {c['detail']}"
            print(line)
        s = report["summary"]
        print(f"\n{s['pass']} pass, {s['warn']} warn, {s['fail']} fail")
    # nonzero exit on any FAIL so CI / scripts can gate on it
    return 0 if report["ok"] else 2
