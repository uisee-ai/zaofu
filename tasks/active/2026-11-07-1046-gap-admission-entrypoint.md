# Gap 准入失败的无 Task 入口闭环

> 状态: active

## 故障与范围

本轮 `evt-610fe9a4b772` 因新增 core 任务缺 `rolling_smoke` 被拒。
此前 gap 因果识别与有界 reader replan 修复可正确解析失败链，但仅覆盖 registry 直接调用；
真实 `task_map.amend.failed` 无 task_id，既不在 wake patterns，也不在 Kernel liveness
集合，因此 watcher 未立即唤醒，run_once 的 taskless/Layer 2 分支也不调用该 builtin。
本轮虽已耗尽同 scope reader cap，却未生成对应升级事件，最终退化为 orphan/quiescent。

仅将该特定事件加入两个既有路由枚举；不扩大所有 builtin 的权限，不调整 cap、Task
状态、运行态、scope 或质量门禁。用户已授权修复；运行恢复由主会话统一执行。

## 验收与证据

- 单独的无 task_id 准入失败 -> verify: 真 EventWatcher/CLI batch/run_once 链即时调用既有恢复逻辑。
- Layer 1 与启用 Layer 2 的配置 -> verify: 同一机械事件均归 Kernel，不交给 orchestrator agent。
- cap 尚有额度/耗尽 -> verify: 分别产生一次带原 gap/拒绝原因的 reader trigger，或一次 cap 升级并收敛；重放不重复。
- 既有因果与跨 run 边界 -> verify: gap recovery、stage replan、resume、watcher 闭包不回归。

RED: 真实入口8例全部失败。GREEN: 8 passed。
Focused（8文件）: 218 passed / 7 skipped。
Premerge sentinel: 50 passed / 1 skipped；flow mock E2E: 10 passed。
`dev-verify plan/run --base dev --paths ...` 生成 scoped receipt；自动207文件 broad selector
不作为本次 full pytest 授权，显式跳过 deterministic/serial 全套，以上述 focused 替代，保留 sentinel/flow mock。
证据 `/tmp/zf-supervision-20261107/gap-entry-*`。

实施说明：[Task Pipeline 断点修复](../../docs/impl/task-recovery-20261105.md)。
本批不提交，由主会话核验后统一记录实施 commit 并归档。

恢复基线由主会话在执行前重验：10:46 UTC Media/Retention 已正式集成，候选更新为
`4a99fd888e87ce546b64385f256ec6764dd334eb`，保留29 done；不得沿用分析初期的27 done/3d405d2快照。
