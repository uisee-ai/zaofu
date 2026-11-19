> 状态: done

# P2 真实验收补丁：重派后的宿主验证身份绑定

承接已批准的无人验收、发现问题修复并记录。基线dev `64057481`。
真实Run `workflow-863563136fc24d34` 的Mask与Average在重派后成功提交执行失败，
宿主验证却因同operation存在多个历史dispatch而拒绝绑定（只读preview已复现）。
不能删除历史事件、伪造新的失败结果或任意选择第一/最后工作区。

## 验收

- 重派历史保留 -> verify: 只绑定当前operation的dispatch、attempt、lease及Task/Run/generation；历史次序不改变归属。
- 缺失/冲突/worker来源/跨任务绑定拒绝 -> verify: 不调用容器，无产品通过事件。
- 实际RunManager入口 -> verify: 确定性mock中执行当前宿主命令一次，接续独立Verify一次，不宣告Task通过。
- 影响闭包、sentinel及确定性mock -> verify: 全部通过后才加载真实运行；维护人工计数不清零。

实现说明：[持续推进与无人资格](../../docs/impl/unattended-progress-recovery.md)。
证据：`reports/host-redrive-binding-20261119/`；旧代码定向回归2 failed / 1 passed。

## 验证记录

- 原代码：2 failed / 1 passed；真实只读preview拒绝同operation历史多派发。
- 影响闭包：244 passed；第一次闭包17失败来自共享测试夹具未标内核来源，补齐真实事件来源后重跑通过，原断言保留。
- 8槽Verify mock、TaskAttempt故障矩阵、8×8 fixture及本次回归：23 passed。
- premerge sentinel：53 passed；文档/结构检查通过。
- 候选代码对两个真实operation只读预检通过；未运行宿主命令、未提交验证结果。
- 记录位于`reports/host-redrive-binding-20261119/`。完整无人资格仍由P2主任务验证。

实现提交：`dfccba59 fix: bind task host recovery to the current dispatch`。
