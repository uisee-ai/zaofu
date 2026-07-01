#!/usr/bin/env bash
# Run the ZaoFu Web interactive E2E audit in a temporary project.
#
# Default run:
#   tests/e2e/scripts/run_web_interactive_e2e_audit.sh
#
# Optional real Codex headless smoke:
#   tests/e2e/scripts/run_web_interactive_e2e_audit.sh --real-provider codex

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

RUN_ROOT="${ZF_INTERACTIVE_E2E_RUN_ROOT:-}"
API_PORT="${ZF_INTERACTIVE_E2E_API_PORT:-}"
WEB_PORT="${ZF_INTERACTIVE_E2E_WEB_PORT:-}"
TOKEN="${ZF_INTERACTIVE_E2E_TOKEN:-}"
DOCKER_IMAGE="${ZF_PLAYWRIGHT_IMAGE:-mcp/playwright:latest}"
SOURCE_PROJECT_DIR="${ZF_INTERACTIVE_E2E_SOURCE_PROJECT_DIR:-}"
SOURCE_ZF_YAML="${ZF_INTERACTIVE_E2E_SOURCE_ZF_YAML:-}"
REAL_PROVIDER="none"
CODEX_SANDBOX="${ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX:-read-only}"
CODEX_APPROVAL_POLICY="${ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY:-never}"
SKIP_UNIT=0
SKIP_DOCKER=0
KEEP=1

usage() {
  cat <<'USAGE'
Usage:
  tests/e2e/scripts/run_web_interactive_e2e_audit.sh [options]

Options:
  --run-root PATH          Temporary run root. Default: /tmp/zf-web-interactive-e2e-<utc>
  --api-port PORT          FastAPI port. Default: auto-pick 8002+
  --web-port PORT          Vite port. Default: auto-pick 5174+
  --token TOKEN            Web action token. Default: generated per run
  --real-provider none     Default; use fake Claude headless provider.
  --real-provider codex    Also run the Kanban Agent smoke against real Codex.
  --codex-sandbox POLICY   Codex sandbox for real-provider smoke.
                           Default: env ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX or read-only.
                           Values: read-only, workspace-write, danger-full-access.
  --codex-approval-policy POLICY
                           Codex approval policy. Default: env
                           ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY or never.
  --source-project-dir PATH
                          Copy this Project into the temp run root before init.
  --source-zf-yaml PATH    Use this zf.yaml, patched to the temp state dir.
  --skip-unit              Skip pytest/typecheck preflight.
  --skip-docker            Prepare services but skip Docker Playwright.
  --cleanup                Remove the temporary run root on success.
  -h, --help               Show this help.

Notes:
  - API and Vite bind to 0.0.0.0 for Docker Playwright access.
  - Port 8001 is intentionally not used for temporary E2E.
  - Source Project copies exclude .git, .zf, .codex, .env*, node_modules, dist,
    build, and coverage.
  - Real provider smoke is opt-in because it can consume model budget.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-root) RUN_ROOT="$2"; shift 2 ;;
    --api-port) API_PORT="$2"; shift 2 ;;
    --web-port) WEB_PORT="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --real-provider) REAL_PROVIDER="$2"; shift 2 ;;
    --codex-sandbox) CODEX_SANDBOX="$2"; shift 2 ;;
    --codex-approval-policy) CODEX_APPROVAL_POLICY="$2"; shift 2 ;;
    --source-project-dir) SOURCE_PROJECT_DIR="$2"; shift 2 ;;
    --source-zf-yaml) SOURCE_ZF_YAML="$2"; shift 2 ;;
    --skip-unit) SKIP_UNIT=1; shift ;;
    --skip-docker) SKIP_DOCKER=1; shift ;;
    --cleanup) KEEP=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$REAL_PROVIDER" in
  none|codex) ;;
  *) echo "--real-provider must be none or codex" >&2; exit 2 ;;
esac

case "$CODEX_SANDBOX" in
  read-only|workspace-write|danger-full-access) ;;
  *) echo "--codex-sandbox must be read-only, workspace-write, or danger-full-access" >&2; exit 2 ;;
esac

