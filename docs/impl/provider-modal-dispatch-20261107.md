# Provider 弹窗导致假派发的恢复

关联任务：[派发前拒绝 Provider 交互弹窗](../../tasks/done/2026-11-07-1326-provider-modal-dispatch.md)。

2026-11-07 额度恢复后，Core 可从保留 checkpoint 继续，但 Privacy Planner 的 provider 停在额度模型选择弹窗。tmux paste/Enter 没有提交任务而是选中了默认模型；operation 的 running 只证明派发路径执行，不证明模型已消费 briefing。实际 pane 显示旧结果和 Luna medium，无新读取。普通低额度 banner 本身不是阻塞。

根因有两处：`provider_interactive_prompt_marker` 未识别 `Approaching rate limits` / `Switch to` / `Keep current model` 组合；`TmuxTransport.send_task` 对复用进程只检查存活和 cwd，未在清输入/paste/Enter 前复查交互弹窗。修复复用同一个探测器，在发送任何输入前抛出现有 TmuxError/诊断，交给已有派发失败处理；不自动确认弹窗或变更模型。此修复是派发前检查，不宣称解决屏幕检查之后瞬间弹窗的所有竞态，也不是 provider 收件确认协议。

真实恢复：操作者先检查当前 pane 无新执行，使用 provider `/model` 恢复既定 Astra high，再通过 `TmuxTransport.send_task` 投递同一 `build_task_prompt(..., prompt_kind="fanout_child")`，没有新建 operation、伪造 worker 心跳或直接改 canonical 状态。13:24:33 UTC 记录在产品项目 `diagnostics/recovery-20261107-1220/planner-prompt-redelivery.json`，briefing SHA256 `1cb00f924ed7a82173f30b80153d1a61188d0e103568d4ca1bbc15ae00c4af5e`。随后 pane 出现读取本轮 briefing 与角色约束的真实工具调用。

验证覆盖：模型弹窗识别、启动 readiness、ready 后新出现弹窗的复用派发、禁止任何输入、普通低额度 banner 恢复，以及现有登录/信任阻塞。使用隔离 fake provider，不调用真实 provider API。实际任务推进单独核对事件、operation、provider 工具调用及后续独立验证；不得把恢复执行当作业务验收完成。

## Critic 派发失败的恢复分类

后续真实观察：Planner提交evt-1110803f8950后，Critic仍有额度模型弹窗。守卫阻止了误切模型，但`PlanSynthRuntimeMixin._dispatch_fanout_synth`的通用异常分支把它写成`fanout.synth.completed/status=failed/recommendation=reject`，间接消耗一次stage replan并重跑Planner。关联[恢复任务](../../tasks/done/2026-11-07-1348-synth-interactive-recovery.md)。

新增处理只消费transport错误携带的`interactive_prompt_marker`：沿既有`fanout.child.dispatch_deferred`记录synth派发等待，同原因在既有60秒窗口内去重。没有发生Critic执行，不发语义失败；保留reader结果与已准备的operation。正常incomplete aggregate恢复扫描在弹窗解除后继续派发同一synth。原有schema、机械preflight和真实Critic拒绝不豁免，不自动确认弹窗、选择模型或提高重试上限。既有pane probe仍报告等待人工输入的环境状态。

## 旧版本失败后遗留的 pending operation

关联[回收任务](../../tasks/active/2026-11-07-1411-synth-pending-terminal-recovery.md)。旧通用异常路径还可能留下从未启动的 synth operation：fanout 已失败，operation 仍 requested，导致 goal idle 驱动一直判定有在途工作。正常 reader recovery sweep 现在可根据不可变请求中的 fanout 身份、同 run/stage/role/attempt 的内核 synth 失败及后续 aggregate 失败事件，通过 `WorkflowOperationService.supersede` 回收这一残留，不生成语义通过/失败结果，不重跑已结束 Planner。

该回收限定 requested 且无 start/dispatch 证据；有派发、重新打开 aggregate、跨身份、worker 自报或请求 sidecar 无法验证时保留原状态。事件次序必须是请求 → synth 失败 → aggregate 失败。每轮至多收口一个，重复扫描幂等。当前合法运行的 Verify/Impl 由原生命周期继续处理。
