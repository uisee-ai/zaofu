# R52：增量任务输入分区交付与准入

> 状态: done

来源：ls-v1 evt-d2f387bbf6d8。新增 FMS-08-GAP-VEHICLE-PORT-02 无原输入分区、无替换前驱，Plan package 已准入但派发被正确拒绝。监工增量预检漏查输入；candidate-rework 也未携带新 Plan ports。用户已授权监工发现并修复阻塞。

- 修复 controlled candidate rework 传递已固定摘要的新输入端口 -> verify: 经真实 ControlledActionService 产生 task_map.ready 且端口可解析；坏摘要拒绝，不冒充已有分区。
- Package 准入验证本次待派发任务的 scoped input 与显式替换链 -> verify: 新增无分区拒绝、显式分区通过、真实替换继承仍通过；保留在途旧绑定。
- LS 修复使用新不可变输入完整保留原 shared/全部任务body，并明确一个分区的身份映射，仅替换从未派发的新生产者 -> verify: 原工件摘要与旧40任务绑定不变，准入/批准/派发事件记录。
- focused impact closure、sentinel、mock runtime -> verify: 记录确切结果；合并 dev 后受控重载，保留活跃 worker。

不放宽任务读取/来源守卫，不更改 Owner 待决，不伪造 predecessor，不直接改 canonical stores。运行态恢复结果单独记录，不以代码测试冒充产品完成。

验证：当前 dev 直接入口复现受控动作丢 plan_ports 与无分区准入；首次 pytest 跨 checkout 导入被正确拒绝，改为当前 HEAD 独立调用复现。修复后影响闭包 357 passed；serial 10 passed；sentinel 53 passed；mock flow 10 passed。dev-verify 默认因297文件扩大到全量；依仓库“按影响闭包、非文件数量触发全量”规则，run --skip-tier deterministic 替换为上述明确 focused pytest，未跑全 pytest/真实 provider E2E。命令首次误传不存在测试文件未执行，已纠正。运行态恢复仍待合并后验证，因此保持 active。

实现 commit：`b629d230 fix: deliver scoped inputs for appended recovery tasks`；dev合并 `1b30cd5c`。
现场恢复已确认：新 Plan package evt-6ea3d3bafffb、generation evt-7512eea599b4、实际派发 evt-6e8b7d2d9dc8（18:43:42 UTC），attempt ta-1367e9f76ceb941b7046。Canonical source manifest 精确绑定新输入abad6f…及 $.shared / $.tasks[22]。旧40Task输入绑定前后相等；原02从未派发，已受控替换为03。
容量预检发现全别名复制超过4MiB，未发布；修订后完整保留原34个分区body与shared，仅明确变更新生产者所用行的task_id，原输入91dfc2…通过额外baseline端口固定。此为本次新生产者绑定，不把新包当作其他Task可省略溯源的许可；将来恢复仍须使用该Task自己的固定输入并提供明确分区。新输入4,133,362字节可解析。没有放宽读取限制/伪造替换。
现场完整记录：ls-v1/docs/records/2026-11-07-r52-recovery-confirmed.json、rtk-input-port-repair-preview/result.json、three-blockers-recovery.md。产品端口仍在实施，本项done仅指控制面修复与真实恢复派发，不是产品任务验收。
