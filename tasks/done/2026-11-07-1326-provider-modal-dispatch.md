> 状态: done

# 派发前拒绝 Provider 交互弹窗

用户授权监工修复卡点并从断点继续。真实 Privacy Planner operation `wop-prd-plan-prd-planner-trig-evt-64319b1a-re-4947fbe2d1c5` 于 13:18 被标记 running，但 provider 停在旧结果；额度模型选择弹窗吞掉输入，当前模型变为 Luna medium。13:24 操作者通过 `/model` 恢复 Astra high，沿原 operation 重新投递同一 briefing；已观察到读取该 briefing 的实际工具调用。没有新建 operation 或修改 canonical 状态。

- 启动及复用派发前识别额度模型选择弹窗 -> verify: 隔离 mock 中弹窗不得通过 ready，也不得收到清输入、paste 或 Enter。
- 普通额度提示不拦截 -> verify: dismissed 后仅剩低额度 banner，可以继续投递；不自动选择模型、回答授权或消耗重试。
- 复用现有交互探测与错误通路 -> verify: 登录/信任弹窗同样阻止派发；覆盖 transport、pane probe 直接调用者。
- 真实恢复证据和方法记录 -> verify: [说明](../../docs/impl/provider-modal-dispatch-20261107.md)；保持在途 Core 和 Planner operation。

TDD RED：新增 5 项回归均失败（未识别模型弹窗、启动 ready 和复用 send_task 均误放行）。首轮 GREEN：新增回归 + transport lifecycle + pane probe，23 passed。后续验证及实现提交在完成后补记。

验证规划：`dev-verify.py plan --base dev --paths <本次六文件> --tests <直接闭包>` 保守扩展为237个测试文件并建议全套。按AGENTS.md本次Provider单边界应先隔离mock，未执行整仓pytest；实际执行transport/readiness/pane/composite/task-binding等11文件84passed，另独立验证reader/writer fanout及task dispatch直接调用者。`dev-premerge-gate.sh` 50passed/1skipped；ruff check与diff --check通过。没有运行真实provider API测试；真实在途恢复观察另记。

直接调用者验证：`pytest tests/test_reader_fanout_runtime.py tests/test_writer_fanout_runtime.py tests/test_task_pipeline_context_ownership.py tests/test_dispatch_inflight_wedge.py -q --no-cov -m "not host and not real_provider"` -> 339 passed。

实现提交：`c61aac0a fix: reject task dispatch into provider modal prompts`。本次代码闭包为84+339=423项隔离/直接调用者测试，另sentinel50passed/1skipped。

部署：确认main三源码与补丁前镜像一致后精确同步（未覆盖其他dirty文件），main组合首轮49passed/1failed；失败为既有10ms ready超时测试的诊断类别竞争，`wait_ready` AST与补丁前逐节点相同，复跑该项+本次guard及dispatch闭包33passed。没有改断言或阈值，该计时不稳定问题保留为后续测试基础设施改善，不隐瞒首轮失败。

通过既有source_repair_restart_actuator发起保留worker的控制面重载，事件evt-b1b761917ad5。13:30:03 UTC新watcher PID3112427启动；13:30:33核实Core与Planner同operation仍running、provider panes仍在，Dashboard HTTP200。真实Planner已读取新briefing、返修上下文和043c348c gap skill。产品独立验收仍待后续，不属于本修复done声明。
