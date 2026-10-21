# 洛书运行恢复检查点（2026-10-21 UTC）

来源：[设计 183](../design/183-runtime-result-authority-and-recovery-closure.md)。
授权：操作者要求“新建 worktree，基于 P0/P1 第一批、第二批生成 backlog 然后实施，待都实施完成后从断点恢复运行，验证 Q01–Q03”。

## 修复及现有证据

- harness 源码修复 `6ef7593`，归档 `6e1c221`；530 项聚焦通过，51 sentinel、10 serial、10 flow-smoke 通过。完整套件的 20 项基线失败已复现并记录在 P0/P1 任务。
- 旧 Run `loshu-impl8-20261020` 的 `evt-3f8927ad7587` 为历史 blocked；升级后仍保留。
- 原结果拒收的 RTK `evt-3c4c0c4505cb` 返回当前 authority，而旧 child payload 覆盖身份；新源码按 pinned operation 绑定并保留显式冲突，仍拒绝真正旧结果。
- legacy implementation adapter 现在保留 impl_self_check 和引用；故障模板不再预填 task_contract_unsatisfiable。不能将历史 29 次同类拒收当作 29 个产品设计缺陷。
- 新服务均从 recovery-closure-20261021 worktree 启动；当前配置仅将两条技能来源路径指向新 worktree。已通过 validate，并记录 run-contract-review；resume policy 为 WARN、非 strict，未绕过 STOP。

## 续接约束

1. 使用 escalation `evt-02cf83149de4` 的 `start_new_generation`，通过 ControlledActionService 追加新阶段代次；不编辑旧事件或冻结 Plan。
2. 保留完整 62 任务、274 mandatory AC 及完整源文档；从失败检查点核查已有提交和证据，禁止重新编造来源或任意删除需求来清除阻塞。
3. 本次是 harness 接收/恢复缺陷修复；原本地业务切片的提交应先重新核对合同和测试，不应因旧队列身份误拒收而重新设计产品。
4. `flow-discovery` worktree 有未提交的 local-material-categories 诊断证据，保留；所有 worker 分支与工作区保留。
5. 8 个 Impl 与 8 个 Verify 为各阶段容量，只有真实派发和正在工作的任务区间才能计入并发。

## Q01–Q03 外部事实

目前已证实本地 React/TypeScript/Vite 前端启动授权和 app 工程；正式后端复用、具名负责人、真实 API/联调环境、登录/会话/网关/撤权事实仍缺。
已向操作者请求正式仓库、API 契约或联调入口、身份服务和负责人，或明确新建后端与认证服务的验收边界。无回复前不把本地 demo/mock 计为 Q01–Q03 closed。
相关源文档：产品仓库 `docs/source/loshu-fms-handoff.md`、`docs/implementation-8x8-request.md`。应继续无外部依赖的真实工作，逐项保留阻塞原因。
