# R43：已替代任务的迟到 Reader 失败不再阻塞 Run

> 状态: active

LS-v1 的 Grants 原 scope 阻塞已通过已审批 Task Map 和 WRC 后继派发解决，但旧 semantic-replan Reader 因过期 attempt 失败，stage replan cap 又将整个 Run 标为 blocked。15项已审批证据范围修订未继续准入。监工通过精确 dismiss 和 goal CLI 恢复，保留历史与全部验收。

## 验收

- 复现旧 Reader 失败晚于正式任务替代 -> verify: 当前 HEAD 测试重现重复 replan/Run blocked。
- 按相同 Run、原失败因果、显式替换关系、WRC 实际准入机械判定 -> verify: 只跳过被完整替代的恢复失败；未批准提案、其他Run/任务/失败、部分覆盖不跳过。
- 接通 reader replan 和 RM terminal 收敛入口 -> verify: 实际入口测试无旧重派/新 blocked；普通新失败仍正常升级。
- 合并部署后观察 LS -> verify: 正式 Run active，三个在途任务保留，审批任务图继续推进；不重写不可变输入、验收或旧结果。

验证：2项旧行为 RED 复现；Reactor fixture 类名初始错误已纠正。12文件定向闭包184 passed（含两个 deterministic mock E2E），sentinel53 passed。首次闭包命令含不存在的 lifecycle 文件未执行，已使用实际 test_workflow_managed_task_lifecycle.py 补齐8项。dev-verify planner 选284文件因数量升全仓；依仓库测试域规则采用明确模块/直接调用/共享生命周期闭包，不执行全仓或新真实Provider E2E。对真实旧失败 evt-62ea8aaba03c 只读重放判定已替代。

运维修复：evt-3cbdca8eb32d 精确关闭旧升级；evt-907fd7c1dee0 恢复同一 Run；15:15:31 evt-cd08bfd99f01 接纳15项后继。30个实际 Impl/Verify 输入预检通过，scope及冻结包原字节保持；车辆15:17实施结果已正式接纳，尚待独立验证。
