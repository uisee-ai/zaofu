"""zf gate — verification gate management."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.config.loader import load_config, ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events import ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.verification.gates import CommandGate


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("gate", help="Verification gates")
    parser.set_defaults(func=lambda args: _run_list(args))

    sub = parser.add_subparsers(dest="gate_cmd")

    list_p = sub.add_parser("list", help="List configured gates")
    list_p.set_defaults(func=_run_list)

    run_p = sub.add_parser("run", help="Run a gate (or 'all')")
    run_p.add_argument("name", help="Gate name or 'all' to run all gates")
    run_p.add_argument("--command", default=None, help="Override gate command")
    run_p.set_defaults(func=_run_gate)


def _load_gates() -> dict:
    config_path = Path.cwd() / "zf.yaml"
    if not config_path.exists():
        return {}
    try:
        config = load_config(config_path)
        return {name: gate for name, gate in config.quality_gates.items()}
    except ConfigError:
        return {}


def _run_list(args: argparse.Namespace) -> int:
    gates = _load_gates()
    if not gates:
        print("(no gates configured)")
        return 0
    for name, gate in gates.items():
        status = "enabled" if gate.enabled else "disabled"
        checks = ", ".join(gate.required_checks) if gate.required_checks else "(none)"
        print(f"  {name:20s} [{status}]  checks: {checks}")
    return 0


def _run_gate(args: argparse.Namespace) -> int:
    gates = _load_gates()

    if args.name == "all":
        return _run_all_gates(gates, args)

    if args.name not in gates:
        available = ", ".join(gates.keys()) if gates else "(none)"
        print(f"Error: Gate '{args.name}' not found. Available: {available}", file=sys.stderr)
        return 1

    return _run_single_gate(args.name, gates, args)


def _run_all_gates(gates: dict, args: argparse.Namespace) -> int:
    """Run all enabled gates sequentially."""
    if not gates:
        print("(no gates configured)")
        return 0

    try:
        context = resolve_project_context()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    event_log = event_log_from_project(context.state_dir, config=context.config)
    passed = 0
    failed = 0

    for name, gate_config in gates.items():
        if not gate_config.enabled:
            print(f"  SKIP: {name} (disabled)")
            continue

        command = args.command or "true"
        gate = CommandGate(name=name, command=command)
        result = gate.run()

        event_type = "gate.passed" if result.passed else "gate.failed"
        event_log.append(ZfEvent(
            type=event_type, actor="zf-cli",
            payload={"gate": name, "exit_code": result.exit_code},
        ))

        if result.passed:
            print(f"  PASS: {name}")
            passed += 1
        else:
            print(f"  FAIL: {name}", file=sys.stderr)
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed")
    return 1 if failed > 0 else 0


def _run_single_gate(name: str, gates: dict, args: argparse.Namespace) -> int:
    """Run a single gate by name."""
    command = args.command or "true"
    gate = CommandGate(name=name, command=command)
    result = gate.run()

    try:
        context = resolve_project_context()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    event_log = event_log_from_project(context.state_dir, config=context.config)
    event_type = "gate.passed" if result.passed else "gate.failed"
    event_log.append(ZfEvent(
        type=event_type, actor="zf-cli",
        payload={"gate": name, "exit_code": result.exit_code, "output": result.output[:500]},
    ))

    if result.passed:
        print(f"PASS: {name}")
        return 0
    else:
        print(f"FAIL: {name}", file=sys.stderr)
        if result.output:
            print(result.output[:500], file=sys.stderr)
        return 1
