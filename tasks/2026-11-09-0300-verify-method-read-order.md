> 状态: done — e1f1072a `fix: load verification methods before bulk input reads`

# 验证角色在长输入前加载已配置方法

授权：用户要求继续监工 LS-v1、修复发现的问题并记录。本项仅调整 Verify 派发卡的读取顺序，不更改任务合同、必读来源、页大小、读账本或验收门禁。

真实证据：LS-v1 `FMS-24-DEVICE-PICKER-DELTA-01` / `ta-02e70a9d1f42d4490e43` 在现行 `1e835780` 生成的卡片上先完整读取 Controlled Artifact Inputs；角色 skill 已包含精确分页方法，却尚未加载。worker 尝试 `--page-bytes 24000` 被拒；监工 `evt-962f4f5b167b` 指导后从自己的连续位置改用现有精确分页。`docs/records/2026-11-09-device-picker-recovery/verify-reading-guidance.json` 保留事实。一次错误的手工心跳时间未影响 runtime 按实际收到活动判活，不借此修改心跳逻辑。

- [x] 先在真实派发入口复现方法缺位 -> verify: transport 收到的 Verify 卡在大批正文之前没有角色说明和方法加载步骤；当前 HEAD 回归先失败。
- [x] 把角色说明及其已配置 auto-injected skills 的按需加载放在长输入之前 -> verify: 实际首次派发、返工、传输失败后重试均包含确切 instance 说明路径；已保留的方法不重复读取，仍须完整消费所有必读正文。
- [x] 保护身份及原流程 -> verify: Impl 原读取顺序、Verify 合同/target/transport gate、未发送前 TaskStore 不变、失败重试及错身份拒绝的既有测试通过；相关 mock E2E 和 premerge 哨兵通过。
- [x] 本地提交、合入 dev 并记录 -> verify: 精确路径提交，记录实现 hash、验证层级；不声称本提示已降低总体 CPU 或必读数据量，不改写活动 worker 的历史 briefing。

验证：修正测试夹具的 callback role 字符串/DispatchContext 取值后，在旧生产代码上四个 Verify 场景因缺少方法步骤而 RED（14 通过）；修复后 briefing/capsule/role-instructions 定向 32 通过。`scripts/dev-verify.py plan/run --base dev` 选择 13 文件，自动确定性层 122 通过，包含 replan checkpoint mock E2E；serial/host/real-provider/perf 按自动层排除。premerge 文档 currentness 与 53 哨兵通过。没有运行 full suite 或真实 provider E2E。初次夹具 AttributeError 不作为生产 RED 证据。

实现 e1f1072ab55fc39bc2cc7fbdbfbf2a18a80ce1d6；合入 dev 6cce0318a2e69db1f691aa186efa1e666a128e4a。合后 32 定向与 53 哨兵通过；Watcher/Web 已加载合并版本且当前 Verify 身份/pane/PID 保持。证据位于 LS-v1 `docs/records/2026-11-09-verify-read-order/`。未 push。
