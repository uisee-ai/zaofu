# 182-B05 P2 · TDD 与规格到测试的证据绑定

> 状态: done；实现：`865863d feat: bind planning roles to execution readiness methods`。
> 来源: [设计 182](../../docs/design/182-plan-review-task-slicing-and-evidence-closure.md) B05。
> 授权: 用户明确要求在独立 worktree 生成 backlog 并全部实施，随后启动 Dashboard 重新规划和评估。

## 范围与验收
- 对应：F07、F08、D04；所有者：开发/Verify 方法；依赖：B03。
- 选取一个纯规则、一个数据库事务、一个 UI 行为示例 → verify: 每例均能从规格定位测试名、预期结果和适当测试层级。
- 使用现有证据工件记录 RED/GREEN → verify: 相同测试在实现前因目标行为缺失失败、实现后通过，并绑定可复核的基线/测试变更与实现版本。
- 区分行为失败与环境失败 → verify: 缺依赖、命令拼错、服务不可达不能被当作功能 RED 的充分证据。
- 保持最终正常功能任务为通过要求 → verify: 不批量设置 expected_red=true，不为 TDD 把测试交给另一独立任务。
- 明确合理例外 → verify: 无行为变化的文档/配置工作说明适用性，不制造失败测试。
- 角色方法及 Verify 评审消费这些证据；如需新增机器字段，先证明现有 sidecar/receipt 无法表达，再评审兼容性。

## 实施记录

- 实现：`865863d feat: bind planning roles to execution readiness methods`。
- `yoke/tdd-evidence` 增补纯规则、数据库事务和 UI 三类规格→测试→有效 RED/GREEN 示例，
  绑定基线、测试变化、实现版本；明确环境失败不是功能 RED，无行为变更可解释不适用。
- Dev/Verify 角色实际消费该方法，技能依赖物化和镜像测试通过；相关方法/注入回归
  83 passed、1 个环境条件 skip。没有新增第二套任务 schema 或改 expected_red 默认语义。
- 本批行为修复的实际失败/通过日志已绑定到 `reports/plan182/tdd-evidence.json`；
  夹具和环境错误单列。洛书产品测试尚未执行，不能用本项完成认证其未来开发已遵循 TDD。
