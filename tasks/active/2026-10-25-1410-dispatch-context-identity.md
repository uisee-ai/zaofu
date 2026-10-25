# 派发上下文身份一致性与 checkpoint 恢复

> 状态: active

用户批准修复、commit、合并 dev、重启并从当前 checkpoint 继续。现场 Environment g2 的 task.md/manifest.active_dispatch_id 仍为 g1，而 briefing/manifest.dispatch_id 已是 g2；evt-8de3bd861112 提交 task_context_stale。宿主锁修复保留。本任务不宣称 38 个任务或真实 8 lane 并行验收完成。

## 实施验收

- [x] 复现首次派发/重派的身份冲突 -> verify: 修改前 4 failed / 6 passed，失败位于实际 transport 回调读取文档的断言。
- [x] 文档绑定当前派发与 worker，发送前校验正文/manifest -> verify: Impl/Verify、首次派发/g2、成功/失败分支；正文/header/manifest/角色被替换后拒绝发送。
- [x] 保持 canonical 更新时序 -> verify: 发送时 TaskStore 尚未伪称本次已发送；transport 失败时 canonical 状态不变、不发 task.dispatched；成功才更新。
- [x] 聚焦直接调用方和共享合同、相关 mock E2E、sentinel -> verify: 记录准确测试层级与结果。
- [ ] 提交并合并、控制面重载、受控 checkpoint 恢复 -> verify: 当前有效 g2 阻塞 operation 通过 recover task-impl 进入下一代；现场文档/briefing/TaskStore/operation/attempt 一致、worker 继续原任务。

## 边界

派发文档是本次 delivery 的投影，不能通过提前修改 TaskStore 来伪造发送成功。稳定的任务/合同版本不混入每次派发 id；身份作为派发前单独校验维度。常规非派发文档审计仍可只检查合同版本。

证据目录：/home/min/workspace/public/zaofu-evidence/dispatch-context-20261025/。

## 回归进展

- 最终聚焦闭包 145 passed（final-closure.log），覆盖任务文档、派发/briefing、operator retry、rework、identity、result、transport binding、call-result runtime、并行背压及相关三 flow mock。
- 新增首次派发/重派 × Impl/Verify × 成功/失败；失败后重开 TaskStore 再次派发；运行中/终态 no-op 不重写文档；准备后文档损坏在 transport 前拒绝。
- dev-verify 选择 72 个影响文件（非全量 pytest），最终自动层 1227 passed / 8 skipped，426.97s；较早跳过自动层的 receipt 不作为通过依据。

源分支 sentinel 51 passed；Ruff、diff check 通过。现场恢复前预览已确认 current g2 -> next g3，尚未 apply。
