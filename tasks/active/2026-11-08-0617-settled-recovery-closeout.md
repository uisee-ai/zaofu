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

现场追加：Planner仍沿旧方法直接输出大段required正文，已发送本次原子缓存/分页指导，并把同一方法补入planner角色技能及两provider分发副本，防止后续计划阶段重复截断。

06:55 UTC现场路径修复：Planner派生JSON写到app/evidence缓存被scope hook正确拒绝，已为既有artifacts/plan/**允许域配置本地忽略缓存并发送纠正指令；技能补充缓存/派生正文同时满足工作区、允许写域、git忽略三条件。未放宽scope hook。

持续监工追加：Consumer baseline遭遇av/Pillow下载300秒超时，已验证持久wheelhouse原锁哈希及29包离线安装并交付worker按原流程恢复；不能计为产品RED。验证日志已持久化，产品任务继续运行。

R1补充：新计划已接管但尚无新operation时，旧Verify恢复必须拒绝；增加当前run/map/package绑定检查。旧实现两个场景RED，修复后84 passed，50 sentinel/1 skipped，3 mock E2E。

R3现场补充：Planner大端口交付必须用ref+sha256，不能用artifact_ref或将全部正文内联。既有运行时可用，已修正briefing遗漏并直接指导当前worker；42项调用方/预检/交接测试通过。R5仍在运行，不能标done。

R3共用提示补充：Critic现场再次选错缓存目录，已在Controlled Artifact Inputs生成入口提供完整output-file用法，覆盖全部角色；具体模型仍须完整消费缓存，禁止凭回执自证已读。

- R6 原生压缩与强制回收竞态：只为当前Codex session+高水位周期提供一次有界宽限 -> verify: 当前压缩不会被立即强杀；旧状态/普通活动不豁免，超期仍回收，新session不继承宽限。旧代码2个RED；生命周期91 passed，身份补充8 passed，sentinel50 passed/1 skipped，mock E2E3 passed。源码/断点保留，部署后继续观察；不替代R5产品验收。

08:16 UTC R5现场进展：Training新Verify已正式passed/admitted，独立探针持久归档；随后candidate smoke因测试解释器配置缺失失败，已核实CPU PyTorch能力、补齐本地忽略配置并经既有recover task-integration重试，等待真实集成结果。Consumer 11个mandatory命令及74个旧失败用例通过，但额外Sources浏览器2pass/1fail仍保留，独立Verify尚未完成。Planner独立Critic拒绝F1历史定位/F2现行审批范围错误后，已受控启动一次限定新代修订；17个原冻结输入恢复至允许缓存，返修继续。35done不变，不能归档R5。详见docs/impl/task-recovery-20261105.md与产品持久diagnostics。

08:17 UTC续报：Training原smoke重跑6 passed并正式集成，task.done evt-e323cb625282，candidate784a5de9，36done；P11A自动派发并与Consumer并行。新Planner正式提交后306项差异检查无失败，独立Critic仍待结论。R1恢复路径已有真实Verify→integration→done证据；R5的计划批准、Ingest修复及后续产品任务尚未完成，继续active。

- R7 忙碌期压缩前置不满足不报执行失败：P11A的Codex尚未发送compact命令，却因idle-only能力记作compact.failed并触发Supervisor修复路由 -> verify: 保留容量warning及pending_recycle，不产生未执行命令的failed/compacted事件或消耗尝试；真正发送后拒绝/失败仍记录failed，硬上限和回收不变。

R7已复现1 RED，修复生命周期及mock context-recycle E2E81 passed、Supervisor/事件27 passed、sentinel50 passed/1 skipped；只纠正未发送命令的事件语义。R5 Consumer实施准入evt-9f6689001697，独立Verify已派发；Sources超时证据显示持续进度，仍为失败且不能定性死锁。计划Critic仍在审查。
