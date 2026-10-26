# Plan Digest — evt-746215246543

task_map_ref: `artifacts/fanouts/fanout-prd-plan-evt-982e731b/prd-planner/artifacts/plan/task_map.json`

## 任务表

| task_id | owner_role | wave | affinity | 根owner | allowed_paths | verification |
|---|---|---|---|---|---|---|
| RACE-FOUNDATION | - | 1 | foundation | ✓ | package.json, package-lock.json, tsconfig.json, vite.config.ts … | node scripts/racing-runner/unit.mjs scaffold; bash scripts/r |
| RACE-DRIVE | - | 2 | vehicle-race | - | src/race/**, tests/race/**, docs/racing/drive.md, artifacts/racing/race/** | node scripts/racing-runner/unit.mjs race; bash scripts/racin |
| RACE-TRACK | - | 2 | track-collision | - | src/track/**, tests/track/**, docs/racing/track.md, artifacts/racing/track/** | node scripts/racing-runner/unit.mjs track; bash scripts/raci |
| RACE-ASSEMBLY | - | 3 | experience-assembly | ✓ | index.html, src/main.ts, src/ui/**, public/** … | node scripts/racing-runner/unit.mjs assembly; env PLAYWRIGHT |

## Checklist（机械预检）

- 🟢 **assembly/根 owner 在位** — RACE-ASSEMBLY
- 🔴 **根文件唯一 owner** — 多任务持根路径: RACE-FOUNDATION, RACE-ASSEMBLY
- 🟢 **每任务有 verification** — 齐
- 🟢 **allowed_paths 无重叠** — 无重叠
