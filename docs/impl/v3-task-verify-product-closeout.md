# v3 分片 Verify 与完整产品验收

v3 `affinity_stage_slots` 的 reader 在集成候选版本上执行逐任务验证。此前仅因为
payload 带 `candidate_head_commit` 就选择 `candidate-verify`，导致每个 reader
都要提交全产品报告，和任务粒度、并行分工冲突。

`scoped_candidate_verify.py` 仅在配置同时声明后续 PRD/Issue discovery 消费者时
选择 `task-verify`。手工指定全局 profile、静态 Candidate Verify、v4 task pipeline
保留各自边界。运行时仍绑定当前 TaskStore contract、TaskRef、snapshot、
candidate generation/head 和候选内任务集合；任务 scope 不表示退回任务源提交。

完整调用链：

```text
candidate.ready
  -> affinity task Verify fanout
  -> test.passed
  -> flow.discovery.requested
  -> flow-discovery（Plan ports + 原始分片结果 + 完整产品验收）
  -> flow.discovery.completed
  -> flow.goal.closed
  -> Thin Judge
```

`flow_discovery_context.py` 将同 run、generation、candidate 和已完成 fanout 的
准入 control/envelope refs 交给受控 reader，并保留 effective config/run contract。
`CanonicalHandoffResolver` 将当前 Plan 必需 ports 加入 discovery 的必读 manifest。
结果准入绑定完整产品报告，拒绝缺 mandatory assertion 或错版本；重放会重新水合并
校验已有报告。Goal closure 只消费实际 discovery 事件对应的已准入完整报告。

语义分工见 [复扫技能](../../skills/zf-verify-rescan-replan/SKILL.md)。产品验收方法
由 Plan/agent/skill 所有；内核只检查身份、schema、证据与生命周期。
分片通过不等于全产品通过，也不绕过 v3 的完整 Impl 候选屏障。

验证覆盖：八个独立 operation 的 profile 选择、缺少全局消费者的回退、
stale candidate 拒绝、完整报告准入/重放、原始分片收据隔离、配置身份连续性，
以及真实 Orchestrator/结果提交 caller 驱动的 deterministic mock E2E。
Mock 不证明真实 provider 同时运行八路；该结论需要独立现场调度和浏览器证据。
