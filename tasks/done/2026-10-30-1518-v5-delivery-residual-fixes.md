> 状态: done — `bb58f246 fix: close v5 delivery gaps and preserve live graph progress`

# V5 真实交付残余问题修复

用户已于本轮明确批准在 `wip/codex-20261030-v5-progress-liveness` 实施。
基线 `ea24ce4c`；保留本 worktree 已验证的无固定期限/活性检测改动，一并验证提交。
证据：[前轮记录](../../docs/records/2026-10-30-v5-unlimited-execution-liveness.md)，
原始证据 `/tmp/zf-v5-liveness-20261030-140230/evidence/`。
不合并、不推送 dev，不修改 8001。运行态只经规范 CLI/事件/受控动作。

## 验收条目

- [x] B01 Discovery 输入范围：复现父任务缺少 task-scoped execution_spec 分区 -> verify: 真实 handoff 调用者按已校验的 run/candidate 范围交付完整计划，Task Impl/Verify 继续限制本任务分区，错代/错 digest 拒绝。
- [x] B02 活跃 Graph 可用性：复现连续事件追加导致 503/409 -> verify: 图按可解释事件边界读取，事件持续追加仍返回一致快照；侧文件修改/损坏、配置和权限变化不得返回假成功；浏览器覆盖。
- [x] B03 原始输入溯源：复现 Kanban Create task 未携带原始消息 -> verify: 真正 UI/API 路径绑定用户消息及项目/会话，critical context 可恢复原文；跨项目/伪造来源拒绝。
- [x] B04 Provider 消费见证：复现 Codex 嵌套/JSON 转义输出无法匹配 -> verify: 支持实际输出包装，精确核对正文、operation/attempt 与 digest；截断/错误/未交付继续 unknown，不降低结果门禁。
- [x] B05 重复读取与负载：分析前轮调用/读取证据，限定真实重复与热路径 -> verify: 减少可复现重复读取/图构建开销，保留完整 8-lane 与所有门禁；新增方法写 skill，确定性优化写代码；报告可比测量，不承诺取消成本约束。
- [x] B06 文本误脱敏：复现内联 token_budget=0 吞掉后续文本 -> verify: 已知数值预算字段可读，真实凭据和非数值可疑字段仍脱敏。
- [x] B07 组合验证：覆盖已改活性逻辑与本批边界 -> verify: 模块/调用者/共享合同定向测试、哨兵、mock E2E；Docker Playwright 真实入口复测；独立核验产品、阶段终态、临时环境清理。真实恢复/人工审批及 Agent 编辑按证据单列，不用 mock 冒充。

## 实施与记录

先写可复现回归并观察失败，再做最小修复。真实 Provider 沿用用户已授权的最小任务验证，
不重置失败 Run 或伪造结果。宿主资源问题与产品缺陷分开记录；新发现超出本批问题保留证据。
完成后记录确切测试、实现 commit 和 archive commit，再归档。

## 本轮实施进度

B01–B07 已实施、验证并完成真实 Provider 交付。
运行时 249 passed，活性/预算 197 passed，Graph/API 初轮 33 passed，追加并发回归后 34 passed，哨兵 51 passed，
flow smoke 10 passed，host mock 1 passed，Web typecheck/unit/build/bundle 通过。
补充嵌套见证边界 88 passed；Docker Playwright Graph 40 passed。
真实 Run 已通过 Scan/Plan/Impl/Task Verify/8-lane Verify/Discovery/Judge，
Kernel 已 ship 至临时项目 main 并发出 run.goal.completed；独立交付检查通过，临时环境已清理。
技能 canonical 与两份分发副本字节一致。
详见 [实施记录](../../docs/records/2026-10-30-v5-delivery-residual-fixes.md)。

## 完成记录

实现提交：`bb58f246 fix: close v5 delivery gaps and preserve live graph progress`。
实现后完整工作区审计无残留；B01–B07 验收证据及真实终态见实施记录。
本批只在当前 worktree 提交与归档，未合并或推送 dev。
