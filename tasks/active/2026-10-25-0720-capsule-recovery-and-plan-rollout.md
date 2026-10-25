# 任务上下文同步、恢复与 loshu-fms 计划切换

> 状态: active

用户已批准：修复并验证派发/恢复逻辑，更新 Dashboard，提交新 Plan Preview，经受控路径恢复并核实 Impl。

## 实施与验收

- [x] 复现 Plan 刷新后 Task Capsule 陈旧 -> verify: 真正 Task Pipeline 派发入口的回归测试先失败。
- [x] 派发前同步并校验当前任务投影，保留合同权威 -> verify: 新旧代际、首次派发、重试与失败关闭测试通过。
- [x] 将可机械识别的上下文故障与语义合同缺口区分 -> verify: 上下文故障不进入产品重规划；真实 upstream gap 保持原路由。
- [x] 影响闭包测试、sentinel、确定性 mock E2E -> verify: 记录准确命令与结果，不用单测冒充真实 E2E。
- [ ] 文档与实现提交 -> verify: 显式路径提交，保留 dev 未关联 index.md 修改。
- [x] 更新 Dashboard 构建与浏览器验证 -> verify: 最新审核阅读层实际呈现。
- [ ] 受控提交 38 任务候选并审核接纳 -> verify: 新 Plan/Package/generation 收据，保留旧审计证据。
- [ ] 加载修复并恢复 -> verify: 当前 Task Capsule/snapshot/briefing 同版、真实 Impl 新派发与 worker 进展；不绕过 Verify/Judge。

## 基线

dev=83b84518；工作树 zaofu-capsule-recovery。产品 run=workflow-4fbf85bbad5fb749；07:06 UTC 已 blocked。候选位于 loshu-fms/docs/plans/parallel-taskmap-revision-20261025/candidate。

## 修复/验证记录

- 5 个新增派发回归在修复前均失败，复现缺失/旧投影、快照冲突、写入失败和选择后合同变化。
- 修复 publish_pipeline_capsule：先核 canonical Task 对 snapshot，再发布/校验说明投影；不修改合同。
- task_context_stale 明确进入 harness 修复分支，真正上游缺口保留重规划。
- 17 个影响范围测试文件：308 passed；另有真实 briefing + mock transport 派发入口检查通过。
- dev-verify plan 选择 231 文件并升级全量；遵循 AGENTS 分层要求，使用上述精确回归替代该全量/无关 serial 档。dev-verify run --skip-tier deterministic --skip-tier serial 继续执行 sentinel 与 flow smoke。
- Dashboard npm run build 通过；Docker mcp/playwright:latest 在真实 8001 确认审批概览/模块规格/验收场景/完整原文均存在，page_errors=[]。证据：zaofu-evidence/capsule-recovery-20261025。
- 说明：[Task Pipeline 上下文发布与恢复](../../docs/impl/task-pipeline-capsule-recovery.md)。

运行时接纳/恢复仍待执行，任务保持 active。

- dev-verify run 的实际自动档：premerge-sentinels 51 passed；三流 flow-smoke 10 passed。未执行全量 pytest。
