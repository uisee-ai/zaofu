> 状态: done — `71e447e2` fix: close failed provider turns without retrying policy refusals

# P1：Codex 终止错误未回收 Task Pipeline

dl 的 `DL-FOUNDATION` 独立 Verify 在 08:40:51 UTC 返回带 `cyber_policy` 错误的
`task_complete`。事件 `evt-c2fbd07fdd40` 却投影为 completed，operation 保持 running。
此前两项产品缺陷已有独立复现；不能将 provider 错误当成验收结果，也不能自动重试策略拒绝。
Owner 已授权监工记录、修复、验证并继续。现场正常停止 runtime，保留任务目录与证据。

- 修复错误投影和分类 → verify: 无错误正文泄露，结构化拒绝码不被文案关键词误分类。
- 注册实际回收入口 → verify: run_once 阻塞精确 operation/task，attempt 不可自动重试，重复回放幂等。
- 约束关联范围 → verify: 其他角色、旧 turn、晚于 close 的新 prompt、已终局 operation 均不受影响。
- 保留恢复边界 → verify: 连接故障沿既有 Run Manager 路径；fanout 不自动重试明确策略拒绝。
- 文档与回归 → verify: [实现说明](../../docs/impl/provider-turn-error-recovery.md)、影响闭包、sentinel 和 deterministic mock E2E。

基线 `f418662d`，原始回归 4 个行为断言失败（另有一个测试复制 API 错误已更正），
证据 `/tmp/zf-provider-turn-error-red-20261031.log`。不修改产品 Verify 裁决或运行态 JSON。

验证：25 文件影响闭包 460 passed；停止策略抽取后补测 74 passed；
`dev-verify run --base dev --skip-tier deterministic --no-parallel` 的 serial、51 sentinel、10 mock flow 均通过。
选择器因公共 reactor 调用图扩到 243 文件而建议全套，依 AGENTS.md 不执行无关全量；
以明确的 provider / attempt / operation / fanout / handler / event 合同闭包替代。
无 real-provider 重试；原服务端拒绝保留待处理。Ruff 与 diff check 通过。
