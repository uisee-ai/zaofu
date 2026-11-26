> 状态: done · `a5e1a458 fix: route rejected pre-plan sources back to their authorized owner` · `12bf47c0 fix: preserve plan port authority and diagnostics in handoff checks`

# 派发前的输入纠正与阶段交接

在 215 工作树完成 8×8 C 的恢复建议，保留正常分工、独立验收与项目批准策略。

- OA 合成时给出不可读取的项目引用 → verify: 真实准入入口向原 OA 交付有来源绑定的拒绝，校正仅改来源，重放不重复执行。
- 已批准的扫描输入在首次派发前失效 → verify: 以 Run、阶段和成员义务定向纠正，只重派被拒成员，成功回执仍有效时继续原 Plan。
- 看板展示尚无 operation 的义务 → verify: 可回看失败事实、责任人和等待原因，当前尝试与历史或其他 Run 的事实不混用。
- 跨阶段交接 → verify: mock 完整流程和隔离的最小真实 Provider 项目分别核对领取、独立验收和最终产品，可重现的失败与人工动作单列。

## 验收结果

- 原 8×8 C 的冻结现场复盘与看板回放已核对。失败的扫描成员、责任角色、待纠正的输入和已完成兄弟成员可分别追溯。真实 OA 在已授范围内修正来源，然后恢复两项扫描并交给 Planner。
- 最小真实项目在 `9bea6359` 的同一 Run 中，初批后按协议注入一次来源故障，61 分 22 秒自动完成计划、实施、独立验证、Judge 与 Git 交付。独立克隆 `main@576d7a77` 后复验退出码、stdout、stderr 与冻结测试，18/18 通过。
- 原 Candidate 和 Judge 卡点分别经受控升级从原 Run 恢复至交付，分别核证 18/18 和 13/13。后者的最终 Judge 尝试与较早尝试读取的 Task 正文在审计中区分。Aisz 在原 Run 上受控恢复，复用两项扫描；Planner 修正任务图后，独立 Critic 读完 7,220 个字符并批准。2026-11-26 07:12:05 UTC 完成交付，独立克隆现场和归档的 `main@c4895ab6` 均通过冻结测试。独立核对 8/9：最终 Judge 已签署通过，其当次读取账本对获准的 Task Verify 正文记录为零。Candidate 三项字段声明的定位或摘要另有偏差，直接子进程测量与产品复验一致。原单版本试验仍记为阻塞。8×8 整批无人完成仍单列验证。
- 收尾代码 `12bf47c0` 在干净工树通过 1 个任务、8 个 Candidate Reader、独立 Judge 和真实 Git 交付的完整 mock，归档 Git 的独立克隆通过。确定性影响域 19,402 通过、22 跳过，串行、Web、哨兵与工作流冒烟均通过。[实现与资格边界](../../docs/impl/spec215-qualification.md) · [本地交接验证归档](../../reports/worktree-archives/20261126T0733Z-spec215/reports/preplan-handoff/验证.md) · [Aisz 独立签收](../../reports/worktree-archives/20261126T0733Z-spec215/reports/preplan-handoff/final-clean-qualification/8da339441821-zf-final-happy-20261126T042818Z-Aisz/independent-recovery/签收.md)
