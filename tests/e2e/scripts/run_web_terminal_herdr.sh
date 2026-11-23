#!/usr/bin/env bash
# Run one Docker Chromium journey against a fresh project and a real Dashboard.
set -euo pipefail

root=$(cd "$(dirname "$0")/../../.." && pwd)
web="$root/web"
tier=${1:-fixture}
binary="$root/tests/fixtures/fake-herdr"
spec=tests/web-terminal-herdr095.spec.ts
filter=()
unset ZF_WEB_HERDR_HOST_SMOKE ZF_WEB_TRUSTED_SESSION ZF_WEB_PASSCODE HERDR_SOCKET_PATH HERDR_SESSION
case "$tier" in
  fixture) filter=(-g "survives uncertainty") ;;
  legacy) spec=tests/web-terminal.spec.ts ;;
  host)
    binary=$(realpath "${2:?provide the Herdr 0.9.5 binary}")
    export ZF_WEB_HERDR_HOST_SMOKE=1
    filter=(-g "official Herdr")
    ;;
  *) echo "Usage: $0 {fixture|legacy|host /path/to/herdr}" >&2; exit 2 ;;
esac
test -x "$binary" && test -f "$web/dist/index.html" && test -x "$web/node_modules/.bin/playwright" || exit 2

run=$(mktemp -d "/tmp/zf-herdr-$(date -u +%Y%m%dT%H%M%S)-XXXX")
project="$run/project"
state="$project/state"
export ZF_WEB_TERMINAL_FIXTURE="$run" ZF_WORKSPACE_HOME="$run/workspace"
export CODEX_HOME="$run/codex" ZF_FAKE_HERDR_STATE_DIR="$run/fake"
export XDG_CONFIG_HOME="$run/config" XDG_STATE_HOME="$run/state" XDG_DATA_HOME="$run/data" XDG_RUNTIME_DIR="$run/run"
export ZF_WEB_ACTION_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
export ZF_WEB_ACTION_TOKEN_FOR_TEST="$ZF_WEB_ACTION_TOKEN"
export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$state" "$ZF_FAKE_HERDR_STATE_DIR"
mkdir -m 700 "$XDG_RUNTIME_DIR"
api_pid=
cleanup() {
  if [[ -n "$api_pid" ]]; then kill "$api_pid" 2>/dev/null || true; wait "$api_pid" || true; fi
  if [[ "$tier" == host ]]; then
    # Herdr stores the session names under the fixture's private config directory.
    for session in "$XDG_CONFIG_HOME"/herdr/sessions/zf-*; do
      [[ -d "$session" ]] || continue
      name=$(basename "$session")
      "$binary" --session "$name" server stop >/dev/null 2>&1 || true
    done
  fi
  rm -rf -- "$run"
}
trap cleanup EXIT
cat > "$project/zf.yaml" <<YAML
version: "1.0"
project:
  name: herdr-browser-test
  state_dir: state
orchestrator:
  backend: codex
runtime:
  web_terminal:
    herdr_binary: $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$binary")
    provider_start_timeout_seconds: 12
YAML
printf '[]\n' > "$state/kanban.json"
printf '[]\n' > "$state/feature_list.json"
touch "$state/events.jsonl"
port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("0.0.0.0", 0)); print(s.getsockname()[1])')
export ZF_WEB_BASE_URL="http://127.0.0.1:$port"
(cd "$project" && exec zf web --state-dir "$state" --host 0.0.0.0 --port "$port") >"$run/dashboard.log" 2>&1 &
api_pid=$!
for i in {1..100}; do
  kill -0 "$api_pid" 2>/dev/null || { sed -E 's/(zf-action-token=)[^ ]+/\1***/g' "$run/dashboard.log" >&2; exit 1; }
  if curl -fsS "$ZF_WEB_BASE_URL/" -o /dev/null 2>/dev/null; then break; fi
  sleep 0.2
done
docker run --rm --network host --ipc=host --user "$(id -u):$(id -g)" \
  -v "$web:/work/web:ro" -v "$run:$run" -w /work/web \
  -e ZF_WEB_TERMINAL_FIXTURE -e ZF_WEB_HERDR_HOST_SMOKE -e ZF_WEB_ACTION_TOKEN_FOR_TEST -e ZF_WEB_BASE_URL \
  --entrypoint bash mcp/playwright:latest -ec '
    chromium=(/ms-playwright/chromium-*/chrome-linux*/chrome)
    export ZF_E2E_CHROMIUM_EXECUTABLE_PATH="${chromium[0]}"
    exec ./node_modules/.bin/playwright test "$@" --workers=1 --output=/tmp/zf-browser-result
  ' bash "$spec" "${filter[@]}"
