# PRD / Plan 可读性与真实双入口验证

> 状态: active

来源：用户批准按上一轮六项建议实施，并在完成后执行真实 Channel / Kanban Agent 验证。延续当前 spec206 worktree，依据 docs/design/206-spec-method-borrowings-and-quality-evaluation.md 与 reports/spec206-preview-repeat-20261111/evaluation.md；审批传递另见同批 approval-constraint-handoff 任务。

## 实施范围与验收

1. Preview 阅读字段 -> verify: 真实工件中的 id/decision/source_ref、关闭条件及决定状态正确呈现；概览和任务入口完整，保留原文及未知状态。
2. Plan 局部责任 -> verify: Planner/Critic 方法落实已有 execution_spec 人读字段；共享条款集中，任务保留本片准备、恢复、验收、交接，不复制整份 Plan；真实候选单独评估。
3. 来源与就绪语义 -> verify: 独立评审核对优先级、mandatory、范围、未关闭义务和具体 RED/GREEN；构造反例校准，判据不变成 runtime 业务硬编码。
4. 紧凑 PRD -> verify: 同一行为与原子场景单一正文、附录引用；记录下游实际输入规模并保留完整同版来源；不以字数下降冒充正确。
5. 写入与长测试基础设施 -> verify: 复现合法写入误判及测试收尾阶段；明确写入路径正常通过，真实越界仍拒绝；测试主体/审计/归档状态分别记录，归档可读取。
6. 验证 -> verify: 聚焦影响闭包、前端构建、Docker 双视口、fake 两入口先通过，再在冻结版本上运行真实 Kanban/Channel 小 CLI 以及一个多任务共享规则样本。保留首轮失败，停在待审批，不实施产品。

## 边界

单一 Task contract；语义由 Agent/Skill 审查，代码负责合法字段、引用/身份、边界及投影。临时测试使用 /tmp/zf-* 与 8002+；不碰 dev 的 8001。仅本地提交，不 merge/push。

## 本批实现与验证进度

已实现字段传递、阅读投影、生成/评审方法及分阶段测试取证。聚焦闭包 378 passed / 1 skipped，
哨兵 53 passed，前端构建/单元通过，Docker 阅读 25 passed，两条 fake UI 旅程通过。
首次 pytest 因共享依赖目录导入 dev 被保护性拒绝，已用 PYTHONPATH=src:. 重跑；
首次浏览器 fixture 因 UID/JSON 导入设置失败，已修正并独立复验。真实验证待本批冻结后执行。

写入策略为显式目标的原生工具；任意 Python shell 的保守拒绝仍存在，不宣称语义解析误判已根除。
