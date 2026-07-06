#!/usr/bin/env bash
set -euo pipefail

# Real-provider smoke for examples/prod/new.
#
# This script intentionally consumes provider tokens. It creates a tiny Node.js
# product under /tmp, then runs PRD -> issue -> refactor with the production
# templates in examples/prod/new. It records a compact report and stops tmux
# sessions after each workflow.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
ZF_BIN="${ZF_BIN:-uv --project "$ROOT" run zf}"
BACKEND="${ZF_AGENT_BACKEND:-codex}"
RUN_MANAGER_BACKEND="${ZF_RUN_MANAGER_BACKEND:-$BACKEND}"
STAMP="${ZF_E2E_RUN_TAG:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_ROOT="${ZF_E2E_ROOT:-/tmp/zf-prod-new-three-workflow-$STAMP}"
PRODUCT="$RUN_ROOT/product"
REPORT="$RUN_ROOT/report.json"
TIMEOUT_SECONDS="${ZF_E2E_TIMEOUT_SECONDS:-3600}"

mkdir -p "$PRODUCT"
cd "$PRODUCT"

if [[ ! -d .git ]]; then
  git init -q
  git config user.email "zaofu-e2e@example.com"
  git config user.name "ZaoFu E2E"
  cat > package.json <<'JSON'
{"scripts":{"test":"node --test"},"dependencies":{},"devDependencies":{}}
JSON
  cat > server.mjs <<'JS'
export function health() {
  return { ok: true, service: "product-pulse-seed", version: "0.0.0" };
}
JS
  mkdir -p tests
  cat > tests/server.test.mjs <<'JS'
import test from "node:test";
import assert from "node:assert/strict";
import { health } from "../server.mjs";

test("seed health", () => {
  assert.equal(health().ok, true);
});
JS
  git add package.json server.mjs tests/server.test.mjs
  git commit -q -m "chore: seed product pulse e2e baseline"
fi

json_count() {
  local state_dir="$1"
  local event_type="$2"
  python3 - "$state_dir/events.jsonl" "$event_type" <<'PY'
import json, sys
path, event_type = sys.argv[1], sys.argv[2]
count = 0
try:
    fh = open(path, encoding="utf-8")
except FileNotFoundError:
    print(0)
    raise SystemExit
for line in fh:
    try:
        event = json.loads(line)
    except Exception:
        continue
    if event.get("type") == event_type:
        count += 1
print(count)
PY
}

latest_payload_field() {
  local state_dir="$1"
  local event_type="$2"
  local field="$3"
  python3 - "$state_dir/events.jsonl" "$event_type" "$field" <<'PY'
import json, sys
path, event_type, field = sys.argv[1], sys.argv[2], sys.argv[3]
value = ""
try:
    lines = open(path, encoding="utf-8")
except FileNotFoundError:
    print("")
    raise SystemExit
for line in lines:
    try:
        event = json.loads(line)
    except Exception:
        continue
    if event.get("type") != event_type:
        continue
    payload = event.get("payload") or {}
    value = str(payload.get(field) or "")
print(value)
PY
}

wait_for_event() {
  local state_dir="$1"
  local event_type="$2"
  local deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if [[ "$(json_count "$state_dir" "$event_type")" != "0" ]]; then
      return 0
    fi
    sleep 10
  done
  echo "timeout waiting for $event_type in $state_dir" >&2
  return 1
}

