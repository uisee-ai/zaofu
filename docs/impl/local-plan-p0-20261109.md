# 局部 Plan 与任务输入 P0（2026-11-09）

对应任务：[第一批 P0](../../tasks/done/2026-11-09-0755-local-plan-p0.md)。
继承 [Critic 输入恢复](critic-input-recovery-20261109.md)，不是另建调度状态机。

## 根因与责任

本轮 datal 局部问题触发了历史 Plan 一致性整理，两个任务的请求演变成 43 个任务及全局字段变化。
缺少精确增量证明时，全量 Critic 是当前正确回退；不能靠减少必读或恢复过期合同冒充局部修复。
P12B 的输入失败报告又被完整读取门禁拒绝，造成“无法读取，但必须读完才能报错”的循环。

语义分类由 Planner/Run Manager 和 `zf-plan-task-map-contract` 方法负责：不变合同中的实现错误走任务返工；
任务义务变化走显式范围的局部 Plan；全局义务/共享协议/不明影响先对账。基础设施读取失败不成为产品否决。
固定基线、保留完成证据、使用已绑定 refs/digests、隔离历史整理是作者责任，运行时只机械核验声明。

## 实现

- `plan_local_review` 在既有严格前置证明之后，支持同版本原生 `task_input_policy`。
  校验完整叶子覆盖、相同策略、共享内容不变、范围外原文不变、任务归属、依赖闭包。
  原始完整正文继续绑定；任何证明不足回退全量，不伪造增量批准。
- `call_result_admission` 允许有摘要和诊断引用的 `failed + reader_execution_failure` 记录未完成读取。
  Impl 必须 blocked/abstained；Verify 仍由原 schema 要求 abstained。只豁免缺少必读完成证据，
  保留 credential、attempt、合同/target digest、损坏 ledger、typed result 等检查。
  成功和产品否决不能借此通过。执行卡给出准确报告方式。
- Planner 的 `zf result validate` 返回 `input_cost`，显示最大端口、去重渲染字节数、完整端口分页、
  固定预算及声明局部输入的任务；新 attempt 在 request 内固定预算，后续验证使用该固定预算。
  草稿估算不是门禁通过：未含原始需求/基线/报告，也未套用最终局部证明。超估算提示 review_required，
  最终 Critic manifest 仍执行硬预算。按字符计算分页；UTF-8 字节不能充当字符页数。
- 任务级选择继续使用既有 `evidence_contract.plan_input_selection` 和 `task_execution_input`，
  保留原始矩阵供 Plan/Candidate Verify。没有新增 Task schema，也不热改正在运行的输入策略。

## 验证边界

新增红绿测试覆盖实际 result submit、实际 Critic dispatch/admission、原生分区拒绝场景、
完整合同 Verify 失败及门禁反例、只读 Planner validate。既有 task selection、gap baseline、
infra retry 分类和独立验证测试纳入影响闭包。合并前执行 dev-verify、sentinel 和确定性 mock E2E。
真实 datal 的重启、PID、代码 SHA、恢复请求和后续事件记录在项目 diagnostics，
不得用单元测试通过代替真实 Critic 或产品完成；已有历史基线失败单独记录。


## datal 部署证据

代码 a732c321 已通过 06263779 合并到 dev；运行时加载 source_revision 06263779，PID 112636。
`evt-5eac9cd4786b` 记录 loop 启动，`evt-08d144de0f00` 证明新 Planner 实际派发。
新 request 固定 4 MiB / 512 页限制，保持原 task_ids、8+8 lanes 和 Plan approval。
两个活跃 Impl 保留同一 attempt 并切换最新 CLI，未热改输入策略。

上一轮固定候选用新预检重检：结构验证零错误，实际渲染候选端口 5056972 字节、最低 524 页，
成本提示 review_required；此前作者按压缩文件计算的约 4.11 MB 不能代表交付成本。
新诊断在 Critic 启动前暴露了该差异，但不替代最终 manifest 硬门禁及语义评审。
51 个任务没有输入选择，新的 Planner 已读取更新后的技能继续修订。
证据位于目标项目 `diagnostics/local-plan-p0-20261109/`，不属于 runtime canonical state。
独立 Critic 和同版 Plan 批准仍待真实新候选，不将运行恢复当作产品通过。


P12B 真实结果回归已受理：`evt-67a9e1f72b7f` 绑定 failed execution / incomplete input consumption，
`evt-02b0e42c9751` 为 reader_execution_failure 的 dev.blocked。既有任务及凭据绑定保留，
没有借技术失败生成产品成功。失败报告不能受理的问题已解除，P12B 业务工作本身仍未完成。

后续监工发现并复现了 [技术输入失败的恢复分类问题](reader-failure-triage-20261109.md)：
结果准入成功后，旧 triage 将失败误认成人工证据缺失；其修复单独记录，不改写本批真实回执。
