# Regression fixture: B1 kernel gate (B-KERNEL-GATE-01)

- **Incident**: `backlogs/experiment-artifacts/b1-smoke-20260421-1211-FAIL-kernel-bug/INCIDENT.md`
- **Root cause**: `src/zf/cli/kanban.py` `_ASSIGN_PRED` hardcoded A-flow predecessors; ignored `zf.yaml roles[].triggers`. B1 preset's `dev.build.done → test` assign was rejected with `missing: "review.approved"`.
- **Fix commit**: B-KERNEL-GATE-01 — introduce `_derive_assign_pred(config_roles)` that derives from `role.triggers` and falls back to `_ASSIGN_PRED` only when triggers are missing.
- **Cost to discover**: $0.0155 (Layer 2 Claude caught it in 5 rounds)

## What this fixture guards

Replaying this `events.jsonl` against `_derive_assign_pred` + `_validate_transition` with the **e2e-full (B1) config** must:

1. NOT emit a new `task.invalid_transition` with `target="test", missing="review.approved"` (the original bug)
2. Allow `assign test` after `dev.build.done` when config has `test.triggers = [dev.build.done]`

## Files

- `events.jsonl` — complete event stream from the failed run (117 lines, cost $0.0155)
- See `tests/test_kanban_gate_b1_regression.py` for the replay test.
