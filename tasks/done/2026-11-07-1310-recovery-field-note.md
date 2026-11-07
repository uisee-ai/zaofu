# R35：恢复技能的快照字段说明校正

> 状态: done — ed94e259 docs: distinguish plan commands from snapshot fields

本轮核对真实Task与冻结snapshot时发现：R31补充的Task contract.verification_commands说明不准确。TaskContract实际使用validation.commands，verification_commands是投影snapshot字段。删去不必要的落地字段括注，保留正确Task Map机器合同；不改runtime及在途attempt。

- 更正 canonical skill -> verify: 元数据验证与skill focused tests通过。
- 同步 LS root技能来源 -> verify: 精确单文件commit，两端SHA一致，既有三个dirty skill保留，不写活跃role快照。
- 归档 -> verify: 实现commit及同步证据记录；不push。

验证：dev-verify 41 passed / 1 skipped（既有外部样例缺失），metadata通过；LS root单文件同步commit见docs/records/2026-11-07-recovery-field-note-correction.json，3个既有dirty skill哈希保持，未写任何活跃role技能。
