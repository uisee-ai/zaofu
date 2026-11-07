> 状态: active

# 派发前拒绝 Provider 交互弹窗

用户授权监工修复卡点并从断点继续。真实 Privacy Planner operation `wop-prd-plan-prd-planner-trig-evt-64319b1a-re-4947fbe2d1c5` 于 13:18 被标记 running，但 provider 停在旧结果；额度模型选择弹窗吞掉输入，当前模型变为 Luna medium。13:24 操作者通过 `/model` 恢复 Astra high，沿原 operation 重新投递同一 briefing；已观察到读取该 briefing 的实际工具调用。没有新建 operation 或修改 canonical 状态。

- 启动及复用派发前识别额度模型选择弹窗 -> verify: 隔离 mock 中弹窗不得通过 ready，也不得收到清输入、paste 或 Enter。
- 普通额度提示不拦截 -> verify: dismissed 后仅剩低额度 banner，可以继续投递；不自动选择模型、回答授权或消耗重试。
- 复用现有交互探测与错误通路 -> verify: 登录/信任弹窗同样阻止派发；覆盖 transport、pane probe 直接调用者。
- 真实恢复证据和方法记录 -> verify: [说明](../../docs/impl/provider-modal-dispatch-20261107.md)；保持在途 Core 和 Planner operation。

TDD RED：新增 5 项回归均失败（未识别模型弹窗、启动 ready 和复用 send_task 均误放行）。首轮 GREEN：新增回归 + transport lifecycle + pane probe，23 passed。后续验证及实现提交在完成后补记。

验证规划：`dev-verify.py plan --base dev --paths <本次六文件> --tests <直接闭包>` 保守扩展为237个测试文件并建议全套。按AGENTS.md本次Provider单边界应先隔离mock，未执行整仓pytest；实际执行transport/readiness/pane/composite/task-binding等11文件84passed，另独立验证reader/writer fanout及task dispatch直接调用者。`dev-premerge-gate.sh` 50passed/1skipped；ruff check与diff --check通过。没有运行真实provider API测试；真实在途恢复观察另记。

直接调用者验证：`pytest tests/test_reader_fanout_runtime.py tests/test_writer_fanout_runtime.py tests/test_task_pipeline_context_ownership.py tests/test_dispatch_inflight_wedge.py -q --no-cov -m "not host and not real_provider"` -> 339 passed。
