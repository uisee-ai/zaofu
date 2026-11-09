# 恢复队列已处理动作不得饿死后续动作

> 状态: done — `2f8910ca` fix: keep delivered recovery actions from starving new work

依据：LS-v1 持续监工授权“遇到问题修复并记录”；设计 [199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

## 复现与职责

当前 dev 623b4a97 的真实 RM projection 一直选择 failure-diagnosis:evt-a1d635a3f3bb；03:26 的 prompted/applied 已使 _action_seen=true，03:59 effect.failed 后该待办仍可见。04:45 tick evt-4485a6e9a03c 仍为 actions_applied=0 / continue_waiting，后续三个未处理诊断都不能被选择。任务流水线继续运行，不应为这次修复中断 worker。OA 不负责 RM 的调度去重；RM continuation 与执行时的可派发谓词不一致。

## 有界实施

- 保留待办、案卷及效果证据；选择下一动作时复用执行器现有去重判定，不自动关闭故障，也不豁免安全门禁。
- 默认 tick 与显式 action_filter 使用同一可派发选择原则；一次 tick 仍最多选择一个动作。
- 旧请求超时后按既有规则可重试，暂停、作用域人工门禁和当前案卷锁不变。

## 验收

1. 已 seen 队首与未 seen 后续并存 -> verify: 真实 projection 选择后续，旧待办/事件保留。
2. 执行实际 run_manager_tick -> verify: 后续产生持久化诊断请求，同一动作重复 tick 不重复副作用；显式筛选亦不被 seen 队首卡住。
3. 队首唯一或后续被人工 gate/案卷阻挡 -> verify: 不重发旧动作、不绕过门禁；可重试过期请求保持原资格。
4. 聚焦调用链、影响闭包、sentinel 与 mock flow -> verify: 通过并记录层级；独立 worktree 提交合并，不 push。
5. 生产部署选择安全点 -> verify: 不为非阻断修复打断活跃 worker；若仍活跃则明确待加载版本，不声称线上修复已生效。

## 验证与交付

- dev 原始复现 3 failed / 3 passed；新门禁负例发现显式 action_filter 可绕过人工等待，已统一选择器；第一轮扩展回归 1 failed / 4,618 passed / 7 skipped，修复被拒过期动作缺少审计回执。
- 最终直接调用链：258 passed；新增回归 10 项涵盖默认/显式筛选、幂等、暂停、人工等待和过期诊断资格。
- `dev-verify plan --base dev --json` 给出 342 文件影响闭包。自动 planner 仅因超过 80 文件扩为 full；按 AGENTS 的分域规则执行精确 342 文件，4,619 passed / 7 skipped（200.07 秒），不把它称作 full pytest。
- `dev-verify run --base dev --skip-tier deterministic` 配合上述精确闭包：serial 10、sentinel 53、mock flow 10 通过。日志 `/tmp/zf-recovery-selection-closure-final-20261107.log`、`/tmp/zf-recovery-selection-tiers-final-20261107.log`。
- 真实 LS 只读选择回放：已 seen 的旧队首切换至未处理下一诊断，四条原待办和案卷保留。证据在 LS `docs/records/2026-11-07-recovery-selection-replay.json`。未在真实状态目录执行修复代码中的动作。
- LS 当前活跃 worker 不受此问题阻塞；本轮不为非阻断修复打断它们。软件合并后仍需在安全点加载常驻 Runtime，并记录真实效果；本任务保持 active，待加载核验后归档。

- 05:07 通过既有 source-repair actuator 仅重载控制面，session.started evt-6f490b617f84、loop.started evt-d695f368f4cc；PID 1650845 的 runtime-load receipt 确认 da14f7fd，三个 worker pane/PID 保持、无重复派发。05:08 真实 projection 已选下一诊断。本轮选择器修复验证完成；下游消费者交接仍有独立缺口，续入 2026-11-07-0521-diagnosis-consumer，不称整条恢复链已修复。
