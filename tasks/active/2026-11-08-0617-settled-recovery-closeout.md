> 状态: active

# 已提交结果后的恢复闭环

用户授权：修复本轮阻塞，修复后恢复原任务并持续监工。隔离分支沿用 task-recovery-20261105，保留35个done及原验收门禁，不将历史失败改成通过。

- R1 有界同目标 Verify 续验：failed/abstained+已准入固定提交 -> verify: 受控恢复保留operation/hash/代次与提交，新attempt重新Verify；旧结果失效且历史可追溯，拒绝产品rejected/passed、活跃attempt、过期检查点及重复恢复。
- R2 已准入结果不走失联兜底：最新pipeline dispatch已有settled结果 -> verify: 不再发orphan升级、不回写backlog；真实无结果worker仍超时。批准/恢复等受控动作可唤醒静默，不能取消所有静默保护。
- R3 必读输出无损缓存：CLI直接原子保存完整required读取，有限分页 -> verify: stdout仅回执，鉴权/digest/逐来源ledger不变；拒绝越界、非忽略文件、已有目标，失败不发布半成品。技能用标准入口，不把read-ledger冒充模型已读。
- R4 Discovery恢复效果：明确绑定的gap计划代表replan请求已产出结果 -> verify: 同run但无因果关联不冒认成功；执行失败无gap仍失败，不把gap准入/计划审批当产品验收。
- R5 现场恢复：修订并审查最新Ingest→Dataset计划的跨工件责任映射；恢复Training Verify与消费者等待任务，修复Ingest锁序由隔离产品worker实施。核实实际dispatch后持续观察，保留风险与失败日志。

验收按变更模块、直接调用方、共享Event/Store/recovery边界闭包执行，另跑sentinel与相关mock E2E；不依文件数量扩大到全pytest。部署仅精确前像/唯一上下文补丁，保留主树并发修改。完成实现后本地commit，不push、不merge。

2026-11-08 实现验证：R1–R4 已完成。定向新增行为16 passed；65文件影响闭包732 passed；sentinel 50 passed/1 skipped；Task Pipeline runtime-noise mock E2E 3 passed；instruction/skill/docs 40 passed/1 skipped。dev-verify --base dev及本批--paths规划已检查，选取器按文件数升级全量（483文件），按AGENTS改为直接模块/调用方/共享边界65文件定向闭包；未执行全pytest或真实provider测试。R5部署与现场恢复仍待完成，保持active。

实现提交：`bf758afe fix: recover settled verifier failures and close recovery effects`。主环境77 passed；控制面重载后Training Verify与Consumer Impl g2实际dispatch，原子必读缓存已真实成功。R5已拉起两worker及有界计划修订Planner；新计划尚待产出/独立审查及批准，Ingest产品修复尚待实施，因此本任务不归档done。现场记录见docs/impl/task-recovery-20261105.md及产品diagnostics/settled-recovery-20261108/。
