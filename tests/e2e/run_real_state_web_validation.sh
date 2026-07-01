#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_DIR="${1:-${ZF_STATE_DIR:-$ROOT/.zf}}"
OUT_DIR="${2:-${ZF_FULLSTACK_OUT_DIR:-$ROOT/.zf/full-stack-validation}}"

mkdir -p "$OUT_DIR"

PYTHONPATH="$ROOT/src" python3 -m tests.e2e.full_stack_validation \
  --repo-root "$ROOT" \
  --state-dir "$STATE_DIR" \
  --require-real-codex \
  --require-docker \
  --preflight-output "$OUT_DIR/preflight.json" \
  --output "$OUT_DIR/scorecard.json" \
  --markdown "$OUT_DIR/report.md"
