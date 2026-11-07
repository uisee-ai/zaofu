# R48：返工范围不得漏掉原合同强制证据

> 状态: active

车辆FMS05-NOOP-NULL已修复为c6be7aa且本地回归通过，但Verify的rework_items.allowed_scope只列产品代码/测试。Impl将其理解为完整写入权限，16:16以scope_contract_gap结算失败evt-9dc5a253502f；原Task Contract本来已允许artifacts/verification/FMS-05/并强制三份文件。R39修复了任务生产者范围，遗漏了Verify返工生产者与Impl如何保留证据义务的提示。

## 验收

- Verify返工输出同时对账修复路径与必需证据路径 -> verify: 方法要求保持原合同授权，只列原有允许范围，不凭evidence_contract扩权限。
- Impl不能将返工代码清单当成删除原合同证据义务 -> verify: 实际Task Pipeline briefing包含该边界，显式禁止写入或真实越界仍走澄清/受控修订；不改冻结结果、不自动扩大Runtime scope。
- 修复在真实调用入口可用 -> verify: task pipeline dispatch/capsule/Verify→rework/result合同闭包、技能元数据、sentinel和199 mock E2E；合并后控制面加载回执。
- 保留c6be7aa，当前失败已结算，走受控恢复交付原三份证据 -> verify: 原AC/命令/owner/tier/输入/其它完成和在途任务不变，后继实际派发，后续独立Verify判定。

方法与实现说明：[证据与范围对账](../../docs/impl/planner-context-and-operation-recovery.md)。本轮不将新派发等同于产品修复闭环；源码修复、交付证据、Task完成分别记录。

验证：dev-verify plan选11文件，run完整执行97 passed、1既有外部配置sensor skipped；补充Verify重试/OA返工/199 mock33 passed；sentinel53 passed；两技能三份分发一致且metadata有效。首次手工测试路径拼错未运行，改用计划器真实闭包后通过。受控恢复dry-run40任务，原scope/AC/命令/owner/tier/证据合同保持，22个done及当前在途逐字段未改；仅未执行下游一对一替换依赖。实际恢复与部署待合并后记录。
