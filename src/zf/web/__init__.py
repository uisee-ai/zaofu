"""F-WEB-MVP-01: local Web dashboard for zaofu.

Optional install: ``pip install -e ".[web]"``
Run:              ``zf web --port 8001``

The web layer is intentionally local-first:
  - FastAPI backend (server.py)
  - React/Vite build served from web/dist when present
  - static/index.html fallback when web/dist is absent
  - SSE for live event tail; plain HTTP for snapshots
  - Read-only projections for tasks, events, traces, candidates,
    fanouts, roles, workdirs, skills, runtime, and diagnostics
  - Token-gated /api/actions/* boundary; disabled by default
  - No PTY transport; local-only by default (127.0.0.1)

Designed to be deletable: removing this directory does not affect
the rest of zaofu. The CLI dispatches `zf web` here lazily so the
fastapi/uvicorn deps stay optional.
"""
