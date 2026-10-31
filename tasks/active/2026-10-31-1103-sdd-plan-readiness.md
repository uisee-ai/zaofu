# PRD／SDD 规格充分性与审查改进

> 状态: active

来源：用户批准“在新的 worktree 生成优化改进的 backlog，实施，完成后列出功能并删除 worktree”。
基线：ZaoFu `8f174350`；本地 Spec Kit `7370e592` 的方法对照。

## 范围与边界

改进现有 Planner／Critic 方法、任务输入交接和预览完整性声明；沿用现有
Plan Package、Task contract、独立 Verify 和受控修订。语义由 Agent 审查，
不新增业务关键词 gate，不将全产品待决事项变成首片全局阻塞。
本批在独立仓库 worktree 实施；ls 冻结计划只读，提供具名修订输入和生效路径，
不把仓库能力更新冒充线上 Plan 已重生成。完成提交后按用户要求删除干净 worktree，
保留未合并分支；不隐式合并 dev 或推送远端。

## 已批准实施项

1. **SDD-01 产品规格与分批设计方法**
   补强现有 `zf-prd-plan-synth`、`zf-plan-execution-readiness`：
   用户价值/优先级/独立验收、成功指标、设计决定/取舍、数据和接口合同、UI 状态。
   - step：按适用范围补充分阶段设计充分性方法 -> verify：代表性输入可产出
     有来源的故事、合同和待决影响；不发明阈值或删减后续 mandatory 范围。
2. **SDD-02 并行交接与独立 Critic**
   补强 `zf-plan-task-map-contract` 的生产者/消费者、合同版本和关闭时点；
   Critic 分别报告产品、设计、覆盖、执行与未决事项，保留审查范围和证据。
   - step：物化到真实 Planner/Critic 输入并进行独立前向评估 -> verify：
     分发副本一致、引用可读；基础任务不被无关未来决定阻塞，依赖未知公共合同的
     消费者被明确指出；字面关键词检查不计作语义质量证明。
3. **SDD-03 预览完整性声明纠正**
   缺失内容 profile 时不再默认 `derived_plan_full`；保留显式声明与旧计划可读性。
   - step：复现后修复审批绑定及 Preview caller -> verify：
     无声明、有长正文、显式完整/摘要、Bundle 和历史入口均有行为回归；
     缺失声明不修改审批结果或运行状态。
4. **SDD-04 样例验证、ls 修订交接及收尾**
   更新实现说明和验证记录；以 ls 高风险切片与不同领域输入评估方法，
   给出 ls 已知错配和公共合同的受控修订清单。
   - step：执行 impact-closure 验证并提交 -> verify：
     记录精确测试层级、独立评估范围和未验证内容；任务归档含实现 commit。
   - step：删除本批 worktree -> verify：分支保留全部提交、无脏文件/活动使用者，
     主 checkout 与其他 worktree 不受改动。

## 验证与完成记录

SDD-01–04 实现已完成，具体验证与受控修订交接见
[验证记录](../../docs/records/2026-10-31-sdd-readiness-validation.md)和
[独立前向评估](../../docs/records/2026-10-31-sdd-method-forward-evaluation.md)。
聚焦 31 passed；方法澄清后 42 passed / 1 skipped；sentinel 51 passed；
Docker 浏览器 7 项既有 + 1 项新增通过，前端构建/typecheck/unit 通过。
impact closure 1116 passed / 1 skipped / 1 failed，唯一飞书用例已在未改动
dev 8f174350 重现；保留已知基线失败，不宣称全绿。
在线 ls 未修改，已提供基于冻结版本的受控修订输入；采用时先核对最新版本。
实现提交与归档状态在提交后填写；干净 worktree 删除由提交归档后的收尾执行，
保留分支和全部提交，不隐式 merge/push。
