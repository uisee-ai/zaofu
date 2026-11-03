> 状态: done — `1c7aaa1d fix: release superseded fanout synth operations and workers`

# P2：被替代的 reader fanout 未回收 synth operation 与 provider

用户授权：持续监工 DL，恢复 blocked task，修复阻塞并合并 dev。

现场：`fanout-prd-plan-evt-d4e8591c` 已被更新 Plan 替代，
但其 `wop-prd-plan-synth-trig-evt-d4e8591c-9d8ae42c9f22` 仍 running。
旧 Critic 持续消费失效候选，并占用新 Plan 的 Critic role。
取消路径仅枚举 children，遗漏 manifest.synth；provider 停止身份检查也遗漏
`fanout.synth.dispatched`。不得丢弃原请求、读账或伪造 Critic 通过。

## 验收

- 当前 dev 复现 -> verify: 真实 Orchestrator sweep 在 supersession 后仍遗留 synth。
- 回收过期 synth -> verify: operation cancelled，确认 provider 退出后释放 role，重复 sweep 幂等。
- 保留当前工作 -> verify: 当前 synth 与已复用 role 不被旧取消误杀。
- 受控部署 -> verify: 旧 Critic 停止，当前 Planner 请求/读账保持，Web 健康。

文档：`docs/impl/plan-producer-failure-admission.md`。

复现：dev `9e319066` 上新增 5 个用例，4 failed / 1 passed；
过期 operation 均错误保留 running。修复后新用例和共用 provider 取消测试
15 passed，后续继续验证 fanout/recovery/operation 调用闭包。

验证：dev-verify 自动层及补充直接调用方共 668 passed / 7 skipped；
pre-merge sentinel 53 passed；三个流程 mock E2E 10 passed。
不是全量 pytest 或真实 provider E2E。合并后哨兵 53 passed、取消回归 15 passed；serial 筛选无命中。

部署：`b3e514a5`，runtime PID 734450，Web PID 734717。
`evt-e37368699de6` 将旧 synth operation 取消；`evt-984f9c0c41aa`
确认 provider 停止，旧 Critic PID 4145380 已退出。当前 Planner PID 478975
及 OA/RM/AR 进程、原请求身份和两个读账前缀均保持。
Web `/health` 返回 ok=true / b3e514a5。证据保存在 DL
`artifacts/supervision/stale-synth-deployment-2026-11-02.json`。
这不是产品 task 完成或 Critic 审查通过。
