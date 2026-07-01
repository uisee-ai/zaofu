#!/usr/bin/env bash
# Archive + read-model + Web API regression probe for ZaoFu event truth.

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

RUN_ROOT="${ZF_EVENT_READ_MODEL_E2E_RUN_ROOT:-}"
API_PORT="${ZF_EVENT_READ_MODEL_E2E_API_PORT:-}"
REAL_PROVIDER="none"
CODEX_SANDBOX="${ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX:-read-only}"
SKIP_UNIT=0
WITH_PLAYWRIGHT=0
KEEP=1

usage() {
  cat <<'USAGE'
Usage:
  tests/e2e/scripts/run_event_read_model_e2e.sh [options]

Options:
  --run-root PATH          Temporary run root. Default: /tmp/zf-event-read-model-e2e-<utc>
  --api-port PORT          FastAPI port. Default: auto-pick 8002+
  --skip-unit              Skip focused pytest preflight.
  --with-playwright        Also run the Web interactive fake-provider Docker audit.
  --real-provider codex    Also run optional real Codex smoke via the Web interactive audit.
  --skip-real-provider     Default; do not call real providers.
  --codex-sandbox POLICY   Sandbox forwarded to the optional Codex smoke.
  --cleanup                Remove the temporary run root on success.
  -h, --help               Show this help.

The default path does not require Docker or real provider credentials. It
creates a /tmp project, forces event log archival, rebuilds read_model.sqlite,
starts a temporary API server, probes hot Web endpoints, and writes JSON +
Markdown reports under the run root.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-root) RUN_ROOT="$2"; shift 2 ;;
    --api-port) API_PORT="$2"; shift 2 ;;
    --skip-unit) SKIP_UNIT=1; shift ;;
    --with-playwright) WITH_PLAYWRIGHT=1; shift ;;
    --real-provider) REAL_PROVIDER="$2"; shift 2 ;;
    --skip-real-provider) REAL_PROVIDER="none"; shift ;;
    --codex-sandbox) CODEX_SANDBOX="$2"; shift 2 ;;
    --cleanup) KEEP=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$REAL_PROVIDER" in
  none|codex) ;;
  *) echo "--real-provider must be codex or use --skip-real-provider" >&2; exit 2 ;;
esac

case "$CODEX_SANDBOX" in
  read-only|workspace-write|danger-full-access) ;;
  *) echo "--codex-sandbox must be read-only, workspace-write, or danger-full-access" >&2; exit 2 ;;
esac

utc_stamp="$(date -u +%Y%m%d-%H%M%S)"
RUN_ROOT="${RUN_ROOT:-/tmp/zf-event-read-model-e2e-${utc_stamp}}"
PROJECT_ROOT="$RUN_ROOT/project"
STATE_DIR="$PROJECT_ROOT/.zf"
REPORT_JSON="$RUN_ROOT/report.json"
REPORT_MD="$RUN_ROOT/report.md"

pick_port() {
  local start="$1"
  python3 - "$start" <<'PY'
import socket
import sys

port = int(sys.argv[1])
while port < 65535:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            port += 1
            continue
        print(port)
        raise SystemExit(0)
raise SystemExit("no free port")
PY
}

API_PORT="${API_PORT:-$(pick_port 8002)}"
mkdir -p "$PROJECT_ROOT"

cat >"$PROJECT_ROOT/zf.yaml" <<YAML
version: "1.0"
preset: event-read-model-e2e
project:
  name: zf-event-read-model-e2e
  state_dir: .zf
session:
  tmux_session: zf-event-read-model-e2e
  tmux_layout: pane_grid
orchestrator:
  backend: python
  permission_mode: bypass
workflow:
  dag:
    external_triggers: [workflow.invoke.requested]
  stages:
    - id: review-wave
      trigger: workflow.invoke.requested
      topology: fanout_reader
      roles: [review]
      aggregate:
        mode: wait_for_all
        timeout_seconds: 300
        success_event: review.approved
        failure_event: review.rejected
roles:
  - name: dev
    backend: python
    permission_mode: bypass
    stages: [implement]
    triggers: [task.assigned]
    publishes: [dev.build.done]
  - name: review
    backend: python
    permission_mode: bypass
    stages: [review]
    triggers: [workflow.invoke.requested]
    publishes: [review.approved, review.rejected]
YAML

if [[ "$SKIP_UNIT" -eq 0 ]]; then
  PYTHONPATH="$ROOT/src" pytest -q \
    tests/test_event_read_model.py \
    tests/test_web_server.py \
    tests/test_workflow_graph_runtime.py \
    tests/e2e/test_full_stack_validation.py
