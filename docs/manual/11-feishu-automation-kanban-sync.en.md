# Feishu Automation and Kanban Sync

> Status: active. This feature synchronizes read-only ZaoFu projections into
> Feishu Docs and Bitable. For the primary direct-message path, see
> [Feishu AI-Native Direct Bridge](19-feishu-ai-native-direct-bridge.en.md).

## 1. Boundary

Synchronization is one-way:

- Daily Brief, Weekly Review, and Project Monitor are summarized into a light
  overview and appended to a Feishu document.
- Project Status, Action Required, Delivery Health, and Runtime Health are
  written as structured rows in an Automation Insights Bitable.
- Kanban projections create or update rows keyed by stable `Task ID`.
- Feishu documents and tables never mutate `events.jsonl`, `kanban.json`, or task state.

The Automation document is a project cover page, not a raw log. The Bitable is
the routine operational view, with recommended Overview, Highlights, Action
Required, Delivery Health, Runtime Health, and History views. Detailed traces,
events, sessions, and task drilldown remain in ZaoFu Web and CLI.

## 2. Environment

Set credentials in the shell or the repository `.env`. Shell values take
precedence:

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_AUTOMATION_DOCUMENT_ID="docx_xxx"
export FEISHU_AUTOMATION_BITABLE_APP_TOKEN="bascn_xxx"
export FEISHU_AUTOMATION_BITABLE_TABLE_ID="tbl_xxx"
export FEISHU_BITABLE_APP_TOKEN="bascn_xxx"
export FEISHU_BITABLE_TABLE_ID="tbl_xxx"
```

URLs are also accepted and parsed automatically:

```bash
export FEISHU_AUTOMATION_DOCUMENT_URL="https://example.feishu.cn/docx/docx_xxx"
export FEISHU_AUTOMATION_BITABLE_URL="https://example.feishu.cn/base/bascn_xxx?table=tbl_xxx"
export FEISHU_BITABLE_URL="https://example.feishu.cn/base/bascn_xxx?table=tbl_xxx"
```

`FEISHU_TENANT_ACCESS_TOKEN` may be supplied directly instead of exchanging the
app ID and secret.

## 3. Initialize Targets

Create external resources explicitly rather than during scheduled sync:

```bash
uv run zf feishu init-targets --transport real --write-env
```

Important options include `--folder-token`, `--document-title`, `--base-name`,
`--table-name`, `--automation-table-name`, `--field key=name`,
`--overwrite-env`, and `--dry-run`.

Initialization creates the Automation document, Automation Insights table and
six recommended views, and a Kanban Base/Table with stable task fields,
`Board Column`, Grid, and Kanban views. `--write-env` persists created IDs to the
uncommitted `.env`.

Preview without Feishu calls:

```bash
uv run zf feishu init-targets --dry-run
```

Test `.env` writes with mock transport:

```bash
uv run zf feishu init-targets --transport mock --write-env
```

The app needs OpenAPI permissions to create documents, bases, fields, and
views. Layout configuration additionally needs `base:view:write_only`. Without
that scope, use `--no-ensure-layouts` to sync records and required structures
without changing view layout.

## 4. Dry Run

```bash
uv run zf feishu sync-automations --dry-run
uv run zf feishu sync-automation-insights-table --dry-run
uv run zf feishu sync-kanban-table --dry-run
```

Filter one automation with `--automation daily-brief`.

## 5. Real Synchronization

Append Automation reports to a document:

```bash
uv run zf feishu sync-automations \
  --transport real \
  --document-id "$FEISHU_AUTOMATION_DOCUMENT_ID"
```

`--document-url "$FEISHU_AUTOMATION_DOCUMENT_URL"` is equivalent.

Sync Automation Insights:

```bash
uv run zf feishu sync-automation-insights-table --transport real
```

If the Automation table does not exist but the Base token does, the command can
create the table and fields, write IDs back to `.env`, and then upsert summary
and insight rows by `Row Key`.

Sync Kanban:

```bash
uv run zf feishu sync-kanban-table \
  --transport real \
  --app-token "$FEISHU_BITABLE_APP_TOKEN" \
  --table-id "$FEISHU_BITABLE_TABLE_ID"
```

Or pass `--bitable-url "$FEISHU_BITABLE_URL"`.

By default, sync includes the active board and terminal tasks from the last 30
days. It ensures `Board Column`, Grid, and Kanban views and their recommended
sorting and visible fields. Gantt is not created because current start and
completion values remain text-compatible fields rather than guaranteed date
columns. Use `--active-only` to mirror only active `kanban.json`.

Use `--no-ensure-views` to write rows without creating fields or views. Use
`--no-ensure-layouts` to preserve manually customized layouts while still
ensuring missing fields and views.

If a remote task row is deleted, it is recreated by `Task ID`. If the configured
Base or table is gone, sync can recreate it and update `.env`; add
`--no-recreate-missing` to fail instead.

Override field names for an existing table:

```bash
uv run zf feishu sync-kanban-table \
  --transport real \
  --field task_id=TaskID \
  --field title=Title \
  --field status=Status \
  --field assigned_to=Owner
```

## 6. Cron

```bash
uv run zf feishu cron-template --daily-time 09:00 --hourly-minute 5
```

The default template syncs Automation and Insights daily and Kanban hourly. It
writes logs under `project.state_dir/logs/` and fixes the project root and state
directory explicitly, preventing cron from accidentally using `$PWD/.zf`.
