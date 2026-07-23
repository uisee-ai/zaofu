#!/usr/bin/env bash

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
RUN_ROOT="${ZF_KANBAN_AGENT_E2E_RUN_ROOT:-}"
WEB_PORT="${ZF_KANBAN_AGENT_E2E_PORT:-}"
DOCKER_IMAGE="${ZF_PLAYWRIGHT_IMAGE:-mcp/playwright:latest}"
KEEP=0

usage() {
  cat <<'USAGE'
Usage: tests/e2e/scripts/run_kanban_agent_e2e.sh [--run-root PATH] [--port PORT] [--keep]

Runs the deterministic Kanban Agent fake-provider suite in Docker Playwright.
Successful runs clean their temporary state by default; failed runs are retained.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-root) RUN_ROOT="$2"; shift 2 ;;
    --port) WEB_PORT="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

pick_port() {
  "$ROOT/.venv/bin/python" - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
while port < 65535:
    with socket.socket() as sock:
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

if [[ ! -x "$ROOT/.venv/bin/zf" ]]; then
  echo "missing $ROOT/.venv; run: uv sync --extra dev --extra web" >&2
  exit 2
fi
if [[ ! -x "$ROOT/web/node_modules/.bin/playwright" ]]; then
  echo "missing web/node_modules; run: npm ci --prefix web" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for Kanban Agent browser E2E" >&2
  exit 2
fi

echo "[build] web production bundle"
npm --prefix "$ROOT/web" run build

STAMP="$(date -u +%Y%m%d-%H%M%S)"
RUN_ROOT="${RUN_ROOT:-/tmp/zf-kanban-agent-e2e-${STAMP}}"
PROJECT_ROOT="$RUN_ROOT/project"
WORKSPACE_HOME="$RUN_ROOT/workspace-home"
STATE_DIR="$PROJECT_ROOT/.zf"
FAKE_CLAUDE="$RUN_ROOT/fake_claude.py"
WEB_LOG="$RUN_ROOT/web.log"
TOKEN="zf-kanban-e2e-${STAMP}-$$"
TOKEN_SHA256="$(printf '%s' "$TOKEN" | sha256sum | cut -d' ' -f1)"
WEB_PID=""
WEB_PGID=""
SIM_INITIALIZED=0

mkdir -p "$PROJECT_ROOT" "$WORKSPACE_HOME"

cat >"$FAKE_CLAUDE" <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import time


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


args = sys.argv[1:]
provider_session_id = ""
for flag in ("--resume", "--session-id"):
    if flag in args:
        index = args.index(flag)
        if index + 1 < len(args):
            provider_session_id = args[index + 1]
            break
provider_session_id = provider_session_id or "fake-kanban-session"

raw = sys.stdin.readline()
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    payload = {"raw": raw}
text = json.dumps(payload, ensure_ascii=False)
match = re.search(r"KBA_[A-Z_]+_[a-z0-9]+", text, re.IGNORECASE)
marker = match.group(0) if match else "KBA_FAKE_DEFAULT"

emit({"type": "system", "session_id": provider_session_id})
emit({
    "type": "assistant",
    "session_id": provider_session_id,
    "message": {"content": [{"type": "text", "text": f"{marker} streamed response"}]},
})

if marker.startswith("KBA_HOLD_"):
    time.sleep(120)
    raise SystemExit(9)

time.sleep(1.5)
if marker.startswith("KBA_CREATE_"):
    result = {
        "action_proposal": {
            "action": "create-task",
            "payload": {
                "title": f"Kanban Agent proposal {marker}",
                "priority": 2,
                "contract": {
                    "behavior": f"Track the deterministic Kanban Agent E2E marker {marker}.",
                    "verification": "uv run pytest -q --no-cov tests/test_web_headless_agent.py",
                    "acceptance": "The task is created only after explicit operator acceptance.",
                },
            },
            "reason": "Explicit create-task request from the E2E operator.",
        }
    }
    reply = json.dumps(result, ensure_ascii=False)
else:
    reply = f"{marker} completed without a state-changing proposal"

emit({
    "type": "result",
    "session_id": provider_session_id,
    "result": reply,
    "usage": {"input_tokens": 12, "output_tokens": 8},
})
PY
chmod +x "$FAKE_CLAUDE"

cleanup() {
  local status="$?"
  set +e
  if [[ "$SIM_INITIALIZED" -eq 1 && -d "$STATE_DIR" ]]; then
    (
      cd "$PROJECT_ROOT" || exit 0
      ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
        PYTHONPATH="$ROOT/src" \
        "$ROOT/.venv/bin/zf" emit simulation.done \
          --actor e2e \
          --payload '{"purpose":"kanban-agent-playwright","runner":"run_kanban_agent_e2e.sh"}' \
          --state-dir "$STATE_DIR" >/dev/null 2>&1
    )
  fi
  if [[ -n "$WEB_PGID" ]]; then
    kill -TERM -- "-$WEB_PGID" >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do
      kill -0 -- "-$WEB_PGID" >/dev/null 2>&1 || break
      sleep 0.25
    done
  elif [[ -n "$WEB_PID" ]]; then
    kill -TERM "$WEB_PID" >/dev/null 2>&1 || true
  fi
  if [[ "$status" -eq 0 && "$KEEP" -eq 0 ]]; then
    find "$RUN_ROOT" -depth -delete
  else
    echo "[kept] $RUN_ROOT"
  fi
  exit "$status"
}
trap cleanup EXIT

echo "[setup] run_root=$RUN_ROOT port=${WEB_PORT:-auto} token_sha256=$TOKEN_SHA256"
(
  cd "$PROJECT_ROOT"
  ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
    PYTHONPATH="$ROOT/src" \
    "$ROOT/.venv/bin/zf" init --preset minimal --force
)
SIM_INITIALIZED=1
WEB_PORT="${WEB_PORT:-$(pick_port 8002)}"

echo "[serve] http://0.0.0.0:$WEB_PORT"
(
  cd "$PROJECT_ROOT"
  exec setsid env \
    ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
    ZF_WEB_ACTION_TOKEN="$TOKEN" \
    ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD="python3 $FAKE_CLAUDE" \
    ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S=30 \
    PYTHONPATH="$ROOT/src" \
    "$ROOT/.venv/bin/zf" web --host 0.0.0.0 --port "$WEB_PORT" --state-dir "$STATE_DIR"
) >"$WEB_LOG" 2>&1 &
WEB_PID="$!"
WEB_PGID="$(ps -o pgid= -p "$WEB_PID" | tr -d ' ')"

for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$WEB_PORT/api/snapshot/light" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! curl -fsS "http://127.0.0.1:$WEB_PORT/api/snapshot/light" >/dev/null 2>&1; then
  echo "Web server did not become ready" >&2
  tail -100 "$WEB_LOG" >&2 || true
  exit 1
fi

# Keep this suite scoped to Kanban Agent. First-install onboarding is covered by
# its own browser suite, so suppress it through the same token-gated Web action
# boundary an operator uses instead of mutating onboarding.json directly.
curl -fsS -X POST "http://127.0.0.1:$WEB_PORT/api/workspace/onboarding" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"action":"skip"}' >/dev/null

echo "[playwright] image=$DOCKER_IMAGE"
docker run --rm --network host \
  --user "$(id -u):$(id -g)" \
  --entrypoint bash \
  -v "$ROOT:/work" \
  -v "$RUN_ROOT:/zf-run" \
  -w /work/web \
  -e HOME=/tmp/zf-playwright-home \
  -e PLAYWRIGHT_BROWSERS_PATH=0 \
  -e ZF_WEB_BASE_URL="http://127.0.0.1:$WEB_PORT" \
  -e ZF_WEB_ACTION_TOKEN_FOR_TEST="$TOKEN" \
  "$DOCKER_IMAGE" \
  -lc 'set -euo pipefail; mkdir -p "$HOME"; timeout 180s ./node_modules/.bin/playwright install chromium; ./node_modules/.bin/playwright test tests/kanban-agent-conversation.spec.ts --config playwright.config.ts --project=chromium --workers=1 --reporter=line --output=/zf-run/test-results'

echo "[pass] Kanban Agent deterministic browser E2E"
