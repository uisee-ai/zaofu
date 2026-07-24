"""zf cost — cost tracking and budget display."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

from zf.core.config.project_context import resolve_project_context
from zf.core.cost.tracker import CostTracker


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("cost", help="Show cost breakdown")
    parser.add_argument("--budget", type=float, default=None, help="Budget to check against")
    parser.add_argument("--days", type=int, default=None,
                        help="Restrict to the last N days (active + recent archives)")
    parser.add_argument("--by-instance", action="store_true",
                        help="Split replicas instead of aggregating by role type")
    parser.add_argument("--by-backend", action="store_true",
                        help="Group spend by backend (claude-code / codex / ...)")
    parser.add_argument("--doctor", action="store_true",
                        help="Diagnose duplicate or legacy cost projection entries")
    parser.add_argument("--json", action="store_true", help="Wrap output in zf.cli.result.v1")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    context = resolve_project_context()
    state_dir = context.state_dir
    tracker = CostTracker(state_dir / "cost.jsonl")

    if getattr(args, "doctor", False):
        return _run_doctor(tracker)

    last_days = getattr(args, "days", None)
    if getattr(args, "by_backend", False):
        totals = tracker.summary_by_backend(last_days=last_days)
    elif getattr(args, "by_instance", False):
        totals = tracker.per_instance_totals(last_days=last_days)
    else:
        totals = tracker.per_role_totals(last_days=last_days)
    grand_total = tracker.total_usd(last_days=last_days)

    if not totals:
        if getattr(args, "json", False):
            from zf.cli.output import print_result

            print_result(
                command="cost",
                data={"totals": {}, "grand_total_usd": grand_total},
                context=context,
            )
            return 0
        print("No cost data recorded yet.")
        return 0

    if getattr(args, "json", False):
        from zf.cli.output import print_result

        budget = None
        if args.budget is not None:
            budget = {
                "limit_usd": args.budget,
                "used_percent": (
                    grand_total / args.budget * 100 if args.budget > 0 else 0
                ),
                "status": "within" if grand_total <= args.budget else "exceeded",
            }
        print_result(
            command="cost",
            data={
                "totals": {
                    role: asdict(summary) for role, summary in sorted(totals.items())
                },
                "grand_total_usd": grand_total,
                "budget": budget,
            },
            context=context,
        )
        return 0

    print("Cost Breakdown:")
    for role, summary in sorted(totals.items()):
        print(f"  {role:15s}  ${summary.total_usd:.4f}  "
              f"({summary.input_tokens:,} in / {summary.output_tokens:,} out)  "
              f"[{summary.entries} entries]")

    print(f"\n  {'Total':15s}  ${grand_total:.4f}")

    if args.budget is not None:
        pct = (grand_total / args.budget * 100) if args.budget > 0 else 0
        status = "WITHIN" if grand_total <= args.budget else "EXCEEDED"
        print(f"\n  Budget: ${args.budget:.2f}  Used: {pct:.1f}%  [{status}]")

    return 0


def _run_doctor(tracker: CostTracker) -> int:
    report = tracker.duplicate_report()
    print("Cost Projection Doctor:")
    print(f"  entries: {report['entries']}")
    print(f"  dedupe_keys: {report['dedupe_keys']}")
    print(f"  duplicate_entries: {report['duplicate_entries']}")
    print(f"  missing_dedupe_key: {report['missing_dedupe_key']}")
    print(
        "  suspect_legacy_duplicate_entries: "
        f"{report['suspect_legacy_duplicate_entries']}"
    )
    if int(report["duplicate_entries"] or 0) > 0:
        print("  status: duplicate cost projection entries found")
    elif int(report["suspect_legacy_duplicate_entries"] or 0) > 0:
        print("  status: legacy entries contain repeated cost-shaped samples")
    else:
        print("  status: no duplicate projection entries detected")
    return 0
