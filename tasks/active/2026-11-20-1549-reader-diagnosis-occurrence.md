# Reader 新失败不能被旧诊断去重吞掉

> 状态: active

来源：用户授权恢复8×8并修复阻塞。前一修复部署后，真实当前失败
`evt-bf286469640a` 仍未成为 RM pending action。只读检查证明它 actionable、未被成功
替代，却被旧 `prd.scan.failed` 的同一 checkpoint 判作 seen。

## 实施

只对带 fanout_id 且明确 failure_kind=infra 的失败，将诊断 checkpoint 绑定具体
失败事件；同一问题的较新发生替代旧输入。问题 fingerprint 保持稳定，同一个事件
仍只处理一次，修复准入和已有权限不变。其他语义事件仍沿原策略。

## 验收

- 两个Reader失败、旧诊断已派发、部分恢复后产生新汇总 -> verify: 当前RM投影选中新的失败事件。
- 旧诊断尚未处理、出现更新失败 -> verify: 仅最新输入入队。
- 同一事件的诊断已派发 -> verify: 重放不重复入队，旧账本不变。
- 原真实账本只读复验 -> verify: pending重新出现 `evt-bf286469640a`，不改运行状态。
- 修复重启后 -> verify: RM消费新诊断，技术Reader出现实际重试；不得代交Reader结果。

验证：TDD 2 failed；聚焦51 passed。`dev-verify.py plan/run --base dev`影响闭包
7,913 passed、7 skipped，Web typecheck/unit通过，sentinel53通过，flow smoke10通过。
当前真实账本只读复验已选中最新失败；部署后的实际恢复待验证。
参见[8×8实施记录](../../docs/impl/eight-by-eight-unattended-qualification.md)，日志前缀
`reports/eight-qualification-20261120T122929Z/reader-diagnosis-occurrence-*`。
