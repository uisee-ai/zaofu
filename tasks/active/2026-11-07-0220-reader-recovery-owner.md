# Reader 恢复的代际替代与角色占用保护

> 状态: active

授权：持续监工 LS-v1，发现问题修复后恢复。真实复现：UTC 02:17:44 新请求 `evt-5b69b77b8744` 成功派发（`evt-48077653aae9`），02:17:49 旧请求又派发进同一 Planner（`evt-882f8a1bc197`），tmux 显示旧 briefing 排在新请求后。已受控停止，保留原始证据。

- 新授权代际的 rework_attempt 从 1 开始，旧代码只比较计数递增，无法替代旧代际 -> verify: 相同 Run/stage/任务恢复范围内按请求 ledger 顺序，承认 owner 新代际或显式 redrive，反序启动不能反向替代。
- lost reader 恢复强制 idle，忽略角色已交给另一个 reader -> verify: 有新 owner 时不改状态、不发 briefing、不消费恢复次数；owner 交付后原仍有效任务可恢复。
- 实际 caller 闭包 -> verify: fanout housekeeping、lost reader、回放、角色生命周期与 mock flow smoke。
- 部署 -> verify: 最新请求成为唯一有效 Planner 执行，保留六条反馈，旧请求被有依据地关闭。

验证：针对 reader 生命周期、恢复与 fanout 的 210 项测试通过。`dev-verify.py plan/run --base dev --tests tests/test_reader_recovery_owner.py` 自动影响闭包 579 passed / 7 skipped、sentinel 53 passed、mock flow smoke 10 passed；日志 `/tmp/zf-reader-owner-verification-20261107.log`。测试初次收集缺少 pytest import，补齐后全部通过。

真实账本只读预检：旧 `fanout-prd-plan-evt-ce1aaf72` 被 `fanout-prd-plan-evt-5b69b77b` 替代，后者没有反向替代。部署后继续核验载体及工具调用。
