> 状态: done

实现：`d94afe63 fix: preserve reader dispatches across controlled role restarts`；
已合入 dev：`cf041e73 fix: keep active reader reviews intact during role restart`。

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
未把聚焦验证称为全量。合并后 11 项回归和 51 项哨兵通过；DL 配置相关既有测试 34 项通过。

06:26 UTC 控制面部署 cf041e73，`session.started` 明确 workers_preserved=true，
Critic provider 与 Autoresearch sidecar 的 PID 均保留。06:28 UTC 通过
`zf restart plan-critic` 恢复原 provider session `01a1947c-cf3a-77a2-bab5-6798206f0757`，
实际进程参数为 model_auto_compact_token_limit=180880。
恢复事件 `evt-8006ab05076f` 绑定原 dispatch `evt-379dff2056da`、Operation 和 request hash；
06:29:27 UTC 在同一个 task_map.json 摘要上继续读取 [221184,229376)。
Operation 保持 running，未消耗新语义重试，Critic 结论仍由原审查角色独立提交。

现场收据：DL `artifacts/supervision/reader-respawn-deployment-2026-11-01.json`。
06:32 UTC provider 实际输入已达 123811 token，超过旧 84000 阈值仍继续读取；
报告的真实窗口仍为 258400，证明配置已生效而未伪造 provider 容量。
本次两处源 worktree/分支均在合并、验证、缓存与进程检查通过后正常删除，未 push。
