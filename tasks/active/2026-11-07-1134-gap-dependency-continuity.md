# 依赖被正式替换后继续任务恢复

> 状态: active — 用户授权监工核实并修复阻塞。

LS-v1 FMS02恢复提案evt-0a036ebd9262在evt-20faf535cb31被拒：旧代6a4790与当前8f8任务仅blocked_by由TODO换为PORT/IMPLEMENT，wave从4调整为5，其他字段完全一致。现有跨代gap校验逐字比较整个任务，未识别已经准入的机械依赖替换。

1. 已准入依赖拆分复现 -> verify: 当前HEAD回归先红。
2. 沿同Run不可变包链逐步核验，仅接受正式amend声明的后继依赖重接及既有raise_dependency_waves算法结果 -> verify: 任意依赖删除/添加、伪造替换、额外wave提升、语义字段变化均拒绝。
3. 源Reader合同与输入绑定保持，输出仍基于最新完整图 -> verify: 实际gap桥接入口保留无关任务并只派发新gap；原输入变化仍拒绝。
4. 合并dev重载、重试已有提案 -> verify: 真实准入/失败证据、在途worker保留，不伪造产品完成。

关联：[199实现](../../docs/impl/199-worker-recovery-implementation.md)。

验证记录：新增两个实际入口用例先红、拒绝用例原先通过；定向52项通过，补中间语义变动回归后14文件影响闭包226项通过，pre-merge哨兵53项、deterministic mock E2E 3项通过。dev-verify plan保守推导181文件要求全量，本轮按实际gap桥接、模块转换、受控rework、Package及Task输入边界收敛为上述14文件；未运行全量pytest/真实Provider E2E。现场只读基线已越过误判，下一门正确拒绝Reader漏输入选择；先修提案再受控续接。
