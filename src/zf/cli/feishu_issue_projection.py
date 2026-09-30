"""CLI registration for the Issue lifecycle Bitable projection."""

from __future__ import annotations

import os
import json
import time
from hashlib import sha256
from typing import Any


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "sync-issue-table",
        help="Project /issue sessions and Issue/Triage/Merge lifecycle into Feishu Bitable",
    )
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--project-id", default="")
    parser.add_argument("--project-name", default="")
    parser.add_argument("--app-token", default="")
    parser.add_argument("--table-id", default="")
    parser.add_argument("--bitable-url", default="")
    parser.add_argument("--backend", choices=["mock", "lark-cli"], default="")
    parser.add_argument("--identity", choices=["user", "bot"], default="", help="lark-cli identity (default: FEISHU_ISSUE_BITABLE_IDENTITY or user)")
    parser.add_argument("--transport", choices=["mock", "real"], default="", help="Deprecated backend alias")
    parser.add_argument("--field", action="append", default=[], metavar="KEY=FEISHU_FIELD")
    parser.add_argument("--table-name", default="Issue")
    parser.add_argument("--create-table", action="store_true")
    parser.add_argument("--no-ensure-views", action="store_true")
    parser.add_argument("--no-ensure-layouts", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--watch", action="store_true", help="持续同步 Issue 生命周期")
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.set_defaults(func=run)


def run(args) -> int:
    from zf.cli import feishu as cli
    from zf.core.config.loader import ConfigError
    from zf.core.config.project_context import resolve_project_context
    from zf.core.events import EventWriter
    from zf.core.events.factory import event_log_from_project
    from zf.core.state.atomic_io import atomic_write_text
    from zf.integrations.feishu.issue_sync import (
        build_issue_records, issue_field_specs, issue_view_layout_specs, issue_view_specs, sync_issue_bitable,
    )
    from zf.integrations.feishu.sync import FeishuSyncLedger
    from zf.integrations.feishu.targets import parse_feishu_bitable_ref
    from zf.integrations.feishu.transport import FeishuTransportError

    try:
        context = resolve_project_context(explicit_state_dir=getattr(args, "state_dir", None))
        field_map = cli._parse_field_map(args.field)
        backend = cli._resolve_projection_backend(args)
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 1
    cli._load_project_env(context.project_root)
    identity = str(getattr(args, "identity", "") or os.environ.get("FEISHU_ISSUE_BITABLE_IDENTITY", "user")).strip() or "user"
    project_id, project_name = cli._project_identity(context, args)
    explicit_app = str(getattr(args, "app_token", "") or "").strip()
    explicit_table = str(getattr(args, "table_id", "") or "").strip()
    url = str(getattr(args, "bitable_url", "") or os.environ.get("FEISHU_ISSUE_BITABLE_URL", "")).strip()
    explicit_ref = parse_feishu_bitable_ref(explicit_app) if "://" in explicit_app else None
    ref = parse_feishu_bitable_ref(url) if url else None
    app_token = (explicit_ref.app_token if explicit_ref else explicit_app) or (ref.app_token if ref else "") or os.environ.get("FEISHU_ISSUE_BITABLE_APP_TOKEN", "").strip() or os.environ.get("FEISHU_BITABLE_APP_TOKEN", "").strip()
    table_id = explicit_table or (explicit_ref.table_id if explicit_ref else "") or (ref.table_id if ref else "") or os.environ.get("FEISHU_ISSUE_BITABLE_TABLE_ID", "").strip()
    initialized = False
    def once() -> int:
        nonlocal table_id, initialized
        if backend == "lark-cli":
            from zf.integrations.feishu.lark_cli import LarkCliBitableClient, LarkCliRunner
            client = LarkCliBitableClient(
                runner=LarkCliRunner(
                    identity=identity,
                    prefer_profile=identity == "bot",
                )
            )
        else:
            client = cli._build_bitable_client(backend)
        schema: dict[str, Any] = {"created": []}; views: dict[str, Any] = {"created": []}; layouts: dict[str, Any] = {"configured": []}
        if not args.dry_run and not app_token:
            print("Error: provide --bitable-url or --app-token/--table-id or FEISHU_ISSUE_BITABLE_APP_TOKEN/FEISHU_ISSUE_BITABLE_TABLE_ID")
            return 1
        if app_token and not table_id and args.create_table and not args.dry_run:
            table_id = cli._table_id_from_result(client.create_table(app_token, name=args.table_name or "Issue"))
            print(f"Created Issue table: app_token={app_token} table_id={table_id}")
        if not args.dry_run and not table_id:
            print("Error: table id is required; pass --table-id or include ?table=... in --bitable-url (or use --create-table)")
            return 1
        if app_token and table_id and not args.dry_run and (not args.watch or not initialized):
            schema = client.ensure_fields(app_token, table_id, issue_field_specs(field_map))
            if not args.no_ensure_views:
                views = client.ensure_views(app_token, table_id, issue_view_specs())
                if not args.no_ensure_layouts:
                    layouts = client.ensure_view_layouts(app_token, table_id, issue_view_layout_specs(field_map))
            initialized = True
        if args.watch:
            watch_path = context.state_dir / "integrations" / "feishu" / "issue-sync-watch.json"
            records = build_issue_records(context.state_dir, synced_at="watch", field_map=field_map)
            digest_rows = [{key: value for key, value in row.items() if key != "Synced At"} for row in records]
            digest = sha256(json.dumps(digest_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            previous = ""
            previous_at = 0.0
            try:
                watch_state = json.loads(watch_path.read_text(encoding="utf-8"))
                previous = str(watch_state.get("digest") or "")
                previous_at = float(watch_state.get("updated_at") or 0.0)
            except (OSError, ValueError, TypeError):
                pass
            if previous == digest and time.time() - previous_at < 300:
                print("Issue Bitable unchanged; skipped remote write.")
                return 0
        result = sync_issue_bitable(
            state_dir=context.state_dir, project_id=project_id, project_name=project_name,
            app_token=app_token or "dry-run-app", table_id=table_id or "dry-run-table",
            client=client, ledger=FeishuSyncLedger.for_state_dir(context.state_dir),
            writer=None if args.dry_run else EventWriter(event_log_from_project(context.state_dir, config=context.config)),
            field_map=field_map, dry_run=bool(args.dry_run),
        )
        if result.get("dry_run"):
            print(result["markdown"], end="")
        else:
            print(f"Synced Issue table to Feishu Bitable {table_id}: rows={result['rows']} created={result['created']} updated={result['updated']}")
            print(f"Issue Base layout: fields_created={len(schema.get('created') or [])} views_created={len(views.get('created') or [])} views_configured={len(layouts.get('configured') or [])}")
            if args.watch:
                watch_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(
                    watch_path,
                    json.dumps({"digest": digest, "updated_at": time.time()}, ensure_ascii=False) + "\n",
                )
        return 0
    try:
        if not args.watch:
            return once()
        while True:
            try:
                code = once()
            except (ValueError, FeishuTransportError, OSError) as exc:
                print(f"Issue Bitable watch retryable error: {exc}")
                time.sleep(max(1.0, float(args.poll_interval_seconds)))
                continue
            if code:
                return code
            time.sleep(max(0.5, float(args.poll_interval_seconds)))
    except KeyboardInterrupt:
        print("Stopped Issue Bitable watcher.")
        return 0
    except (ValueError, FeishuTransportError, OSError) as exc:
        print(f"Error: {exc}")
        return 1


__all__ = ["register", "run"]
