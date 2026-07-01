"""zf presets — list and show workflow presets."""

from __future__ import annotations

import argparse

from zf.core.config.presets import list_presets, get_preset, generate_preset_yaml


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("presets", help="List and show workflow presets")
    parser.set_defaults(func=_run_list)

    sub = parser.add_subparsers(dest="presets_cmd")

    show_p = sub.add_parser("show", help="Show preset details")
    show_p.add_argument("name", help="Preset name")
    show_p.set_defaults(func=_run_show)


def _run_list(args: argparse.Namespace) -> int:
    if getattr(args, "presets_cmd", None) == "show":
        return _run_show(args)
    presets = list_presets()
    print("Available presets:")
    for name in presets:
        config = get_preset(name)
        roles = [r["name"] for r in config.get("roles", [])]
        print(f"  {name:15s}  roles: {', '.join(roles)}")
    print(f"\nUsage: zf init --preset <name>")
    return 0


def _run_show(args: argparse.Namespace) -> int:
    name = args.name
    try:
        yaml_str = generate_preset_yaml(name, "<project-name>")
    except ValueError as e:
        print(f"Error: {e}", file=__import__("sys").stderr)
        return 1
    print(f"# Preset: {name}\n")
    print(yaml_str)
    return 0
