> 状态: active

# 回收已失败 fanout 遗留的未启动 synth operation

授权：用户要求持续监工、修复不能自愈的阻塞并从断点续跑。真实运行中旧 Critic operation `wop-prd-plan-synth-trig-evt-64319b1a-994e3c657bfe` 在派发前失败，fanout 已由内核失败收口，但 operation 仍 requested。`goal_idle_driver` 因此永远认为有在途操作。新 Critic 已审查通过，不能重新启动旧审查。

- 复现真实派发异常后的 requested 残留 -> verify: 正常 recovery sweep 测试先红。
- 仅凭完整内核失败事件及不可变请求身份回收从未启动的 synth -> verify: operation superseded，无语义验收结果、无重派、幂等。
- 活跃/已派发/跨 run-stage/worker 自报/重新打开/损坏请求不回收 -> verify: 回归负例。
- 正常恢复入口接入 -> verify: 定向影响闭包、sentinel、mock E2E；部署后确认旧项收口及在途 Verify/Impl 保留。

## 验证

- 原始入口回归先红：requested未收口；7个防护测试首次因新模块未实现失败。实现后新测试8项及弹窗恢复2项共10passed。
- 自动影响闭包14文件538passed/7skipped，sentinel50passed/1skipped，flow mock smoke10passed；额外operation/goal idle/reader recovery/modal28passed。未跑全量pytest或真实provider E2E。
- `dev-verify.py plan/run --base dev --paths <本次四个源码/测试/文档路径> --tests tests/test_synth_pending_terminal_recovery.py --tests tests/test_reader_fanout_runtime.py --no-parallel`；ruff和diff check通过。首次手动测试命令误引用不存在test_fanout_synth_preflight.py，未执行测试，删除错误路径后完整重跑28项通过。
- 真实ledger只读预演（替换service为只记录建议）只匹配旧64319b1a operation，因果绑定aggregate失败evt-31d62c4021e3；不操作当前Impl。产品diagnostics/recovery-20261107-1220/synth-pending-recovery-dry-run.json留档。
