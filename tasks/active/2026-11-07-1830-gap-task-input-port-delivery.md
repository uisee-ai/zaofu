# R52：增量任务输入分区交付与准入

> 状态: active

来源：ls-v1 evt-d2f387bbf6d8。新增 FMS-08-GAP-VEHICLE-PORT-02 无原输入分区、无替换前驱，Plan package 已准入但派发被正确拒绝。监工增量预检漏查输入；candidate-rework 也未携带新 Plan ports。用户已授权监工发现并修复阻塞。

- 修复 controlled candidate rework 传递已固定摘要的新输入端口 -> verify: 经真实 ControlledActionService 产生 task_map.ready 且端口可解析；坏摘要拒绝，不冒充已有分区。
- Package 准入验证本次待派发任务的 scoped input 与显式替换链 -> verify: 新增无分区拒绝、显式分区通过、真实替换继承仍通过；保留在途旧绑定。
- LS 修复使用新不可变输入完整保留原 shared/任务语义，仅替换从未派发的新生产者 -> verify: 原文摘要与旧40任务不变，准入/批准/派发事件记录。
- focused impact closure、sentinel、mock runtime -> verify: 记录确切结果；合并 dev 后受控重载，保留活跃 worker。

不放宽任务读取/来源守卫，不更改 Owner 待决，不伪造 predecessor，不直接改 canonical stores。运行态恢复结果单独记录，不以代码测试冒充产品完成。

验证：当前 dev 直接入口复现受控动作丢 plan_ports 与无分区准入；首次 pytest 跨 checkout 导入被正确拒绝，改为当前 HEAD 独立调用复现。修复后影响闭包 357 passed；serial 10 passed；sentinel 53 passed；mock flow 10 passed。dev-verify 默认因297文件扩大到全量；依仓库“按影响闭包、非文件数量触发全量”规则，run --skip-tier deterministic 替换为上述明确 focused pytest，未跑全 pytest/真实 provider E2E。命令首次误传不存在测试文件未执行，已纠正。运行态恢复仍待合并后验证，因此保持 active。
