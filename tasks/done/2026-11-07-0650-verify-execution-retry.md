# 补齐 Verify 执行失败后的受控续接

> 状态: done；实现 `52395e93` — `fix: retry failed verification execution without rerunning implementation`

依据：持续监工修复授权；[199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

网关 Verify 在输入生命周期误报后，交付已准入 execution_status=failed/verdict=abstained（evt-3c253a91926d），未判产品失败。WRC 正确不触发 Impl 返工，但 repair 后没有 Verify-only 的受控恢复入口；recover workflow 为 no_action，现有 task-impl/task-integration 均不适用。RM/OA 没有此执行动作，诊断或修复代码不能自动转换为验证续接。

1. 增加 task-verify preview/apply，复用 operation supersede 和 WRC 新代次 -> verify: 只恢复 Verify，保留成功 Impl、原失败证据和精确 target；重复 apply 仅一次。
2. 恢复前核验已准入非产品失败、当前 Task/合同/run/generation/目标与无活跃写入者 -> verify: 产品 fail/pass、缺证据、损坏摘要、旧身份、新 attempt、暂停/终止、目标变更均拒绝，零写入。
3. 普通任务与 verify_only 入口一致 -> verify: 实际 CLI → event reducer → WRC dispatchable 的行为测试，影响闭包/sentinel/mock。
4. 合并部署后网关实际续接 -> verify: 新 Verify 派发且原 Impl 不重跑，记录真实结果；本轮不声明产品通过。

## 软件验证

- 首轮聚焦 47 passed/1 failed 为新 attempt fixture 漏必填 operation_id；修正后 59 passed；补足 request/attempt/generation 和连续重试后 64 passed。CLI 实际入口、reducer、WRC 选择及 prepare_verify_target 自检准入均覆盖。
- 首次影响闭包 1,718 passed/1 failed，生成 CLI 手册未同步；generator 更新两份命令目录后，最终 159 文件闭包 **1,728 passed**（61.86 秒）。
- 最终 sentinel **53 passed**；serial **10 passed**；flow/runtime-noise mock **10 passed**。dev-verify 的按文件数升级全量已按 AGENTS.md 停止，采用其全部 selected_tests 显式影响闭包；未声称全量或真实 Provider 验证。
- 真实 gateway preview 接受原 target 99f1eb98、下一代 Verify 3，尚未 apply；合并加载后继续验证实际派发。证据 /tmp/zf-verify-retry-{focused-v2,impact-v2,gate-v2,serial,mock}-20261107.log。

## 实机效果验收

dev 0dfc25f6，Watcher PID 2134687，runtime-load/0e2365303f8f4616acff7287ab2680a3.json 绑定该版本；控制面重载前后全部 Worker pane/PID 一致。public recover task-verify 07:04:53 请求 evt-d24d5a3c8061；07:05:04 WRC 实际派发 Verify g3 evt-8bc668d2c0c3 / wop-verify-tp-FMS-20-GAP-GATEWAY-02-be27e32-af1730c9e811。原 g2 Impl 仍 passed/settled；目标 99f1eb98 和自检摘要 7d085ed2f9dd07ca057cae9628d07726a41b30b833db273d6846249711cb541f 不变，恢复后无 Impl 重派。新 Verify 已开始读取。

证据：LS-v1 docs/records/2026-11-07-gateway-verify-recovery-effect.json、2026-11-07-verify-retry-reload-after.json 和持续监工问题记录。合并后聚焦 64、sentinel 53、mock 10 通过；源 worktree 已按已合并、无活跃进程及仅缓存/空锁审计后删除。

本任务关闭的是输入恢复/阶段续接的软件义务；不代表网关产品 Verify 已通过，也不代表 RM 已自主证明源码补丁效果。监工提供修复证据后请求受控动作的事实保留。
