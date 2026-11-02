> 状态: active

# 局部重规划的断点恢复闭环验证

用户批准先在隔离环境验证恢复闭环；实际 DL 的监工、审批和恢复保持暂停。

说明与复验边界：[局部重规划的断点恢复闭环](../../docs/impl/recovery-closure-validation.md)。

## 实际复现的 P1

完整 mock 链路在两个旧代次下游仍执行时产生 `candidate.ready`。修复冻结入口，
按同 Run 当前任务集合校验各自代次的收据、活动操作、归档及候选历史；不得提前冻结。
修复前 E2E 的 `candidate.ready` 否定断言失败；修复后继续执行两个下游并核对最终只冻结一次。

## 验收

- 使用真实 Orchestrator、TaskStore、结果准入、Git worktree 和 mock transport 建立小型依赖图 -> verify: 基础任务完成独立 Verify 和集成后，两个下游任务实际送达 transport。
- 模拟实现检查点及局部合同重规划 -> verify: 保留代码 commit，新的合同使用新的操作和验证证据；未变任务的合同、代次和历史保持不变。
- 重建 Orchestrator 并重复消费事件 -> verify: 不重复提交、集成或调度。
- 覆盖不完整恢复输入 -> verify: 明确区分诊断任务范围、实际修订范围、代码检查点和运行配置准入，不能将中间步骤报告为闭环成功。
- 运行聚焦回归和 mock E2E -> verify: 记录真实结果和未覆盖的边界，不据此宣称实际 DL 已恢复。

## 验证结果

- 原始负例：下游未完成时 `candidate.ready` 断言失败；补丁后包含三个任务的完整场景通过。
- `dev-verify.py plan/run --base dev`，显式测试集合见说明文档 -> 22 文件、193 passed（含本 E2E 3 项），无真实 Provider。
- `bash scripts/dev-premerge-gate.sh` -> 53 passed；manual docs currentness: ok。首次文件体积检查失败已通过删除重复参数收敛，无豁免。
- `git diff --check` -> passed。
- 实际 DL 不部署、不审批、不恢复。本任务的 done 仅指隔离验证和冻结缺口修复完成。