append_report() {
  local name="$1"
  local state_dir="$2"
  python3 - "$REPORT" "$name" "$state_dir" <<'PY'
import json, sys
report, name, state_dir = sys.argv[1], sys.argv[2], sys.argv[3]
events_path = f"{state_dir}/events.jsonl"
types = [
    "task_map.ready", "dev.build.done", "review.approved", "test.passed",
    "judge.passed", "run.completed", "human.escalate",
    "workflow.resume.rejected", "autoresearch.trigger.accepted",
    "supervisor.decision.recorded",
]
counts = {key: 0 for key in types}
try:
    lines = open(events_path, encoding="utf-8")
except FileNotFoundError:
    lines = []
for line in lines:
    try:
        event = json.loads(line)
    except Exception:
        continue
    typ = event.get("type")
    if typ in counts:
        counts[typ] += 1
try:
    data = json.load(open(report, encoding="utf-8"))
except Exception:
    data = {"schema_version": "prod-new-three-workflow-e2e.v1", "runs": []}
data["runs"].append({"name": name, "state_dir": state_dir, "counts": counts})
open(report, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
PY
}

run_workflow() {
  local name="$1"
  local yaml="$2"
  local state_name="$3"
  local session_name="$4"
  local event_type="$5"
  local payload_file="$6"
  cp "$yaml" zf.yaml
  local state_dir="$PRODUCT/$state_name"
  export ZF_PROJECT_NAME="prod-new-$name-$STAMP"
  export ZF_STATE_DIR="$state_dir"
  export ZF_TMUX_SESSION="$session_name"
  export ZF_AGENT_BACKEND="$BACKEND"
  export ZF_RUN_MANAGER_BACKEND="$RUN_MANAGER_BACKEND"
  export ZF_RUN_MANAGER_REFLECT_BACKEND="$RUN_MANAGER_BACKEND"
  export ZF_RUN_MANAGER_RESIDENT_ENABLED="${ZF_RUN_MANAGER_RESIDENT_ENABLED:-true}"
  export ZF_GLOBAL_BUDGET_USD="${ZF_GLOBAL_BUDGET_USD:-900}"
  $ZF_BIN init
  $ZF_BIN start
  $ZF_BIN emit "$event_type" --payload-file "$payload_file"
  wait_for_event "$state_dir" run.completed
  append_report "$name" "$state_dir"
  local candidate
  candidate="$(latest_payload_field "$state_dir" candidate.ready candidate_ref)"
  if [[ -n "$candidate" ]]; then
    git merge --ff-only "$candidate"
  fi
  npm test
  $ZF_BIN stop --include-run-manager || true
}

cat > "$RUN_ROOT/prd-request.json" <<JSON
{
  "text": "请基于当前 Node.js baseline 构建一个极小的 Product Pulse 产品: 1) /health 返回 {ok:true, service:'product-pulse', version:string}; 2) 新增 /api/pulse 返回最近 3 条产品状态 items,每条包含 id/title/status/updatedAt; 3) 首页展示 Product Pulse 标题和这 3 条状态; 4) 保留 npm test,增加覆盖 /health、/api/pulse、首页标题和状态渲染的 node:test 测试; 5) 代码保持轻量,不要引入外部依赖。请走完整 PRD->task_map->impl->verify->judge 流程,产出可运行产品。",
  "objective": "build minimal Product Pulse product from PRD to production-ready candidate",
  "run_tag": "prod-new-prd-$STAMP",
  "source_commit": "$(git rev-parse HEAD)"
}
JSON
run_workflow prd "$ROOT/examples/prod/new/prd-fanout-v2.yaml" ".zf-prod-new-prd-$STAMP" "zf-prod-new-prd-$STAMP" user.message "$RUN_ROOT/prd-request.json"

cat > "$RUN_ROOT/issue-request.json" <<JSON
{
  "text": "Issue: /api/pulse currently ignores query parameters. Add support for GET /api/pulse?status=<status> so it returns only items with an exact matching status while the default /api/pulse still returns all three items newest-first. Add node:test coverage for filtered and unmatched status behavior. Keep no external dependencies and preserve existing Product Pulse behavior.",
  "objective": "fix Product Pulse API status filtering regression with tests",
  "run_tag": "prod-new-issue-$STAMP",
  "source_commit": "$(git rev-parse HEAD)"
}
JSON
run_workflow issue "$ROOT/examples/prod/new/issue-fanout-v2.yaml" ".zf-prod-new-issue-$STAMP" "zf-prod-new-issue-$STAMP" user.message "$RUN_ROOT/issue-request.json"

cat > "$RUN_ROOT/refactor-request.json" <<JSON
{
  "pdd_id": "prod-new-refactor-$STAMP",
  "feature_id": "product-pulse-server-structure",
  "target_ref": "HEAD",
  "source_commit": "$(git rev-parse HEAD)",
  "run_tag": "prod-new-refactor-$STAMP",
  "objective": "Refactor Product Pulse server internals without changing behavior. Separate pulse data access/filtering and HTML rendering into small pure functions inside the existing no-dependency Node.js project. Preserve /health, /api/pulse, /api/pulse?status=<status>, homepage rendering, package metadata, and npm test results. Keep changes small and covered by node:test."
}
JSON
run_workflow refactor "$ROOT/examples/prod/new/refactor-lane-v2.yaml" ".zf-prod-new-refactor-$STAMP" "zf-prod-new-refactor-$STAMP" refactor.scan.requested "$RUN_ROOT/refactor-request.json"

echo "report: $REPORT"
