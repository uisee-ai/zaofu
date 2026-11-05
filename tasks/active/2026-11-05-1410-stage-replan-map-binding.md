> 状态: active

# Reader stage 恢复引用一致性

P16B 恢复发现已接纳 Run Plan 与原任务派发分属不同代际，这是 gap-only 运行的合法状态。不能用旧任务包覆盖 Run 当前 Plan。真正的自动恢复缺陷是 stage replan 只重绑包与 generation，却保留旧 task_map/source_index。

- 以当前已接纳包的 source_event_id 绑定 map/index 引用及摘要 -> verify: 先 RED（old-map != current-map），后 GREEN；晚到未接纳草稿不覆盖当前绑定，缺新摘要不沿用旧摘要。
- 保留失败证据和候选 target -> verify: stage、run-manager、受控恢复测试104 passed。
- 执行 contract sentinel 和 mock E2E -> verify: 归档时记录结果。

实现说明见 [task-recovery](../../docs/impl/task-recovery-20261105.md)。不修改 canonical TaskStore，不放宽包身份门禁。

验证：上述定向104 passed；dev-verify contract 50 passed / 1 skipped；mock E2E 10 passed。与首批相同，自动扩大为全套的 deterministic/serial 层由定向调用闭包替代。已部署此独立模块到现有运行源码；受控规划恢复调用新代码，无需中断三个在途 worker。受控授权事件 evt-aefbdf7f3628，代际 stage-replan-b58909c053ce2bd964dc。
