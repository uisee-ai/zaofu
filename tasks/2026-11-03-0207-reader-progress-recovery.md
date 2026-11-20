# P1：Reader 读取推进后再次断线耗尽恢复额度并误触发 Plan 重规划

> 状态: done — `58eff080` fix: preserve plan and reader checkpoints across transport recovery。

Owner 已授权持续监工、修复恢复问题并合并 dev。DL 在 `evt-45cde7953353`
和 `evt-9ae31aba9802` 两次收到 Provider `server_overloaded`。第一次自动重投
沿用 request hash 和读取断点；第二次虽新增 72 页，却被固定一次恢复额度判为
`worker_noop_or_terminal_missing`。聚合随即把未产出的计划误报为端口缺失，
`evt-b2aeada47d63` 触发新 Planner，只剩 10 项必读，原 33 项中 20 项已完整读取。
已用受控 `run-pause` 暂停后续派发（`evt-8a1f7045aaed`），原账本和候选均保留。

1. 当前 HEAD 复现重复断线 -> verify: 第二次断线前新增的唯一必读覆盖应允许同请求继续，重复页、错身份和损坏账本不增加额度。
2. 正确分类无产出执行失败 -> verify: 实际 Plan 聚合入口不把 Reader 传输失败转换成产品端口缺陷或语义重规划；无进展保护与正常语义拒绝仍有效。
3. 验证恢复链 -> verify: 影响闭包、sentinel 和 mock E2E 通过；通过受控入口恢复有效候选及读取断点，不写伪造读凭据、不直接改运行态。
4. 合并部署并继续监工 -> verify: 原失败事实留存，旧 nonretryable Verify 不重试，新工作只在合法合同和准入下派发。

实现说明：[Reader 传输断点恢复](../docs/impl/reader-transport-checkpoint-recovery.md)。
2026-11-04 只读核验：原 operation/hash/attempt 完整，有效必读覆盖 7,197,493
字符，20/33 切片已完成。仅误开的一个后继 fanout 需要被原检查点取代。

验证边界：`dev-verify.py plan --base dev` 因大型 Orchestrator 的导入图扩展到
471 个测试文件，自动建议全套。依 AGENTS 的影响闭包规则，不仅因这一数量运行
全套；显式覆盖 Reader/Synth 恢复、CLI/lifecycle 直接调用者、Fanout/Operation/
Run/Event 契约，再用 dev-verify 执行 sentinel 和确定性 mock flow 层。

代码验证完成：最终影响闭包 620 passed / 7 skipped；合并哨兵 53 passed；
mock flow 10 passed。实际 CLI 恢复测试覆盖旧版缺少 producer 的错误派生链，
请求/预算/账本不变、Run 恢复事件顺序、下一次断线、错误阻塞拒绝与中断重放。
2026-11-04 时暂保持 active，等待合并部署后核对真实读取续跑再归档。

2026-11-17 收尾：来源修复已合入当前 dev，适配现有 Reader recovery 调用者并通过
最终 900 项恢复/安全影响闭包。真实 DL 原 Planner 已产出，当前恢复其原 Plan
Critic operation；同时修复 Synth 超时恢复和长期停机凭证过期。Run、请求哈希、
attempt、候选输入及历史读取账本均保留，实际 Provider 已重新接收有效分页输出。
旧分页 bundle 不兼容会保守重读，因此不声明零重复读取或 Plan 已获批准。
部署、备份和旧服务目录退役证据见
[服务收敛报告](../reports/worktree-archives/20261117-dl-service-consolidation/README.md)。
