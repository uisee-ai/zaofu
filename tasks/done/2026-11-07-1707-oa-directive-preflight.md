# R50：OA 引用协议错误应在结算前反馈

> 状态: done — `962f6118 fix: validate OA directive references before settling results`

LS-v1 治理 FMS22 的 OA 决策 evt-8751a11c1edd 于17:04被 CallResult 接纳并结算，随后 evt-ab007435973a 因 directive_ref_outside_operation 拒绝执行。OA引用了正文中的子工件，而不是 source manifest 的直接授权来源；原门禁正确拒绝，但生产者已认为交付完成，恢复停在建议之后。

## 验收

- 同一 source manifest 的 ref/digest 准入规则用于提交前与执行时 -> verify: 不递归扩权、不修改冻结输入；basis/reuse/invalidate 越界均拒绝。
- 真实 result validate/submit 在错误决策消耗 capability、结算 operation 之前反馈 -> verify: 失败无canonical决策或结算事件，同一operation修正后可成功提交且不能重复消费。
- 方法明确嵌套链接不自动成为可执行引用 -> verify: OA依据当前manifest引用父工件并说明定位，确需新增引用走受控输入修订；不改旧结果。
- 合并加载后受控恢复当前治理任务 -> verify: 保留失败与已提交决策，新的诊断/续作通过现有受控入口；记录实际派发，不能把入场当作任务完成。

运行时只提前检查机械协议；语义返工判断仍由OA负责，下游执行的身份/currentness/范围校验继续保留。

验证：先复现三类引用未在submit拒绝（3 failed；此前测试import路径笔误已纠正），修复后40项聚焦通过。dev-verify plan/run执行76个测试文件影响闭包，1277 passed、8 skipped；sentinel53 passed；flow mock10 passed；199恢复mock与现有operator retry27 passed。技能metadata和三份分发一致。未运行全量pytest或声称真实provider E2E。

当前恢复预览通过，next generation=2，原source=evt-f8dcf89ce9cc；计划使用现有recover task-impl受控入口，明确原诊断不能证明上游阻塞，保留原合同与独立验证。实际加载、派发及归档待后续效果记录。

实际效果：dev cc6a88b3，控制面PID3539594于17:12:49加载。历史真实OA payload在新预检中按原白名单拒绝，无需改写旧工件；旧结果仍保留拒绝。现有recover task-impl申请evt-8d8243360a5b，17:13:46实际派发evt-1aed4817a111，FMS22第二次Impl operation `wop-impl-tp-FMS-22-GAP-EVIDENCE-01-86614e-d6d2cc2f78a4`、attempt `ta-950c95cd118108369413`、dev-lane-1、pane %1634/PID3541341；workdir仍基于173c951，范围和合同未改。原visual/vehicle Verify工作进程均保留。源码修复闭环，治理产品实现与独立验证继续跟踪，未宣称Task完成。

限制：本次存量任务恢复由用户授权operator完成；未来相同引用协议错误在submit前反馈，并非所有执行后currentness拒绝已有自动恢复。LS项目已有未提交OA技能文件未覆盖，仓库canonical方法与两份分发已同步。详细现场记录在LS-v1/docs/records/2026-11-07-r50-controlled-reload.json、2026-11-07-r50-governance-recovery-result.json和2026-11-07-three-blockers-recovery.md。
