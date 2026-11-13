> 状态: done；实现：`2518fa03 fix: reconcile spec handoffs from real workflow evaluation`。

# 第二批：修订、反例、成本与真实交接

来源：用户批准的八项候选；基线 `6da4e8d2`；设计见 [206](../../docs/design/206-spec-method-borrowings-and-quality-evaluation.md)。

- [x] B2-5 集中变更、保留行为、消费者与证据重审 -> verify: 绑定同版章节展示；共享合同变更正反例核对直接/间接消费者
- [x] B2-6 按风险明确反例与不变项 -> verify: 独立 oracle 对正确实现及错误变体执行；记录实际反例
- [x] B2-7 减少方法/上下文重复，保留必要输入 -> verify: 对比方法字节和真实读取/耗时/token；不将未受控观测说成因果收益
- [x] B2-8 真实 Kanban/Channel 规划及多任务实施交接 -> verify: 两入口同候选同输入运行；审阅后经 UI 审批隔离产品实施，独立 oracle；多领域及真人评测如未取得则明确未测

边界：沿用现有合同、Skill、受控审批和验证入口；不新增轻量流程，不改8001，不合并或推送。真实评测保留失败与恢复证据。

## 实施与验证记录

首轮实现：`ea894e5d feat: clarify spec review and verification responsibilities`。
补修：`2518fa03 fix: reconcile spec handoffs from real workflow evaluation`。实现与实测的范围分别记录，不把产品行为通过当作规格生成质量全通过。

- Web：概览/责任/决定/变更入口，同版决定原文在结构化登记为空时仍可阅读。
- 方法：原始约束、完整决定集合、AC实际阶段责任、唯一生产者与后继消费者、真实解码命令和交付资源核对；沿用既有六类报告ref。
- 校准：同版14项真实模型文本校准通过（3正例/11负例），独立oracle校准7项，六引用交接路由测试。
- 完整真实运行冻结首轮提交；Kanban目标闭环通过，Channel经操作员续测也达到目标闭环（28项强制目标），保留原run失败记录。补修有局部回归和真实工件回放，未声称补修提交完整重跑双入口。
- 结果与证据：[206实施评估](../../docs/impl/206-spec-quality-evaluation.md)，本地详细报告 `reports/spec206-batches-20261113/evaluation.md`。
- 性能未解决；方法总字节增长，离线引用体积下降不等于时延下降；没有真人阅读时间、多领域真实产品或稳定成本A/B。

最终补修验证：`dev-verify` 按两批明确路径与6个直接测试模块执行，Python **77 passed、1 skipped**；Web typecheck、完整Web unit、`npm run build` 通过；Docker `plan-human-review.spec.ts` **14/14**通过。未运行全量后端pytest。
