> 状态: active

# P1：Reader/Synth 的受控角色重启被误判为派发丢失

监工授权：Owner 已要求遇到问题记录、修复、验证、合入 dev 并继续任务。
当前 HEAD `be2bacba` 隔离复现：`tests/test_reader_respawn_continuation.py`
两个场景均失败；重启期间的 launch 事件以及已经投递恢复简报后的 launch 事件，
都会再次消耗历史失联恢复次数，并把正在继续的 Synth 判成 failed。

现场动机：DL plan-critic 的 native 窗口报告 258400，启动参数却在 84000 token
触发自动压缩。调整前必须先保证受控角色重启不会破坏原审查。
现场仍保持原请求与 Critic 执行，不以监工代替审查结论。

验证时发现既有 `test_restart_blocks_strict_run_contract_drift_before_stop` 在干净
dev 同样失败：它期待 dry-run 写入事件，与已有只读 dry-run 合同冲突。修正测试为
实际 restart 请求，并在重启副作用入口设置失败探针，验证严格漂移先记录 STOP、
再拒绝重启；保留原 dry-run 无事件验证。

验收：
- 重启持有角色替换锁期间执行 reader 恢复扫描 -> verify: 不重复派发、不失败。
- 同版原派发的恢复简报成功投递后扫描 -> verify: 原 Operation/hash/attempt 保持，
  启动事件不再触发失联恢复。
- 错误身份、错误原派发和恢复投递后新的 provider 终止 -> verify: 不被豁免。
- CLI、Reader、Synth、预算恢复及 mock E2E 回归 -> verify: 聚焦闭包与哨兵通过。
- 部署控制面时保留运行中的 provider -> verify: 原 Critic 身份与读取继续，
  再通过受控单角色恢复应用经核验的上下文配置。

相关说明：`docs/impl/reader-respawn-continuation.md`。

已完成聚焦验证：Reader/生命周期/准入/context recycle 244 项；启动与重启 63 项；
最后的恢复调用链复查 44 项；mock flow 10 项；最后哨兵 51 项。`dev-verify plan`
保守扩展到 186 文件/全套确定性测试；按 AGENTS.md 的影响闭包规则执行上述边界，
未把聚焦验证称为全量。现场部署与恢复证据仍待完成。
