# PRD / Plan 可读性与真实双入口验证

> 状态: done — `2362995c` fix: preserve planning constraints and improve Plan review handoff；后续修正见下文。

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

## 完成证据与边界

实现提交：

- `2362995c` fix: preserve planning constraints and improve Plan review handoff
- `f872d575` fix: align Channel conditions and preserve legacy Task identities
- `ad5539f8` fix: expose authored Plan openings and decision proposals
- `d032afa5` test: bind Plan UI audits to reviewed evidence artifacts

聚焦闭包378 passed / 1 skipped，兼容性追加212 passed，审计/取证/Provider聚焦30 passed；两次sentinel各53 passed；前端构建/单元通过；原阅读回归25 passed、补丁后Docker回归25 passed。测试集合有重叠，不合计；未执行全量pytest。fake两入口、兼容性fake Channel、最终审计版fake Kanban均通过。

真实结论（全部保持首轮状态）：

- Kanban 小CLI：一次额外PRD终点UI协助；首次正确YAML，一次Critic返工；浏览器主体50.3分钟后通过，Plan evt-534623fc5a8b pending；原审计路径错误failed，补充同Run/fanout合同摘要审计通过。
- Channel 首轮：flat条件丢失，首次错误YAML被拦截，0 Workflow invoke；据此修复并用独立新状态复验。
- Channel 复验：条件/首次YAML正确，真实扫描通过；Planner abstain，无实际Critic派发/最终Preview；运行升级人工后主动停止浏览器等待，退出137。不能把Kernel合成的失败聚合当真实Critic审查。
- 多任务：来源P0/P1及mandatory保留；Critic发现首片矩阵混入后继计数断言、README交付时点冲突，reject并保留D-001；没有套用无关Owner答复。
- 真实候选1280/375只读前后回放，开篇和proposed_resolution展示修复，0修改请求；原文和未知状态保留。

完成的是本批方法/代码修正及真实评估，不是所有生成语义100%收敛。独立human_review产物、单任务AC粒度解释、失败分类/恢复、动态问答终点、裸命令Markdown格式和重复上下文仍有缺口。保守shell防护仍存在；无产品实施/产品验收证明。

报告与截图：忽略的 reports/spec206-optimization-20261111/evaluation.md。真实运行已停止并校验归档，未merge/push。