fi

echo "[setup] initialize $PROJECT_ROOT"
(
  cd "$PROJECT_ROOT"
  PYTHONPATH="$ROOT/src" python3 -m zf.cli.main init --no-workspace-register --force
)

echo "[setup] seed archived + active events"
PYTHONPATH="$ROOT/src" ZF_EVENT_LOG_MAX_ACTIVE_BYTES=1 python3 - "$STATE_DIR" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore

state_dir = Path(sys.argv[1])
state_dir.mkdir(parents=True, exist_ok=True)
TaskStore(state_dir / "kanban.json").add(
    Task(id="TASK-E2E", title="event read model e2e", status="in_progress", assigned_to="dev")
)
log = EventLog(state_dir / "events.jsonl")
events = [
    ZfEvent(type="task.created", id="evt-task", actor="e2e", task_id="TASK-E2E", payload={"source_kind": "issue"}),
    ZfEvent(type="dev.build.done", id="evt-dev", actor="dev", task_id="TASK-E2E", payload={"trace_id": "trace-e2e"}),
    ZfEvent(type="workflow.invoke.requested", id="evt-wf", actor="channel", task_id="TASK-E2E", payload={"entrypoint": "channel", "pattern_id": "review-wave", "fanout_id": "fo-e2e"}),
    ZfEvent(type="fanout.started", id="evt-fo-start", actor="orchestrator", task_id="TASK-E2E", payload={"stage_id": "review-wave", "fanout_id": "fo-e2e"}),
    ZfEvent(type="fanout.child.dispatched", id="evt-fo-dispatch", actor="orchestrator", task_id="TASK-E2E", payload={"stage_id": "review-wave", "fanout_id": "fo-e2e", "child_id": "review"}),
    ZfEvent(type="fanout.child.completed", id="evt-fo-child", actor="review", task_id="TASK-E2E", payload={"stage_id": "review-wave", "fanout_id": "fo-e2e", "child_id": "review"}),
    ZfEvent(type="fanout.aggregate.completed", id="evt-fo-agg", actor="orchestrator", task_id="TASK-E2E", payload={"stage_id": "review-wave", "fanout_id": "fo-e2e", "status": "completed"}),
    ZfEvent(type="run.started", id="evt-run-start", actor="orchestrator", task_id="TASK-E2E", payload={"run_id": "run-e2e"}),
    ZfEvent(type="run.completed", id="evt-run-done", actor="orchestrator", task_id="TASK-E2E", payload={"run_id": "run-e2e"}),
    ZfEvent(type="channel.message.posted", id="evt-channel", actor="operator", task_id="TASK-E2E", payload={"channel_id": "ch-e2e", "thread_id": "main"}),
]
for event in events:
    log.append(event)
log.close()
PY

echo "[projection] rebuild/status/doctor"
(
  cd "$PROJECT_ROOT"
  PYTHONPATH="$ROOT/src" python3 -m zf.cli.main projection --state-dir "$STATE_DIR" rebuild --json >"$RUN_ROOT/projection-rebuild.json"
  PYTHONPATH="$ROOT/src" python3 -m zf.cli.main projection --state-dir "$STATE_DIR" status --count-source --json >"$RUN_ROOT/projection-status.json"
  PYTHONPATH="$ROOT/src" python3 -m zf.cli.main projection --state-dir "$STATE_DIR" doctor --json >"$RUN_ROOT/projection-doctor.json"
)

API_PID=""
cleanup_processes() {
  if [[ -n "$API_PID" ]]; then kill "$API_PID" >/dev/null 2>&1 || true; fi
}
trap cleanup_processes EXIT

echo "[serve] zf web http://0.0.0.0:$API_PORT"
(
  cd "$PROJECT_ROOT"
  PYTHONPATH="$ROOT/src" python3 -m zf.cli.main web --host 0.0.0.0 --port "$API_PORT" --state-dir "$STATE_DIR"
) >"$RUN_ROOT/api.log" 2>&1 &
API_PID="$!"

for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$API_PORT/api/snapshot/light" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! curl -fsS "http://127.0.0.1:$API_PORT/api/snapshot/light" >/dev/null 2>&1; then
  echo "[error] API did not become ready" >&2
  tail -80 "$RUN_ROOT/api.log" >&2 || true
  exit 1
fi

echo "[probe] Web API hot paths"
python3 - "$API_PORT" "$RUN_ROOT/api-probes.json" <<'PY'
from __future__ import annotations

import json
import sys
import time
import urllib.request

