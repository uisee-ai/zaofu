"""zf profile — deterministic stack detection + zf.yaml recommendation (doc 102).

    zf profile detect    [PATH] [--json]
    zf profile recommend [PATH] [--intent build|refactor|review|maintain] [--stack X] [--json]
    zf profile bootstrap [PATH] [--intent ...] [--stack X] [--apply]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zf.core.profile.apply import (
    apply_agents_md_stack,
    fill_required_checks,
    materialize_zf_yaml,
    materialize_flow_assets,
    scaffold_from_zero,
)
from zf.core.profile.detector import declared_profile, detect
from zf.core.profile.recommender import VALID_BACKENDS, VALID_INTENTS, VALID_SCALES, recommend
from zf.core.profile.schema import ProjectProfile


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("profile", help="Detect project stack + recommend zf.yaml")
    sub = parser.add_subparsers(dest="profile_cmd")

    d = sub.add_parser("detect", help="Detect the project stack")
    d.add_argument("path", nargs="?", default=".")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=run_detect)

    r = sub.add_parser("recommend", help="Recommend a zf.yaml archetype")
    r.add_argument("path", nargs="?", default=".")
    r.add_argument("--intent", default="build", choices=VALID_INTENTS)
    r.add_argument("--stack", default=None, help="Declare stack (python|node|go|rust) instead of detecting")
    r.add_argument("--scale", default=None, choices=VALID_SCALES,
                   help="Survey: project scale → harness strictness (overrides detect default)")
    r.add_argument("--backend", default="claude", choices=VALID_BACKENDS)
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=run_recommend)

    b = sub.add_parser("bootstrap", help="Detect + recommend + (optionally) materialize zf.yaml")
    b.add_argument("path", nargs="?", default=".")
    b.add_argument("--intent", default="build", choices=VALID_INTENTS)
    b.add_argument("--stack", default=None, help="Declare stack instead of detecting (for from-0)")
    b.add_argument("--scale", default=None, choices=VALID_SCALES,
                   help="Survey: project scale → harness strictness")
    b.add_argument("--backend", default="claude", choices=VALID_BACKENDS)
    b.add_argument("--apply", action="store_true", help="Write zf.yaml / required_checks / AGENTS.md")
    b.add_argument("--scaffold", action="store_true",
                   help="Also create src/tests/README so cold-start passes (from-0)")
    b.set_defaults(func=run_bootstrap)

    parser.set_defaults(func=_no_sub)


def _no_sub(args: argparse.Namespace) -> int:
    print("Error: `zf profile` requires a subcommand: detect | recommend | bootstrap",
          file=sys.stderr)
    return 2


def run_detect(args: argparse.Namespace) -> int:
    profile = detect(args.path)
    if getattr(args, "json", False):
        print(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_profile(profile)
    return 0


def run_recommend(args: argparse.Namespace) -> int:
    profile, declared = _resolve_profile(args)
    rec = recommend(profile, args.intent, declared=declared, scale=getattr(args, "scale", None), backend=getattr(args, "backend", "claude"))
    if getattr(args, "json", False):
        print(json.dumps({"profile": profile.to_dict(), "recommendation": rec.to_dict()},
                         ensure_ascii=False, indent=2))
    else:
        _print_profile(profile)
        _print_recommendation(rec)
    return 0


def run_bootstrap(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    profile, declared = _resolve_profile(args)
    rec = recommend(profile, args.intent, declared=declared, scale=getattr(args, "scale", None), backend=getattr(args, "backend", "claude"))
    _print_profile(profile)
    _print_recommendation(rec)

    if not args.apply:
        print("\n(dry-run — 加 --apply 物化 zf.yaml / required_checks / AGENTS.md)")
        return 0

    root.mkdir(parents=True, exist_ok=True)
    zf_yaml = root / "zf.yaml"
    if not zf_yaml.exists():
        zf_yaml.write_text(materialize_zf_yaml(rec.archetype, root.name, rec), encoding="utf-8")
        print(f"\n  + 生成 zf.yaml(archetype={rec.archetype}, profile={rec.harness_profile})")
        if rec.catalog == "flow":
            assets = materialize_flow_assets(rec.archetype, root, config_path=zf_yaml)
            copied = [
                *assets.get("profile_sources", []),
                *assets.get("skills", []),
            ]
            if copied:
                print(f"  + flow assets: {', '.join(copied[:12])}"
                      f"{' ...' if len(copied) > 12 else ''}")
    else:
        res = fill_required_checks(zf_yaml, rec.required_checks, write=True)
        print(f"\n  + zf.yaml 已存在 → required_checks {res['action']}")
        if res["action"] == "kept":
            print(f"    (no-clobber:保留现有 {res['existing']})")
    agents = root / "AGENTS.md"
    if agents.exists():
        res = apply_agents_md_stack(agents, profile, write=True)
        print(f"  + AGENTS.md 栈段 {res['action']}")
    if getattr(args, "scaffold", False):
        res = scaffold_from_zero(root, profile, write=True)
        if res["created"]:
            print(f"  + scaffold: {', '.join(res['created'])}")
    return 0


def _resolve_profile(args: argparse.Namespace) -> tuple[ProjectProfile, bool]:
    stack = getattr(args, "stack", None)
    if stack:
        return declared_profile(stack), True
    return detect(args.path), False


def _print_profile(p: ProjectProfile) -> None:
    print(f"探测: layout={p.layout} confidence={p.confidence} "
          f"fullstack={p.is_fullstack}")
    for u in p.units:
        fw = f" ({', '.join(u.frameworks)})" if u.frameworks else ""
        print(f"  - {u.root}: {u.language}{fw} / {u.surface}"
              f"{' / 有测试' if u.has_tests else ''}")
    if p.all_gate_cmds:
        print(f"  gate 命令: {', '.join(p.all_gate_cmds)}")


def _print_recommendation(r) -> None:
    roles_label = ", ".join(r.roles) if r.roles else f"{r.role_count} 角色"
    backend = f", {r.backend}" if r.backend else ""
    print(f"\n推荐 zf.yaml:")
    print(f"  archetype : {r.archetype} [{r.catalog}{backend}]  ({roles_label})")
    print(f"  严格度    : harness_profile={r.harness_profile}")
    print(f"  required_checks: {', '.join(r.required_checks) or '(空)'}")
    for line in r.rationale:
        print(f"  · {line}")
    if r.misroute:
        print(f"  ⚠ misroute: {r.misroute}")
