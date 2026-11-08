# Plan 候选输入检查与派发选择一致

> 状态: done — a32775ce fix: align plan input preflight with dispatch selection

用户授权持续监工及修复阻塞。实测 FMS03 局部修订的 trigger.task_ids 仅含 FMS-03-GAP-E2E-PORT-PROBE-02，但预提交校验检查全部55任务以及各任务未消费的历史端口，产生7个旧任务分区错误；正常包准入已有 task_ids 与 plan_input_selection 过滤。

## 范围

复用现有 Writer 任务选择，统一候选与包准入的消费端口/分区检查；完整端口 schema 仍全量验证，选中任务的缺正文、错误来源或谱系仍拒绝。不得篡改运行中 Planner 输出、扩大任务或重绑冻结输入。

## 验收

- selected task / wave / successor 与 Writer 一致 -> verify: 实际 evaluate_plan_candidate_preflight 调用的回归测试。
- implementation/task-verify 声明和 legacy required/local 端口一致 -> verify: 候选/派发共同规则正反例。
- 必需分区、格式及来源门禁保持 -> verify: 缺分区、畸形选用、未选中端口结构损坏均拒绝。
- 当前55任务候选只读回放 -> verify: 7个旧任务误报消失，原正文/任务/operation身份不变；后续由原Planner自行提交。
- 合入dev并重载控制面 -> verify: 在途操作/终端保留，记录真实提交结果。

## 验证记录

- 实际入口新增回归先4红4绿；最初fixture遗漏完整矩阵导致额外schema错误，补齐后仅剩4个目标红例。
- 新增10例、相关入口/包/谱系和199 mock验证；首轮70通过，新增local/verify-only后21定向通过。
- dev-verify 74文件影响闭包首轮9失败1160通过；均为旧谱系fixture未声明必读端口，补齐合同后所有9项安全拒绝保持。最终1169通过，76.04秒；非全量pytest/真实provider E2E。
- 哨兵53通过；真实冻结30文件/55任务同一候选前7错、修复后0错，逐文件摘要不变。
- 相关实现说明：[输入分区选择](../docs/impl/plan-candidate-input-selection.md)。
- 合并 edae2889；部署Watcher 2874345/Web 2874271，5个在途operation/attempt/pane完全保留，Web health/UI 200。合并后21项定向通过。原Planner正式提交继续观察；不改运行中候选/旧任务身份。