utc_stamp="$(date -u +%Y%m%d-%H%M%S)"
RUN_ROOT="${RUN_ROOT:-/tmp/zf-web-interactive-e2e-${utc_stamp}}"
TOKEN="${TOKEN:-zf-e2e-token-${utc_stamp}}"
PROJECT_ROOT="$RUN_ROOT/project"
STATE_DIR="$PROJECT_ROOT/.zf"
SPEC_FILE="$RUN_ROOT/web-interactive-audit.spec.ts"
PLAYWRIGHT_CONFIG="$RUN_ROOT/playwright.config.cjs"
FAKE_CLAUDE="$RUN_ROOT/fake_claude_headless.py"
REPORT="$RUN_ROOT/report.md"

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
WEB_PORT="${WEB_PORT:-$(pick_port 5174)}"

mkdir -p "$RUN_ROOT" "$PROJECT_ROOT"

if [[ -n "$SOURCE_PROJECT_DIR" ]]; then
  if [[ ! -d "$SOURCE_PROJECT_DIR" ]]; then
    echo "--source-project-dir does not exist or is not a directory: $SOURCE_PROJECT_DIR" >&2
    exit 2
  fi
  if ! command -v rsync >/dev/null 2>&1; then
    echo "rsync is required for --source-project-dir" >&2
    exit 2
  fi
  echo "[setup] copy source project: $SOURCE_PROJECT_DIR -> $PROJECT_ROOT"
  rsync -a \
    --exclude ".git/" \
    --exclude ".zf/" \
    --exclude ".codex/" \
    --exclude ".env" \
    --exclude ".env.*" \
    --exclude "node_modules/" \
    --exclude "dist/" \
    --exclude "build/" \
    --exclude "coverage/" \
    "$SOURCE_PROJECT_DIR"/ "$PROJECT_ROOT"/
fi

if [[ -n "$SOURCE_ZF_YAML" ]]; then
  if [[ ! -f "$SOURCE_ZF_YAML" ]]; then
    echo "--source-zf-yaml does not exist or is not a file: $SOURCE_ZF_YAML" >&2
    exit 2
  fi
  echo "[setup] copy source zf.yaml: $SOURCE_ZF_YAML"
  cp "$SOURCE_ZF_YAML" "$PROJECT_ROOT/zf.yaml"
  python3 - "$PROJECT_ROOT/zf.yaml" "$STATE_DIR" "zf-web-interactive-e2e-$utc_stamp" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

yaml_path = Path(sys.argv[1])
state_dir = sys.argv[2]
tmux_session = sys.argv[3]
lines = yaml_path.read_text(encoding="utf-8").splitlines()

section = ""
out: list[str] = []
project_state_written = False
session_tmux_written = False

for line in lines:
    stripped = line.strip()
    if line and not line.startswith((" ", "\t")) and stripped.endswith(":"):
        if section == "project" and not project_state_written:
            out.append(f"  state_dir: {state_dir}")
            project_state_written = True
        if section == "session" and not session_tmux_written:
            out.append(f"  tmux_session: {tmux_session}")
            session_tmux_written = True
        section = stripped[:-1]
    if section == "project" and stripped.startswith("state_dir:"):
        out.append(f"  state_dir: {state_dir}")
        project_state_written = True
        continue
    if section == "session" and stripped.startswith("tmux_session:"):
        out.append(f"  tmux_session: {tmux_session}")
        session_tmux_written = True
        continue
    out.append(line)

if section == "project" and not project_state_written:
    out.append(f"  state_dir: {state_dir}")
if section == "session" and not session_tmux_written:
    out.append(f"  tmux_session: {tmux_session}")

yaml_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY
else
  cat >"$PROJECT_ROOT/zf.yaml" <<YAML
version: "1.0"
preset: web-interactive-e2e
project:
  name: zf-web-interactive-e2e
  state_dir: .zf
session:
  tmux_session: zf-web-interactive-e2e
  tmux_layout: pane_grid
orchestrator:
  backend: python
  permission_mode: bypass
workflow:
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
fi

cat >"$FAKE_CLAUDE" <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time

raw = sys.stdin.read()
prompt = raw.lower()
session_id = "fake-claude-kanban-e2e"

def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)

emit({"type": "system", "session_id": session_id})
emit({
    "type": "assistant",
    "session_id": session_id,
    "message": {
        "content": [
            {"type": "thinking", "text": "redacted private reasoning"},
            {"type": "text", "text": "Inspecting Project state and preparing a bounded response."},
            {"type": "tool_use", "name": "read_project_projection", "input": {"scope": "kanban"}},
        ],
    },
})
time.sleep(0.05)

