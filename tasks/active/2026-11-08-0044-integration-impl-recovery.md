> 状态: active

# R60 集成失败后的同任务实施修复

来源：199 与 LS-v1 真实监工。用户已授权发现、修复、记录阻塞。

FMS23 的 Impl、Verify 均通过，但 rolling smoke 改写跟踪的证据文件，integration.queue.needs_review / workflow.operation.blocked 已发生。现有 task-integration 只重跑集成，不能合法重新派发 Impl 修正产品脚本。不能伪造失败 Verify 或删除洁净检查。

## 本批验收

- 增加 task-integration --restart-from-impl preview/apply -> verify: 当前 canonical integration block + admitted passing Verify/Impl、合同/TaskRef精确一致；暂停、终结、新代际、活动attempt、只读任务、摘要/目标漂移拒绝且不写入。
- 受控 supersede 后正常派发下一代 Impl -> verify: 同任务/合同/工作区，传递修复原因；原通过结果保留，新提交必须重新 Verify，不直接集成或触发 Plan。
- 幂等与模式冲突 -> verify: 重放不重复事件，不可切换已申请的恢复模式。
- 实际 CLI / projection / dispatch 调用者与确定性 mock E2E -> verify: focused closure、dev-verify、premerge gate。
- 合并 dev 后受控加载，现场恢复并记录 -> verify: 原 worker 保留、FMS23 新代际携带具体修复反馈，最终产品结果由 worker 与 Verify 产生。

## 边界与后续

本批补齐操作员获授权的确定性恢复入口，不宣称 RM/OA 自动诊断已完成。当前 registry 将 operation.blocked 泛归 workflow_operation_divergent；本次尚无 RM/OA 对该集成失败的处置。下一批以本失败事件作为触发，核验现有队列到语义分诊的接线与无人值守闭环，避免把业务脚本原因硬编码到 Kernel。

监工脚本曾漏选 blocked/needs_review，已补事件筛选，须同步持久当前阻塞视图和问题记录。

## 已验证（合并前）

- 聚焦 CLI / integration / Verify / dispatch：103 passed（增加 canonical source 核验后）。
- 最终 dev-verify run --base dev --workers 4 --tests tests/test_candidate_incremental.py：40 文件，562 passed / 7 skipped；sentinels 53 passed；flow mock E2E 10 passed。未跑全量 pytest 或真实 provider 测试层。
- LS-v1 只读 preview：当前通过目标35b4a636和evt-d0213691bbcd匹配，next_stage=impl，next_generation=2；无状态写入。
- 预览发现真实准入产生的完成事件origin为空；改为按已准入结果描述符核对原始完成事件，测试覆盖空origin，未降低摘要或身份校验。
- 现场部署及新代际派发回执待合并后记录；不将产品集成完成计入本次代码验证。
