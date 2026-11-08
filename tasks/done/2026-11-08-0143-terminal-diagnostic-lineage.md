> 状态: done — `0ae2beb7 fix: bind task replacement diagnostics to admitted runs`

# R62：正式替代事件的 Run 身份与历史诊断退役

用户授权持续监工及修复。LS-v1 FMS20 Gateway 已正式替代，RM 仍保留旧失败；实际 Orchestrator writer adoption 未传 trigger，task.superseded 缺 Run 身份。已有测试假设 correlation 含 Run，未覆盖真实调用。

- [x] 为实际 writer adoption 事件绑定 loaded 的权威 Run -> verify: Orchestrator caller、TaskStore cancelled 及 RM 待办闭环。
- [x] 兼容旧无 Run 事件 -> verify: 只用同一精确 replacement map 的 kernel generation admission 唯一 Run 证明；冲突、跨 Run、未准入 ref、伪造 origin 均不能退役。
- [x] 复核并保持严格事件顺序 -> verify: terminal 前后不混淆，取消后新失败仍可见；不按 task id 或普通活动关闭诊断。
- [x] 聚焦回归、合并与线上只读回放 -> verify: Gateway 历史待办退出，未证明的 planner / grants 故障仍保留，不声称自动修复产品。

额外发现：FMS21 旧任务于 14:54 被替代，但 discovery 在 14:55 才派发、15:06 新失败。不是“晚到的旧结果”，不应借较早 terminal 隐藏；需另查过期恢复派发。根 planner 旧故障和虚假 open attempt 投影暂未纳入本窄修复。

验证：原 HEAD 3 个真实回归失败；focused writer/runtime 初轮205 passed；最终补入只读投影及精确历史图身份后230文件影响闭包3267 passed，sentinel53 passed，serial10 passed。dev-verify会因80文件阈值升级全套，因此run显式skip deterministic后逐个执行选出的230文件；不声称全pytest。mock恢复E2E包含于focused/闭包。LS只读回放：Gateway旧诊断retired=true；Grants取消后的新失败及root planner失败仍false。实际writer取消先于generation admission，故使用精确已准入map引用反向证明Run，保持terminal必须晚于源失败。

实际部署与收口：merge2e2fe198已加载，watcher762159→808184且在途OA身份与pane不变；之后两个产品Impl正常并行派发。01:52:56真实RM投影已排除Gateway evt-ebed9a1d61e9，保留planner evt-86c01b169417和取消后新失败Grants evt-9ed43fc84801。未改历史ledger；证据LS-v1 docs/records/2026-11-08-r62-live-projection-confirmed.json。合并后focused35及sentinel53通过。