if "task proposal" in prompt or "整理成" in prompt or "create-task" in prompt:
    result = {
        "action_proposal": {
            "action": "create-task",
            "payload": {
                "title": "Fix Channel Group interactive E2E gap",
                "priority": 2,
                "contract": {
                    "behavior": "Cover Channel Group and Kanban Agent real interaction flows.",
                    "verification": "Docker Playwright interactive audit passes.",
                    "acceptance": "No runtime truth mutates before token-gated confirmation.",
                },
            },
            "reason": "The requested work should be tracked as a controlled ZaoFu task.",
        }
    }
    emit({
        "type": "result",
        "session_id": session_id,
        "result": json.dumps(result, ensure_ascii=False),
        "usage": {"input_tokens": 42, "output_tokens": 24},
    })
else:
    emit({
        "type": "result",
        "session_id": session_id,
        "result": "ZF_KANBAN_AGENT_FAKE_OK: I can see the current Project projection and will not mutate task truth without a confirmed action.",
        "usage": {"input_tokens": 39, "output_tokens": 18},
    })
PY
chmod +x "$FAKE_CLAUDE"

cp "$ROOT/tests/e2e/scripts/web_interactive_e2e_audit.spec.ts" "$SPEC_FILE"
cat >"$PLAYWRIGHT_CONFIG" <<'JS'
const { defineConfig, devices } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "/zf-run",
  timeout: 30_000,
  expect: {
    timeout: 7_500,
  },
  outputDir: "/zf-run/test-results",
  use: {
    baseURL: process.env.ZF_WEB_BASE_URL ?? "http://127.0.0.1:8001",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
JS

chmod -R a+rwX "$RUN_ROOT"

echo "# ZaoFu Web Interactive E2E Audit" >"$REPORT"
{
  echo
  echo "- run_root: $RUN_ROOT"
  echo "- project_root: $PROJECT_ROOT"
  echo "- state_dir: $STATE_DIR"
  echo "- api: http://127.0.0.1:$API_PORT"
  echo "- web: http://127.0.0.1:$WEB_PORT"
  echo "- token: $TOKEN"
  echo "- real_provider: $REAL_PROVIDER"
  echo "- codex_sandbox: $CODEX_SANDBOX"
  echo "- codex_approval_policy: $CODEX_APPROVAL_POLICY"
  echo "- source_project_dir: ${SOURCE_PROJECT_DIR:-none}"
  echo "- source_zf_yaml: ${SOURCE_ZF_YAML:-none}"
  echo "- prompt: prompt/zaofu-web-interactive-e2e-audit.md"
} >>"$REPORT"

if [[ "$SKIP_UNIT" -eq 0 ]]; then
  echo "[preflight] npm typecheck"
  npm --prefix web run typecheck
  echo "[preflight] npm event bus test"
  npm --prefix web run test:event-bus
  echo "[preflight] focused pytest"
  pytest_targets=(
    tests/test_channel_projection.py
    tests/test_channel_router.py
    tests/test_workspace_projects.py
    tests/test_web_headless_agent.py
    tests/test_web_server.py
  )
  if [[ -f tests/test_operator_reliability_projection.py ]]; then
    pytest_targets+=(tests/test_operator_reliability_projection.py)
  fi
  PYTHONPATH="$ROOT/src" pytest -q "${pytest_targets[@]}"
fi

echo "[setup] initialize temp project: $PROJECT_ROOT"
(
  cd "$PROJECT_ROOT"
  PYTHONPATH="$ROOT/src" python3 -m zf.cli.main init --no-workspace-register --force
)

API_PID=""
WEB_PID=""
cleanup_processes() {
  if [[ -n "$WEB_PID" ]]; then kill "$WEB_PID" >/dev/null 2>&1 || true; fi
  if [[ -n "$API_PID" ]]; then kill "$API_PID" >/dev/null 2>&1 || true; fi
}
trap cleanup_processes EXIT

echo "[serve] zf web http://0.0.0.0:$API_PORT"
(
  cd "$PROJECT_ROOT"
  ZF_WEB_ACTION_TOKEN="$TOKEN" \
  ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD="python3 $FAKE_CLAUDE" \
  ZF_KANBAN_AGENT_HEADLESS_SYNC=1 \
  ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S=240 \
  ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY="$CODEX_APPROVAL_POLICY" \
  ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX="$CODEX_SANDBOX" \
  PYTHONPATH="$ROOT/src" \
  python3 -m zf.cli.main web --host 0.0.0.0 --port "$API_PORT" --state-dir "$STATE_DIR"
) >"$RUN_ROOT/api.log" 2>&1 &
API_PID="$!"

echo "[serve] vite http://0.0.0.0:$WEB_PORT"
(
  cd "$ROOT"
  ZF_API_TARGET="http://127.0.0.1:$API_PORT" \
  npm --prefix web run dev -- --host 0.0.0.0 --port "$WEB_PORT"
) >"$RUN_ROOT/vite.log" 2>&1 &
WEB_PID="$!"

wait_url() {
  local url="$1"
  local name="$2"
  for _ in $(seq 1 120); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "[ready] $name $url"
      return 0
    fi
    sleep 1
  done
  echo "[error] timeout waiting for $name $url" >&2
  echo "--- api.log ---" >&2
  tail -80 "$RUN_ROOT/api.log" >&2 || true
  echo "--- vite.log ---" >&2
  tail -80 "$RUN_ROOT/vite.log" >&2 || true
  return 1
}

wait_url "http://127.0.0.1:$API_PORT/api/snapshot" "api"
wait_url "http://127.0.0.1:$WEB_PORT/" "vite"

if [[ "$SKIP_DOCKER" -eq 0 ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required for Playwright E2E; rerun with --skip-docker to only start services" >&2
    exit 2
  fi
  echo "[playwright] Docker image $DOCKER_IMAGE"
  set +e
  docker run --rm --network host \
    -v "$ROOT":/work \
    -v "$RUN_ROOT":/zf-run \
    -w /work/web \
    -e ZF_WEB_BASE_URL="http://127.0.0.1:$WEB_PORT" \
    -e ZF_WEB_ACTION_TOKEN_FOR_TEST="$TOKEN" \
    -e ZF_E2E_CODEX_HEADLESS="$([[ "$REAL_PROVIDER" == "codex" ]] && echo 1 || echo 0)" \
    -e NODE_PATH=/work/web/node_modules \
    -e PLAYWRIGHT_BROWSERS_PATH=/tmp/ms-playwright \
    --entrypoint /bin/bash \
    "$DOCKER_IMAGE" \
    -lc "npx playwright install chromium >/dev/null && npx playwright test /zf-run/web-interactive-audit.spec.ts --config /zf-run/playwright.config.cjs --project=chromium --workers=1 --reporter=line" \
    | tee "$RUN_ROOT/playwright.log"
  playwright_status=${PIPESTATUS[0]}
  set -e
  if [[ "$playwright_status" -ne 0 ]]; then
    if grep -Eqi "sandbox_unsupported|unshare|Operation not permitted" "$RUN_ROOT/api.log" "$RUN_ROOT/playwright.log" 2>/dev/null; then
      echo "environment_sandbox_unsupported: Codex sandbox '$CODEX_SANDBOX' is not supported by this host" >&2
      {
        echo
        echo "## Failure Classification"
        echo "- environment_sandbox_unsupported: Codex sandbox \`$CODEX_SANDBOX\` is not supported by this host."
      } >>"$REPORT"
    fi
    exit "$playwright_status"
  fi
fi

echo "[report] $REPORT"
{
  echo
  echo "## Result"
  if [[ "$SKIP_DOCKER" -eq 0 ]]; then
    echo "- Docker Playwright completed. See \`playwright.log\`."
  else
    echo "- Docker Playwright skipped."
  fi
  echo "- API log: \`api.log\`"
  echo "- Vite log: \`vite.log\`"
  echo "- Spec: \`web-interactive-audit.spec.ts\`"
} >>"$REPORT"

if [[ "$KEEP" -eq 0 ]]; then
  cleanup_processes
  if [[ "$SKIP_DOCKER" -eq 0 ]] && command -v docker >/dev/null 2>&1; then
    docker run --rm \
      -v "$RUN_ROOT":/zf-run \
      --entrypoint /bin/bash \
      "$DOCKER_IMAGE" \
      -lc "rm -rf /zf-run/test-results /zf-run/playwright-report" >/dev/null 2>&1 || true
  fi
  rm -rf "$RUN_ROOT"
else
  echo "[kept] $RUN_ROOT"
fi
