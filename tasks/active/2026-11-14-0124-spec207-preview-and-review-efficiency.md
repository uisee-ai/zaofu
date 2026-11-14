> 状态: active

# 207 后续：审批一致性、阅读优先级与增量收益

用户批准执行复评建议 1–3；原 worktree，基线 ceecedc9。沿用设计 207。

- [x] 摘要责任一致性 -> verify: 单 owner 串行任务不因缺专门 assembly 误报；并行 bundle 缺 assembly 仍报错，使用准入同源拓扑规则，实际旧样本复测。
- [x] 审批阅读优先级 -> verify: 默认页优先交付、变更和待决定；工程来源折叠、完整原文不变；类型检查、构建与 Docker 浏览器回归。
- [x] 增量收益 -> verify: 保留资格与证据检查；完整计量新旧分区，未知或无字节收益回退全量；实际派发/重放调用者回归和旧真实样本复算；依赖裁剪由 Skill 指导，不机械删边或放松门禁。

验证层级：定向模块/调用者/契约、sentinel 与相关 mock E2E；真实产物回放和浏览器分别记录，不冒充新 Provider 性能对照。实现后提交及归档，不 merge/push。


## 已验收结果

- 定向首批 117 passed、调用者/共享合同 248 passed，最后 digest/CLI/计量 31 passed；批次重叠，不合计。
- sentinel 53、相关 mock E2E 10 passed；Web build/typecheck/bundle budget 和 4 个模型测试文件通过；未跑 full pytest。
- Docker 浏览器 39 passed；之后 statement 字段展示补修经模型单测、最终 build 和真实工件 API/browser 覆盖。
- B02 原真实派发回放：范围资格仍 verified，scoped 442000 bytes、full 415494 bytes，选择 full，unknown=0；原 ledger 未改变。
- 同一真实 Plan 的当前摘要无 assembly 误报；审批首屏交付/变化/决定优先，来源可展开，完整原文与 API 一致。
- 首次浏览器 33 passed/6 failed 的计数、重复章节和新折叠/版本定位问题已修正复测；失败记录保留。

限制：没有新 Provider 端到端或多样本延迟对照；依赖简化已落到方法和分发，未修改
旧 Package 的任务图。减少 26506 选中字节不等于模型 token、费用或耗时收益。

本地 [报告](../../reports/spec207-followup/evaluation.md) 和 handoff 保存具体日志、
源码身份、截图与清理证据。临时 API/浏览器已停止，simulation.done 经 CLI 发出，
本轮临时目录已清理；8001 未使用。未 merge/push。
