# 连续 Plan 修订后的已完成任务证据恢复

> 状态: done

用户已授权 8×8 真实验证中发现问题后修复、记录并断点恢复。本项只修改隔离 harness 工作分支，不合并 dev、不 push。

## 真实复现

窗口8 Planner/Critic 已原生通过，新 Plan `evt-b39d507a9135` 经委托审批
`evt-98a7951862b8` 后准入 `evt-3f815fec2b82`。正常停止后，原生
completion reuse preview 报 `completion reuse lacks native completion/Verify: TASK-ASCENDING`。
上一代 87e7 的完成来自已批准的复用记录，原始独立 Verify/集成仍在更早代际；
只查上一代 task.done 的实现没有消费该链。新任务合同另有一项增量：
TASK-AVERAGE 的最终 candidate_verify 浏览器命令增加7个 producer_paths；
Task Verify 的行为、命令、范围均未变，不能默默忽略新最终验收义务。

控制面启动时同步 systemctl start 等待 notify，造成观察脚本未能及时进入准入监视；
本次已额外正常停止，10个重派工作区需复核。后续操作者脚本用 --no-block；
这是本 session 操作问题，不伪记为 ZaoFu 业务失败。

## 验收

- 连续两次精确版本批准与复用 -> verify: 原生完成/独立 Verify/集成引用保持，第三代 Candidate 可建立当前授权，无伪造 task.done。
- 复用链缺失、损坏、目标/当前合同/原验收变化 -> verify: fail closed，TaskStore 不变。
- 只允许标准开发任务的既有最终 candidate_verify 命令追加产出路径 -> verify: 需明确语义复核，完整新义务进入 manifest，Task 本地命令/审计任务/命令/权限/证据删除不得借此豁免。
- 真实原生预览/受控复用/最终 Candidate 调用方 -> verify: 16完成证据有效，保留旧 Reader attempt 3/3、旧失败和所有人工窗口记录。
- 定向影响闭包、sentinel、mock E2E -> verify: 保存具体层级与日志，不称为全量测试或无人通过。

## 实现验证

基线真实预览失败；回归最初2 failed/1 passed，其中连续复用为行为RED，新增helper缺失为开发阶段导入失败，不算产品行为RED。修复后100项定向通过（含8槽mock E2E），以已验证5823c009为增量基线的自动闭包94 passed，sentinel53 passed、flow smoke10 passed。最后两处复核类型/链条检查后32项相关回归通过；未宣称全量pytest。真实暂停现场新代码预览16项成功，manifest 47ed5cdc，10个重派工作区均clean，尚待部署后的正式受控恢复签收。

实现：4d2d81d7 fix: preserve native evidence across repeated plan recovery。
真实签收：受控复用evt-2e58ec1dd71c，原生冻结evt-62cd367e1e56 / fanout evt-37b17d55d247，16/16原生验收与集成证据保持；7个当前Reader实际派发且Luna输入绑定有效。未增加业务task.done，候选09094c2d保持。旧lane1占用和首次派发等待属于独立后续修复，不代表本项最终Candidate通过。证据：second-completion-reuse-{applied,freeze-proof}.json、current-candidate-input-bindings.json。
