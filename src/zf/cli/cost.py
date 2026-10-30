"""zf cost — cost tracking and budget display."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from zf.core.config.project_context import (
    load_project_env,
    resolve_project_context,
)
from zf.core.cost.billing import (
    BillingError,
    BillingReconciliationService,
    BillingReconciliationStore,
    admin_key_from_environment,
)
from zf.core.cost.catalog import (
    PricingCatalogError,
    PricingCatalogStore,
    PricingCatalogSyncService,
)
from zf.core.cost.tracker import CostTracker


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("cost", help="Show cost breakdown")
    parser.add_argument("--budget", type=float, default=None, help="Budget to check against")
    parser.add_argument("--operation", help="Read current operation token/cost headroom")
    parser.add_argument("--days", type=int, default=None,
                        help="Restrict to the last N days (active + recent archives)")
    parser.add_argument("--by-instance", action="store_true",
                        help="Split replicas instead of aggregating by role type")
    parser.add_argument("--by-backend", action="store_true",
                        help="Group spend by backend (claude-code / codex / ...)")
    parser.add_argument("--doctor", action="store_true",
                        help="Diagnose duplicate or legacy cost projection entries")
    parser.add_argument("--refresh-pricing", action="store_true",
                        help="Refresh the configured pricing catalog")
    parser.add_argument("--reconcile", choices=("openai", "anthropic"),
                        help="Reconcile an organization billing window")
    parser.add_argument("--start", help="Billing window start (RFC3339)")
    parser.add_argument("--end", help="Billing window end (RFC3339)")
    parser.add_argument(
        "--accounting-mode",
        choices=("api", "subscription", "enterprise"),
        default="api",
        help="Billing route represented by the provider session",
    )
    parser.add_argument("--json", action="store_true", help="Wrap output in zf.cli.result.v1")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    context = resolve_project_context()
    state_dir = context.state_dir
    tracker = CostTracker(state_dir / "cost.jsonl")

    if getattr(args, "operation", None):
        from zf.cli.output import print_result
        from zf.runtime.workflow_budget_status import operation_budget_status

        try:
            data = operation_budget_status(state_dir, context.config, args.operation)
        except ValueError as exc:
            print_result(command="cost", context=context, data=None, ok=False, error=str(exc))
            return 2
        print_result(command="cost", context=context, data=data)
        return 0

    if getattr(args, "refresh_pricing", False):
        return _run_refresh(context, args)
    if getattr(args, "reconcile", None):
        return _run_reconcile(context, tracker, args)
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
    precision = tracker.precision_summary(last_days=last_days)
    reconciliation = BillingReconciliationStore(state_dir).latest()

    if not totals:
        if getattr(args, "json", False):
            from zf.cli.output import print_result

            print_result(
                command="cost",
                data={
                    "totals": {},
                    "grand_total_usd": grand_total,
                    "precision": precision,
                    "reconciliation": reconciliation or None,
                },
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
                "precision": precision,
                "reconciliation": reconciliation or None,
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
    print(
        "  Precision        "
        f"estimate ${precision['estimated_usd']:.4f} | "
        f"reported ${precision['provider_reported_usd']:.4f} | "
        f"billed ${precision['billed_usd']:.4f}"
    )
    if precision["unpriced_entries"] or precision["partial_entries"]:
        print(
            "  Pricing status  "
            f"{precision['unpriced_entries']} unpriced | "
            f"{precision['partial_entries']} partial"
        )
    if reconciliation:
        print(
            "  Reconciliation  "
            f"{reconciliation.get('provider', 'unknown')} | "
            f"{reconciliation.get('status', 'unknown')} | "
            f"{reconciliation.get('billed_usd') or 'unavailable'}"
        )

    if args.budget is not None:
        pct = (grand_total / args.budget * 100) if args.budget > 0 else 0
        status = "WITHIN" if grand_total <= args.budget else "EXCEEDED"
        print(f"\n  Budget: ${args.budget:.2f}  Used: {pct:.1f}%  [{status}]")

    return 0


def _run_refresh(context, args: argparse.Namespace) -> int:
    config = getattr(context.config, "cost", None)
    url = str(getattr(config, "pricing_catalog_url", "") or "")
    service = PricingCatalogSyncService(
        PricingCatalogStore(context.state_dir),
        url=url,
        ttl_seconds=int(
            getattr(config, "pricing_refresh_ttl_seconds", 86_400)
        ),
        timeout_seconds=float(
            getattr(config, "pricing_refresh_timeout_seconds", 10.0)
        ),
    )
    try:
        result = service.refresh(force=True)
    except PricingCatalogError as exc:
        print(f"Pricing catalog refresh failed: {exc}")
        return 2
    if getattr(args, "json", False):
        from zf.cli.output import print_result

        print_result(command="cost", data={"pricing_refresh": result}, context=context)
    else:
        print(f"Pricing catalog: {result.get('status', 'unknown')}")
        if result.get("catalog_version"):
            print(
                f"  {result['catalog_version']} | {result.get('digest', '')}"
            )
    return 0


def _run_reconcile(
    context,
    tracker: CostTracker,
    args: argparse.Namespace,
) -> int:
    if not args.start or not args.end:
        print("Billing reconciliation requires --start and --end.")
        return 2
    load_project_env(context.project_root)
    provider = str(args.reconcile)
    key = admin_key_from_environment(provider)
    estimate: Decimal | None = None
    try:
        estimate = tracker.estimated_usd_between(
            start=args.start,
            end=args.end,
        )
        result = BillingReconciliationService(
            BillingReconciliationStore(context.state_dir)
        ).reconcile(
            provider=provider,
            accounting_mode=args.accounting_mode,
            start=args.start,
            end=args.end,
            project_id=str(
                getattr(getattr(context.config, "project", None), "name", "")
            ),
            estimated_usd=estimate,
            api_key=key,
        )
    except (BillingError, ValueError) as exc:
        print(f"Billing reconciliation failed: {exc}")
        return 2
    if getattr(args, "json", False):
        from zf.cli.output import print_result

        print_result(
            command="cost",
            data={"reconciliation": result},
            context=context,
        )
    else:
        print(
            f"Billing reconciliation: {result['provider']} | "
            f"{result['status']}"
        )
        print(f"  estimate: ${result.get('estimated_usd') or 'unavailable'}")
        print(f"  billed:   ${result.get('billed_usd') or 'unavailable'}")
        print(f"  variance: ${result.get('variance_usd') or 'unavailable'}")
        print(f"  ref: {result['ref']}")
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
