"""zf rules — list active verification rules."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.config.loader import load_config, ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.verification.architecture_rules import parse_rules
from zf.core.verification.promoted_rules import PromotedRulesStore


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("rules", help="List active verification rules")
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Runtime state dir (default: project.state_dir from zf.yaml)",
    )
    parser.set_defaults(func=_run_list)

    sub = parser.add_subparsers(dest="rules_cmd")
    promoted_p = sub.add_parser("promoted", help="List promoted rules only")
    promoted_p.set_defaults(func=_run_promoted)


def _run_list(args: argparse.Namespace) -> int:
    if getattr(args, "rules_cmd", None) == "promoted":
        return _run_promoted(args)

    context = resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
        load_config_with_explicit=True,
    )
    workspace = context.project_root
    state_dir = context.state_dir

    # Config gates
    config_path = workspace / "zf.yaml"
    if config_path.exists():
        try:
            config = load_config(config_path)
            if config.quality_gates:
                print("Config gates:")
                for name, gate in config.quality_gates.items():
                    status = "enabled" if gate.enabled else "disabled"
                    print(f"  {name:20s} [{status}]")
        except ConfigError:
            pass

    # Architecture rules
    arch_file = workspace / "ARCHITECTURE_RULES.md"
    if arch_file.exists():
        rules = parse_rules(arch_file)
        if rules:
            print("\nArchitecture rules:")
            for rule in rules:
                print(f"  {rule.name:20s} check: {rule.check[:50]}")

    # Promoted rules
    promoted_path = state_dir / "promoted_rules.jsonl"
    if promoted_path.exists():
        store = PromotedRulesStore(promoted_path)
        promoted = store.list()
        if promoted:
            print("\nPromoted rules:")
            for r in promoted:
                print(f"  [{r.category}] {r.rule[:50]}")

    return 0


def _run_promoted(args: argparse.Namespace) -> int:
    state_dir = resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
    ).state_dir
    store = PromotedRulesStore(state_dir / "promoted_rules.jsonl")
    promoted = store.list()
    if not promoted:
        print("No promoted rules.")
        return 0
    for r in promoted:
        print(f"  [{r.category}] {r.rule}")
        if r.fix_hint:
            print(f"    fix: {r.fix_hint}")
    return 0
