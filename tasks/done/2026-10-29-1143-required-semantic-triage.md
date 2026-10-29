> 状态: done — `77f7b9e fix: route required semantic triage independently of legacy thresholds`

# 显式语义耗尽事件不能被旧阈值静默丢弃

来源：持续监工授权。P12A 的 evt-e2a4e7410bcd 明确要求 semantic_triage_required，但默认 strict_triggers.rework_attempts_gte=0 且 execution_routing.disabled 时，没有恢复动作。保留产品数值持久化失败证据和已有人工资格门，不修改预算或伪造成功。

- 显式耗尽事件进入 Run Manager 诊断 -> verify: 默认配置及高旧阈值下都产生有界 triage，未标记事件仍遵循旧阈值。
- 实际 tick 接线与幂等 -> verify: 重复 tick 只请求一次 triage，不自动产生返工、task.done 或新计划接纳。
- 验证并部署 -> verify: focused、影响闭包、sentinel 和现有确定性 E2E；真实事件投影出现 P12A 恢复动作。

本批只修复诊断路由。人工门决策、产品修复、独立复验及持续监工另行继续。

验证记录：新增回归 RED 4 failed / 1 passed；修复后 focused 104 passed。已检查 `dev-verify.py plan --base dev`，继承批次已独立验证，本次以 ecb0344 为增量基线运行影响闭包：912 passed / 7 skipped；sentinel 50 passed / 1 skipped；Flow smoke 10 passed。未运行全量 pytest 或真实 provider E2E。

部署后模块回归 61 passed；补齐主目录此前漏同步的 d399f8d 人工门测试夹具（原断言误将 upstream_contract_gap 当作 manual_evidence_required）。新进程真实事件预览出现 P12A 唯一 triage。操作员按持续监工/修复授权调用受控 `run_manager_tick(action_filter={"orchestrator-rework-triage"})`，一次 applied、零 blocked/failed，产生 `evt-decfaf945338 orchestrator.rework.triage.requested`，实际 orchestrator 已开始处理。人工资格门未批准，产品失败未改成通过。运行中 watcher 未重启，常驻进程后续维护窗口仍需加载补丁；本次受控 tick 已加载修复。
