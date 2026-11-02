# 历史诊断请求的因果当前性对账

> 状态: done；实现 `6b4fff28 fix: release obsolete unbridged diagnostic requests`

## 事实与边界

LS 曾积压多个来自旧 stage-replan 和环境故障的 Autoresearch 请求；现有 reducer 有部分后续进展替代规则，但通用 trigger 包装的来源链不足以证明所有请求已被解决。不能按年龄删除、跳过或伪造终态；真实未解决问题必须保留。

## 验收

1. 从请求追踪 trigger、源故障、Run/generation、operation 和后续结果 -> verify: 至少覆盖相同目标已解决、无关联进展、跨 Run、缺来源四个可复现事件序列。
2. 核对现有 `run_manager.py::_event_superseded_by_later_progress` 和 resident 的现有消费路径 -> verify: 仅对已证明有缺陷的具体路径提出最小修复及真实 caller 回归。
3. 对当前 LS 请求先只读分类 -> verify: 分别列出可证实已解决、仍相关、证据不足；不写入运行态、不自动取消活跃实验。

## 2026-11-02 只读对账

- `ar-stage-replan-941255352b815ab1` 的 trigger `evt-9ae7bdd1363d` 指向 `run.goal.blocked evt-3e1de9bf3bf7`；`ar-stage-replan-5e6de0263869af45` 的 trigger `evt-8195a5f7dc34` 指向 `run.goal.blocked evt-cbc1bf7e28f7`；两者的源故障均为同一 LS Run 的 `stage_replan_cap_exhausted`，不能仅按同名推断两次故障等价。
- 两个请求分别已在 `evt-fc0592ffd1dc`、`evt-cfafd050157f` 结算为 failed；这些请求不应再作为无终态积压案例。尚未完成其他请求和各自后续结果的因果对账，不改事件或自动跳过。
- 2026-11-02 只读最终生命周期对账：此前消费的 6 项均出现 `autoresearch.loop.failed`；`pending_loop_requests` 返回 0，只表示没有待消费项，不表示诊断成功。4 个 stage-replan 实验均有隔离 operation token 预算超限证据，另见 `2026-11-02-0539-autoresearch-diagnostic-input-budget.md`。

## 实现与验收记录（2026-11-02）

- 根因：LS `evt-146f0d386c88` 绑定 `evt-9c9da7f1dee4`，同一 Run 在 `evt-adcde0eb69f2` 重新打开；现有 invocation 入口明确拒绝桥接该旧终态诊断，但 Run Manager 的单飞投影仍把它计算为在途，造成 `diagnosis_wait=autoresearch_inflight`。
- 修复复用现有 `run_manager_autoresearch_request_superseded`，只排除尚无 invocation/loop 生命周期的已替代终态请求；被真实桥接、排队或启动的请求继续占位，其他 Run、来源缺失和未 reopen 均不推定失效。不更改事件账本、不造终态。
- LS 事件只读重算 `superseded_by_reopen=true`，补丁下 `inflight_request_ids=[]`。六条已执行的旧请求均为 failed，不能据此判定原问题已解决；旧请求已被替代这一结论仅适用于 `evt-146f0d386c88`，其他来源与故障状态证据不足时保留。
- 单元与入口回归：跨 Run/早于阻塞/来源缺失、已桥接/已请求/已接纳/已启动保留单飞；真实 `_emit_autoresearch_request` 可接纳新请求。定向闭包 243 passed，哨兵 53、flow smoke 10 passed；未跑全量 pytest 或真实 provider E2E。
