> 状态: done

# 技术输入失败的恢复分类与监工推进

用户授权持续监工、记录和修复问题；基线 dev f925b76f。真实证据：P12B dev.blocked evt-02b0e42c9751 已准入，后续 triage evt-a9586a4a4e6f 错判 evidence_payload_gap，manual gate 从未执行验收的 blocked 占位状态制造 hdec-1a3a664def67。

- 保留结构化 reader_execution_failure -> verify: 不被 missing/receipt 文本或 YAML 返工路由改成证据补发；不消耗产品重试，交 Run Manager 诊断，不自动重跑相同输入。
- 真实失败优先于未执行的验收占位 -> verify: 旧 triage 记录也不再派生伪人工证据 gate；真实 manual_evidence 失败仍维持人工门禁。
- 所有现有控制面路线可接收该诊断 -> verify: datal 的 run_manager 模式和 wrc 都能从真实 Run Manager 入口派发；不能只修 pure helper。
- 受控部署与恢复 -> verify: 定向回归、sentinel、mock E2E；部署后核对新代码；仅通过 ControlledActionService 解除已证实错误的 decision，保留原产品验收、原始失败和新诊断链。
- 持续观察 Planner/P16B -> verify: 记录实际输入、实现、验证进展；不把心跳或读取当业务完成。

已验证：105 项定向回归；15544 项广泛通过，24 项既有基线失败无新增；serial 10、sentinel 53、mock E2E 10 均通过。真实事件只读回放确认新诊断入口和人工门禁抑制。部署与受控恢复入口验证已完成，详见下文。

实现：fea3c755 `fix: route reader execution failures to diagnostic recovery`；合并 dev：6e28c63c。
控制面重启 PID 388578，runtime-load 确认来源 6e28c63c；配置、worker pane/PID 保留。
错误 decision 通过 ControlledActionService 撤销（evt-6858b7b45928），RM 自动派发诊断
（evt-7666ced8dbc8），派发检查通过（evt-166a7abae525），agent 已读取 Current Focus。
合并后 105 回归 + 53 sentinel 通过；来源 worktree/branch 已按合并归属清理。
监工记录：datal diagnostics/reader-triage-20261109/，含原始失败、回放、测试、部署、受控操作和恢复事件。
本任务完成的是 harness 恢复链修复；P12B 诊断效果仍 pending，Planner/P16B 仍执行，未声明产品任务完成。
