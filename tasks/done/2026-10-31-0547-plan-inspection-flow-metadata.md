> 状态: done

实现：`eb69d3f8 fix: inspect single-flow execution metadata consistently`。

# Plan 检查 CLI 对齐运行时 Flow 元数据读取

监工审查 dl 的正式 92 任务 Plan 时，`plan inspect-execution` 报 task_pipeline=off，
而当前 PrdFlow 明确 blocking，运行时 `flow_metadata_for(config, 'prd')` 也为 blocking。
单一 Flow 编译后保留 flow_metadata，CLI 仅读 flow_metadata_by_kind，导致假阴性。
Owner 已授权记录并修复运行中发现的问题。

- CLI 沿用运行时元数据 helper -> verify: 单 Flow 的真实 CLI 回归返回 blocking / 独立 Task Verify。
- 保持多 Flow 选择及只读边界 -> verify: metadata helper、Plan review/CLI/预检影响闭包，不创建状态目录。

说明：[Planner 检查与恢复](../../docs/impl/planner-context-and-operation-recovery.md)。
原 HEAD 的真实 CLI 回归返回 off；运行时 helper 在同一 dl 配置返回 blocking。

验证：Plan review/预检 focused 53 passed；dev-verify 自动 CLI/审批调用闭包 31 passed；
ruff、diff check 通过。首次 dev-verify 误用了未安装 pytest-cov 的系统 Python，
已改用仓库 .venv 完整依赖解释器重跑通过。收据：
`/tmp/zf-plan-inspection-verification-20261031.json`。
