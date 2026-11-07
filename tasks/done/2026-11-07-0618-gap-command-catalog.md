# 保留增量任务的结构化验证命令

> 状态: done；实现 `51ebe834` — `fix: preserve structured verification catalogs in gap amendments`

依据：持续监工修复授权；[199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

## 真实问题

LS-v1 FMS-13 discovery 提交两项 gap task，flow.gap_plan.ready evt-e822fbe66701 携带 verify_commands 对象列表（完整 id/command/acceptance_ids/owner/tier/timeout/rolling_smoke）。module_gap_plan 将该列表逐项 str，既丢验证 ID，又把对象 repr 当命令；task_map.amend.failed evt-150d66427514 报 CHK-13-CONTRACT/CHK-13/E2E-13 不存在。独立 Task Map 验证正确拒绝，尚未派新任务。

旧修复仅覆盖 verification.commands 字典和 validation.commands；测试未覆盖同一规范已支持的 verify_commands 对象列表。OA 已交完整语义，RM 未在增量适配前发现字段损坏，也无修复代码能力。本轮不代替 Q09 的业务批准。

## 验收

1. 统一已有命令字段的结构化适配并保留原元数据 -> verify: verify_commands/verification 对象列表以及旧字典均保留 ID、所有命令字段；命令字符串不变、不生成放宽验收的默认决定。
2. 冲突和无效命令继续拒绝 -> verify: 两份不一致目录拒绝；未知引用仍失败；旧字符串合同继续兼容。
3. 实际 flow.gap_plan.ready → Task Map → Task contract -> verify: 准入和原命令消费正常、重放不重复派发。真实 LS 输入只读重放确认原因已消失。
4. 聚焦/影响闭包、sentinel/mock、合并 dev、控制面受控重载；使用既有 stage-retrigger 只重放失败 amendment -> verify: 保留原发现和当前在飞 Worker，记录结果，不重新运行 discovery 来修适配代码。

## 验证

- 初次复现 5 failed / 1 passed。首轮影响闭包 2,718 passed / 1 failed（旧 malformed gap fixture 的拒绝约束被额外别名放宽），已收窄为原有 verification/verify_commands；初次 sentinel 52 passed/1 failed，适配迁入已有专责模块，未放宽结构门。
- 最终聚焦及 Reader caller 231 passed；188 文件影响闭包 2,718 passed（83.76 秒）。serial 10、最终 sentinel 53、mock flow 10 通过，ruff/diff check 通过。
- 真实 LS 失败产物只读重建与验证通过，35 项候选 map，原命令 ID 和元数据保留；记录 LS docs/records/2026-11-07-gap-command-catalog-replay.json。尚未以此替代线上状态或 Q09 批准。
- 待合并、加载和受控恢复。日志 /tmp/zf-gap-command-{focused,closure,gate}-v3-20261107.log，tiers/mock 日志同前缀。

## 实际交付验收

- dev 合并 1ebf91ff，控制面运行版本回执 d6d1e91430014ea68e8617640721f6b8；在飞 Worker pane/PID 保留。
- 只重放 TODO 的失败 amendment：evt-bb16bd8e720a → task_map.amended evt-c81ed1ad2adf → task_map.ready evt-3beec210c3d2。原有命令身份与元数据准入成功。
- 既有 approve 授权下审阅两个增量任务后，plan.approved evt-b8e0262aea7b；任务准入 evt-ffae3f1fdd47；06:47:05 TODO-PORT 实际派发 evt-c47342450904。未重跑 discovery，未重启在飞任务。Q09 的业务待决保留。
- 证据：LS-v1 docs/records/2026-11-07-three-blockers-recovery.md 及 todo-gap-controlled-redrive.json。源码 worktree 已在合并验证后清理；本任务归档只关闭结构化命令恢复义务，不把业务完成数计为增加。
