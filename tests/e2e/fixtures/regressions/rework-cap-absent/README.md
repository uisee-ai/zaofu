# Regression fixture: rework cap absent at CLI gate (B-REWORK-CAP-01)

- **Incident**: v2 live smoke 2026-04-21 14:14 stalled for 17 min after 2 × `test.failed`.
- **Archive**: `backlogs/experiment-artifacts/b1-smoke-v2-20260421-1358-STALLED-rework-cap-absent/`
- **Root cause**: Existing `_dispatch_rework` in `src/zf/runtime/orchestrator_dispatch.py:426-430`
  checked `task.retry_count > max_attempts`, but this only fired on the Python
  legacy dispatch path (A-flow, no orchestrator role). In v4.0 three-layer
  architecture with an orchestrator role, Layer 2 calls `zf kanban assign dev`
  (CLI) directly, bypassing the Python check. The CLI gate `_validate_transition`
  had no corresponding rework-cap check, so Layer 2 could keep re-assigning dev
  indefinitely (or stall while deciding, as in this incident).
- **Fix commit**: B-REWORK-CAP-01 — added rework-cap check to
  `src/zf/cli/kanban.py _validate_transition`. When a task has accumulated
  `>= max_rework_attempts` fail events (test.failed/review.rejected/judge.failed),
  `assign dev` is refused and `task.rework.capped` is emitted.
- **Cost to discover**: $2.39 (v2 live smoke, 21 minutes wall clock)

## What this fixture guards

Replaying this `events.jsonl` against a config where `max_rework_attempts = 2`
with `_validate_transition("TASK-79A4E5", "dev", "assign", ...)` must:

1. Count `test.failed` + `review.rejected` + `judge.failed` events for the task
2. Return `(False, "rework.cap:N/2")` when the count reaches or exceeds the cap
3. Trigger emission of `task.rework.capped` (not `task.invalid_transition`)

## Files

- `events.jsonl` — 490 lines, contains 2 × `test.failed` for `TASK-79A4E5`
  (Layer 2 never progressed past this point because cap was missing at gate).
- See `tests/test_rework_cap.py` for the replay test.
