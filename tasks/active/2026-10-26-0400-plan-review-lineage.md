> 状态: proposed

# Plan review lineage and dedupe

## 问题
本轮第 13 代 Plan 已通过 Critic，但旧 pending plan 以相同 task ID 指纹占位，触发 `plan.minting.suppressed`，需要人工 reject + stage-retrigger 才恢复。Plan Preview bundle 也可能遗漏 authority refs。

## 范围
- Plan fingerprint 必须绑定 workflow run、task-map generation、plan package digest 或 requirement digest；同一 generation 仍保持幂等，新的 artifact generation 不得被旧 pending plan 拦截。
- Plan Preview bundle 直接收录 `authority_refs` 中可解析的 Markdown，并保留完整 provenance。
- 保持审批前不生成 canonical writer tasks、不启动 impl/verify。

## 验收
- step -> verify: 相同 run/generation/task map 的重复 trigger 仍产生 `plan.minting.suppressed`。
- step -> verify: 相同 task IDs 但不同 package/generation 的 trigger 可生成新的 `plan.approval.requested`。
- step -> verify: bundle 的 authority_refs 与 authority Markdown artifacts 均可读取并通过 digest 校验。
- step -> verify: `uv run pytest tests/test_writer_fanout_runtime.py tests/test_operator_plan_preview.py -q --no-cov` 通过。
