# 已授权恢复保留 shadow 诊断并纠正其权限角色

> 状态: done — `4848698f fix: preserve shadow evidence without granting replan authority`

授权：持续监工 LS-v1，修复阻塞并恢复。前一轮代际修复后，真实启动在 `evt-4a9ccd24994b` 再现 `replan OA decision differs from baseline identity`；恢复请求错误继承历史 shadow/revise 引用，旧待办随后被恢复，且未携带新反馈。停止该次运行后修复。

- 已授权恢复 + 同 Run 精确 kernel shadow 证据 -> verify: 将建议保留为可检索的 observation_only 历史上下文，原事件不变，新反馈完整。
- 正式/未知/跨 Run/不匹配的决定 -> verify: 不降级其身份要求，摘要损坏仍拒绝。
- 受控 new-generation 与 retrigger 两个实际入口 -> verify: caller 回归及计划输入闭包测试。
- 真实恢复 -> verify: 固定本次新反馈，检查真实 dispatch 的源请求和读取清单。旧规划载体冲突单独记录，未以心跳认定恢复成功。

验证：直接 caller/反例回归 96 passed；dev-verify 识别 91 个受影响测试文件，因仓库要求按影响闭包验证，本轮不因 >80 文件阈值重复跑全仓，而显式执行这 91 个文件：1,521 passed / 2 skipped（78.65 秒）。对应 `dev-verify.py run --base dev --tests tests/test_reader_shadow_recovery.py --skip-tier deterministic` 执行剩余串行层 10 passed、sentinel 53 passed；额外 mock flow smoke 10 passed。日志 `/tmp/zf-reader-shadow-{closure,tiers}-20261107.log`。

真实停止状态下预检：对 `evt-61f05e3689ef` 的副本运行修正与完整 handoff builder，通过 21 个输入源、六条反馈，精确 shadow 证据进入 manifest；只生成不可变输入 sidecar，未改原事件或计划。待部署后核实 dispatch 再归档。

部署验收：已合入 dev `a1ebbfa4`；`evt-48077653aae9` / `evt-2b1a47ac7275` 派发 `evt-5b69b77b8744` 对应 operation，六条反馈及 shadow 观察均保留。随后暴露的旧 reader 抢占已另由 `d30944ca` 修复。UTC 02:27 Planner 已读取正确 briefing 并调用本 attempt 的 artifact read；不是仅 action applied。真实监工记录见 LS-v1 `docs/records/2026-11-07-three-blockers-recovery.md`。业务任务尚未完成，不计入本代码修复完成数。