port = int(sys.argv[1])
out = sys.argv[2]
base = f"http://127.0.0.1:{port}"
endpoints = [
    ("events", "/api/events?limit=20", 1.0),
    ("task_timeline", "/api/tasks/TASK-E2E/timeline", 1.0),
    ("snapshot_light", "/api/snapshot/light", 2.0),
    ("workflow_graph", "/api/workflow/graph", 2.0),
]
results = []
passed = True
for name, path, budget_s in endpoints:
    started = time.perf_counter()
    with urllib.request.urlopen(base + path, timeout=15) as response:
        body = response.read()
        status = response.status
    elapsed = time.perf_counter() - started
    ok = status == 200 and elapsed <= budget_s
    passed = passed and ok
    results.append({
        "name": name,
        "path": path,
        "status": status,
        "elapsed_ms": round(elapsed * 1000, 2),
        "budget_ms": int(budget_s * 1000),
        "passed": ok,
        "bytes": len(body),
    })
payload = {
    "schema_version": "event-read-model-api-probes.v1",
    "passed": passed,
    "base_url": base,
    "results": results,
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
print(json.dumps(payload, ensure_ascii=False, indent=2))
raise SystemExit(0 if passed else 1)
PY

PLAYWRIGHT_STATUS="skipped"
REAL_PROVIDER_STATUS="skipped"
if [[ "$WITH_PLAYWRIGHT" -eq 1 ]]; then
  tests/e2e/scripts/run_web_interactive_e2e_audit.sh --skip-unit --cleanup
  PLAYWRIGHT_STATUS="passed"
fi
if [[ "$REAL_PROVIDER" == "codex" ]]; then
  tests/e2e/scripts/run_web_interactive_e2e_audit.sh \
    --skip-unit \
    --real-provider codex \
    --codex-sandbox "$CODEX_SANDBOX" \
    --cleanup
  REAL_PROVIDER_STATUS="passed"
fi

python3 - "$RUN_ROOT" "$STATE_DIR" "$PROJECT_ROOT" "$REPORT_JSON" "$REPORT_MD" "$PLAYWRIGHT_STATUS" "$REAL_PROVIDER_STATUS" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

run_root = Path(sys.argv[1])
state_dir = Path(sys.argv[2])
project_root = Path(sys.argv[3])
report_json = Path(sys.argv[4])
report_md = Path(sys.argv[5])
playwright_status = sys.argv[6]
real_provider_status = sys.argv[7]

status = json.loads((run_root / "projection-status.json").read_text(encoding="utf-8"))
doctor = json.loads((run_root / "projection-doctor.json").read_text(encoding="utf-8"))
probes = json.loads((run_root / "api-probes.json").read_text(encoding="utf-8"))
archive_segments = [
    item for item in status.get("source_cursor", {}).get("segments", [])
]
payload = {
    "schema_version": "event-read-model-e2e.v1",
    "passed": (
        status.get("projection_state") == "ready"
        and int(status.get("projection_lag") or 0) == 0
        and int(status.get("segment_count") or 0) > 1
        and probes.get("passed") is True
        and doctor.get("status") in {"ok", "attention"}
    ),
    "run_root": str(run_root),
    "project_root": str(project_root),
    "state_dir": str(state_dir),
    "projection": status,
    "doctor": doctor,
    "api_probes": probes,
    "playwright": playwright_status,
    "real_provider": real_provider_status,
}
report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
lines = [
    "# ZaoFu Event Read Model E2E",
    "",
    f"- status: {'passed' if payload['passed'] else 'failed'}",
    f"- run_root: `{run_root}`",
    f"- state_dir: `{state_dir}`",
    f"- projection_state: `{status.get('projection_state')}`",
    f"- source_seq: `{status.get('source_seq')}`",
    f"- projection_lag: `{status.get('projection_lag')}`",
    f"- segment_count: `{status.get('segment_count')}`",
    f"- playwright: `{playwright_status}`",
    f"- real_provider: `{real_provider_status}`",
    "",
    "## API Probes",
    "",
    "| endpoint | status | elapsed_ms | budget_ms | result |",
    "|---|---:|---:|---:|---|",
]
for row in probes.get("results", []):
    lines.append(
        f"| `{row['name']}` | {row['status']} | {row['elapsed_ms']} | {row['budget_ms']} | {'pass' if row['passed'] else 'fail'} |"
    )
report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
raise SystemExit(0 if payload["passed"] else 1)
PY

if [[ "$KEEP" -eq 0 ]]; then
  cleanup_processes
  rm -rf "$RUN_ROOT"
else
  echo "[kept] $RUN_ROOT"
  echo "[report] $REPORT_JSON"
  echo "[report] $REPORT_MD"
fi
