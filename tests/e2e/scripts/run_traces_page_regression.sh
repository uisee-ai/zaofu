#!/usr/bin/env bash

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
BASE_URL="${ZF_WEB_BASE_URL:-http://127.0.0.1:8001}"
RUN_ROOT="${ZF_TRACES_RUN_ROOT:-$(mktemp -d /tmp/zf-traces-page-regression-XXXXXX)}"
IMAGE="${ZF_PLAYWRIGHT_IMAGE:-mcp/playwright:latest}"

mkdir -p "$RUN_ROOT"
trap 'status=$?; if [[ "${ZF_TRACES_KEEP:-0}" != "1" ]]; then rm -rf "$RUN_ROOT"; fi; exit "$status"' EXIT

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for the Traces page regression" >&2
  exit 2
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "missing Playwright image: $IMAGE" >&2
  exit 2
fi

docker run --rm --network host \
  --user "$(id -u):$(id -g)" \
  --entrypoint bash \
  -v "$ROOT:/work:ro" \
  -v "$RUN_ROOT:/zf-run" \
  -w /work/web \
  -e HOME=/tmp/zf-playwright-home \
  -e ZF_WEB_BASE_URL="$BASE_URL" \
  -e ZF_WEB_PROJECT_ID="${ZF_WEB_PROJECT_ID:-}" \
  "$IMAGE" \
  -lc 'set -euo pipefail; mkdir -p "$HOME"; chromium_path="$(find /ms-playwright -type f -path "*/chrome-linux64/chrome" 2>/dev/null | sort | tail -1 || true)"; test -n "$chromium_path"; export ZF_E2E_CHROMIUM_EXECUTABLE_PATH="$chromium_path"; ./node_modules/.bin/playwright test tests/traces-page.spec.ts --config playwright.config.ts --project=chromium --workers=1 --reporter=line --output=/zf-run/test-results' \
  2>&1 | tee "$RUN_ROOT/docker.log"

echo "Traces regression evidence: $RUN_ROOT"
