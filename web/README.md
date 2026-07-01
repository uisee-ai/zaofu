# ZaoFu Web Client

React/Vite client for the local ZaoFu workbench.

```bash
npm --prefix web install
PYTHONPATH=src python3 -m zf.cli.main web --port 8001
ZF_API_TARGET=http://127.0.0.1:8001 npm --prefix web run dev
```

The Vite dev server proxies `/api/*` to `http://127.0.0.1:8001` by default.
Override with `ZF_API_TARGET` if `zf web` is using another host or port.

Production build:

```bash
npm --prefix web run typecheck
npm --prefix web run build
PYTHONPATH=src python3 -m zf.cli.main web --port 8001
npm --prefix web run api-smoke
npm --prefix web run smoke
```

FastAPI serves `web/dist` when it exists and falls back to
`src/zf/web/static` otherwise.

`npm --prefix web run smoke` runs the Playwright smoke against
`ZF_WEB_BASE_URL` or `http://127.0.0.1:8001` by default.

The React client is presentation-only. It consumes `/api/snapshot`,
`/api/stream`, and read projections under `/api/*`; controlled mutations
must go through token-gated `/api/actions/<name>` and kernel-owned services.

Kanban columns use `/api/snapshot.tasks[].kanban_column` as a read-only display
projection. `Task.status` remains the durable runtime state; normal harness
handoffs may keep it as `in_progress` while `phase` and `assigned_to` move the
card through Review and Verify.
