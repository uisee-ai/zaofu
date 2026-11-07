# R53：语义结果拒收后自动恢复协议纠正

> 状态: done — `7f6d42dc fix: resume rejected semantic results through protocol correction`

用户已授权持续监工、分析修复阻塞并记录。基于 dev 65266d0c，单独 worktree 实施。

## 复现与根因

LS-v1 OA operation wop-orchestrator-agent-pre_impl-checkpoint-revision-2c2a36981309-0d0ba8bf5e3c 在 19:13:53 UTC 缺少当前 attempt 的 effective-config 阅读凭证。SemanticResultSubmitService 正确拒收并发出 evt-f48042587654（repair_round=1）；provider 随后结束 turn，operation 仍 running。现有 correction 派发只接在 legacy result admission，repair.requested 未被 runtime 消费且问题注册表为 observe/no owner。Submit once 未解释有界协议修复；required_read_missing 指导错误地建议替换 schema 字段。

## 验收

1. 补通 runtime 对 durable repair.requested 的消费 -> verify: 实际提交拒收、provider turn closed 后同 operation 的精确补正被送达；无需 OA/RM 重规划。
2. 生命周期与重放安全 -> verify: operation/hash/attempt/dispatch/Run 终止和取消守卫；相同修复只派发一次，未结束 provider turn 不抢派，旧 correction 不能落到新 operation，传输失败保留可重试意图。
3. 阅读补正方法 -> verify: required_read_missing 要求真实受控读取，禁止编辑 ledger/input_consumption，缺页补读后可同操作准入；不降低必读门禁/重试上限/语义证据。
4. Caller 接线及文档 -> verify: Orchestrator 已有 sweep 真实调用、mock provider 路径测试、聚焦影响闭包、dev-premerge sentinel、相关 mock E2E。更新 docs/impl/199-worker-recovery-implementation.md。
5. 本地提交与 dev 合并，控制面受控重载 -> verify: 记录 source revision、保留在途 worker，持续检查实际恢复；不 push。

## 实施与验证记录

- 修复在同一结果重放服务中消费 semantic-submit 的持久化纠正，保留原 operation/attempt/dispatch 和 Provider session；原消费门禁不放宽。精确补读指导禁止伪造读取证据。
- 247 个影响测试文件显式执行（4 workers，300 秒上限）最初 3613 passed / 7 skipped / 1 failed，126.45 秒；唯一失败是新增 wake 事件未更新数量快照。补齐 192→193 的具名原因和 Kernel-only 分类断言。最新聚焦回归结果见后续归档。
- dev-verify plan 的自动规则因 >80 文件会升级整仓，本次遵守 AGENTS.md 的影响闭包要求，显式运行其全部 247 selected_tests；同时运行 dev-verify run --paths tests/test_call_result_repair_reconciliation.py，不把该小集合冒充完整闭包。
- serial 10 passed；dev-premerge sentinel 53 passed；三流 mock E2E 10 passed。未运行全仓 pytest、host 或 real-provider E2E。
- OA 现场本次由 operator evt-c59f989cbe05 指导恢复，evt-948add4f6618 已获准入；此事实不算尚未部署修复的自动恢复证据。

- 最终聚焦 115 passed（含 wake 快照及边界分类），新增文档影响 8 passed。合并后 dev 聚焦 107 passed、sentinel 53 passed。完整影响闭包中唯一原失败已关闭，无新增未执行测试文件。
- dev 合并 ebf5e59d，19:28 UTC 控制面重载 PID 3900120；加载收据 d3f4f24e1c8a4e34aa785287cccfb37b 明确同版源码，4 个在途 Task operation 的 attempt/dispatch/role/task 与全部既有 pane/PID 均保留。真实 OA 已由监工指导恢复，暂未发生部署后的自然协议拒收，不能声称已完成真实自动恢复演练。
