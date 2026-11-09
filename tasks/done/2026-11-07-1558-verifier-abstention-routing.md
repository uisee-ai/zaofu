> 状态: done — `1d3e9f11 fix: keep verifier abstention out of product rework`

# 独立验证执行失败不得自动转为产品返工

现场 evt-cdc7b0115b92 的受控结果为 execution_status=failed/verdict=abstained，未提出产品缺陷；reconciler 将任意非passed的已settled Verify映射到Impl返工，产生evt-6e28e9769bfe且rework_feedback为空。监工已让worker保留实现与证据，通过修复读取入口继续原候选验证；此处修复未来重放的错误分流，不篡改既有事件。

- 受控Verify弃权，无论剩余产品返工额度 -> verify: 不派发Impl、不批准集成、不报语义返工耗尽，输出独立执行失败 blocker。
- 真正rejected及passed仍按原策略 -> verify: 保持产品返工/集成与verify-only行为。
- 记录恢复边界 -> verify: 弃权不会自动无限重试；监工/Run Manager修复执行条件后使用受控恢复，不把产品代码变更当恢复条件。

验证：先1失败/32通过复现把弃权报为semantic_rework_exhausted；修复后reconciler/真实调用方orchestration/rework/operator retry/fault matrix/attempt terminalization/operation闭包85通过，sentinel50通过1跳过，确定性v4 runtime-noise mock E2E3通过，ruff/diff通过。现场不撤销正在读取的Impl2，等待自然返回独立Verify。

部署：逐上下文hunk合并，保留主checkout原额外尝试授权与角色排序修改；主代码54项回归及新增弃权行为直接复核通过。两个worktree测试混跑出现conftest名称冲突，拆开运行解决，未改测试语义。evt-88f11d1f209a受控重载，watcher4016326于15:58:40UTC取得新epoch；媒体Impl2、Core audit Impl与OA操作保持运行。
