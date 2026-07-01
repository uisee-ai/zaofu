#!/usr/bin/env bash
# LH-6.T4: E1 invariant — agent.usage event count == cost.jsonl entries.
#
# We fixed the 2× double-count in commit 9c4daa1 (E1/E2). This guard
# prevents regression: if a future change reintroduces double-counting
# the autoresearch loop will flag it the next iteration.
set -euo pipefail

STATE_DIR="${1:-.zf}"
EVENTS="$STATE_DIR/events.jsonl"
COST="$STATE_DIR/cost.jsonl"

if [ ! -f "$EVENTS" ] || [ ! -f "$COST" ]; then
  # Not yet populated — don't fail on empty runs.
  exit 0
fi

events_count=$(python3 - "$EVENTS" <<'PY'
import json
import sys
from pathlib import Path

count = 0
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    if event.get("type") == "agent.usage":
        count += 1
print(count)
PY
)
cost_count=$(python3 - "$COST" <<'PY'
import sys
from pathlib import Path

count = 0
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if line.strip():
        count += 1
print(count)
PY
)

if [ "$events_count" != "$cost_count" ]; then
  echo "invariant_cost_equality: agent.usage=$events_count cost=$cost_count" >&2
  exit 1
fi
exit 0
