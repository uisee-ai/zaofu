# prod/new templates

These templates are the current production-facing examples for the three common
ZaoFu entry types:

- `prd-fanout-v2.yaml`: PRD / new product delivery.
- `issue-fanout-v2.yaml`: issue or bug-fix delivery.
- `refactor-lane-v2.yaml`: refactor delivery with scan, plan, lane impl,
  review, verify, and judge.

They intentionally keep the runtime control plane in existing primitives:

- `workflow.stages` remains the kernel contract.
- PRD and issue templates use explicit `fanout_reader` /
  `fanout_writer_scoped` stages.
- Writer and verifier dispatch use `workflow.affinity_lanes` with
  `fanout.assignment.strategy: affinity_stage_slots`, so one task can keep the
  same lane from impl to verify.
- Refactor uses the same low-level primitives instead of inventing a second
  controller path.
- Run Manager is enabled as a dedicated tmux resident by default, while source
  repair remains opt-in.

Useful overrides:

```bash
ZF_AGENT_BACKEND=codex
ZF_RUN_MANAGER_BACKEND=codex
ZF_RUN_MANAGER_SOURCE_REPAIR_ENABLED=false
ZF_PROJECT_NAME=my-prod-run
ZF_STATE_DIR=.zf-my-prod-run
ZF_TMUX_SESSION=zf-my-prod-run
```

For deterministic smoke validation, set `ZF_AGENT_BACKEND=mock` and run
`zf validate --path <template>`, `zf workflow inspect --config <template>`, and
`zf start --dry-run` from a temporary project that copied the selected template
to `zf.yaml`. Run Manager backends are intentionally limited to `codex` or
`claude-code`; do not set `ZF_RUN_MANAGER_BACKEND=mock`.

When validating from this repository checkout, use the real skill locations:

```bash
ZF_AGENT_SKILLS_DIR=/home/user/workspace/agent-skills/skills
ZF_ZAOFU_SKILLS_DIR=/path/to/zaofu/skills
```
