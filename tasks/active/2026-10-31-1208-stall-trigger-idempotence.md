> 状态: active

# 停滞补发与原触发的派发幂等

持续监工授权范围内复现：受控新 Plan 触发 evt-f177941af091 尚未消费时，停滞检测器按事件数阈值补发 evt-d22c30daa50f。两份语义相同的事件产生两个 fanout，较新 fanout 又取消已正常启动的 Planner。Kernel 最终收敛到单操作，但造成重复派发、briefing 更换与浪费；受控维护脚本检查到旧操作已取消而主动停止，未中断仍在运行的新 Planner。

- 自动补发与原触发合并派发 -> verify: 两种消费顺序均只创建一个 fanout，重复 tick 不增加派发/抑制事件。
- 保留真实新代际 -> verify: operator 新 rework 仍独立派发，其他阶段/运行/变更后的输入不被误抑制。
- 实际运行入口 -> verify: reader/writer runtime 接线、既有停滞恢复回归、sentinel 与 mock E2E。

不提高预算，不修改旧结果，不手写 canonical state。完成后合并 dev 并继续监工。

验证：基线 2 failed / 1 passed；Reader/Writer 两种消费顺序、独立 rework、stage/run/payload/task 隔离与完整 reader/writer/停滞恢复闭包共 374 passed；EventWriter、operation 和确定性 OA/审批 mock 共 26 passed。自动计划选择 184 文件触发全量回退，按普通修复规则跳过 deterministic 全量层，以以上 12 个文件作为显式闭包；serial 10 passed、sentinel 51 passed、flow smoke 10 passed。Ruff/diff 检查通过；没有运行全量 pytest 或真实 Provider E2E。
