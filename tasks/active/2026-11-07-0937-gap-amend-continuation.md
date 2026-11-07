# 并行任务恢复增量准入修复

> 状态: active

授权：用户要求持续监工 LS-v1，分析修复阻塞并记录；沿用本会话开发、合并和受控恢复授权。

现场：FMS04 的 evt-c072b7f57a4b 补修在 evt-349e338519df 被 conflicting structured verification commands 拒绝。同一报告提供 validation.commands 对象目录和 verification.commands 本地字符串列表。另观察 gap 基线选择旧 6a47 包而运行时已准入 9a2c，需要证明不会覆盖其他任务的新版本。

## 实施与验收

- [x] 复现对象目录 + 字符串执行列表被误判冲突；只适配真正对象目录，保留严格的对象目录冲突检查 -> verify: gap 单元与 runtime 入口测试，准入后命令身份/owner/tier 完整且字符串局部检查列表不被变造。
- [x] 核对跨代 task 续接的全量基线；若任务合同未变，基于已准入的当前完整 map 合并局部 gap；若目标任务已变则拒绝旧结果 -> verify: 两个独立 gap 先后准入不回退其一；篡改包/未知代次/目标任务变化仍拒绝。
- [x] 实现影响闭包、sentinel 和 deterministic mock E2E -> verify: dev-verify plan/run、聚焦测试与合并后检查。
- [ ] 受控加载并重试原失败 gap -> verify: FMS04 部分代码/input selection 保留，TODO 改动不回退；记录根因、RM/OA 未闭合原因及真实派发证据。


## 验证记录

- 初始命令适配回归 1 failed / 5 passed，当前 17 focused passed。
- 最终 18 文件影响闭包 287 passed，覆盖模块、真实 gap bridge/Writer caller、Task Map/包/Sidecar/TaskStore/EventLog/Operation/受控重试。
- dev-verify plan 选择 193 文件并升级为整个 deterministic suite；按仓库规则使用 bounded impact closure，run 仅跳过宽 deterministic tier，serial 10、sentinel 53、flow-smoke 10 passed。非 full suite / real-provider E2E。
- fixture 修正失败、两个错误测试路径导致 exit 4 已在 LS 监工记录如实记录；未改变生产门禁。
- 现场只读回放：35 -> 35，FMS04 -> FMS04-GAP-BUSINESS01；TODO 全部内容不变，其他变动为既有下游依赖重连；保留 1b3ffa8 与 input_selection。正式恢复待部署后验收。
