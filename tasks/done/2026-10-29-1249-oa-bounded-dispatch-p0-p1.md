> 状态: done；实现提交 `afeeb883 feat: add bounded OA execution dispatch`。

验证：定向 OA/Task Pipeline 31 项、trace/API 51 项、配置/Task Pipeline 659 项通过；
premerge sentinel、flow smoke、Web typecheck/unit 通过；完整 deterministic tier 为
13,228 passed、21 skipped、5 个基线文件缺失导致的 `FileNotFoundError`，详见本次交接报告。

# OA 有界自主执行与调度观测 P0–P1

Owner 已在 2026-10-29 批准“p0-p1 生成 backlog 在新的 worktree 实现”。
基线：`98546fa4 fix: keep implementation checkpoints nonterminal`，依赖尚未合入 dev 的
FMS pipeline repair 提交；不带入其他 worktree 的未提交修改。

## 目标与边界

复用唯一 Task contract、Task Pipeline、OA typed operation、Run Manager 与受控接纳：
合同内委派/精确返工无需 Planner/Critic；合同变化继续局部 Replan、Critic 和既有审批。
只在隔离测试中启用新策略，不修改活跃 FMS 配置、账本、进程或 Plan。

## 已批准工作项

- [x] P0-01 统一执行摘要：保留 Task 生命周期，单独展示实际执行状态、最新 operation/结果、
  阻塞、就绪队列、容量、Run 与 Plan 绑定。CLI/API/OA 复用同一只读投影。
  step -> verify: in_progress + settled failed 不再被摘要算作活跃执行；旧失败不覆盖新成功；
  Task Map ref 正确返回；查询不写 canonical state。
- [x] P0-02 有界自主决策：在现有 flow policy 中显式限定自主动作；绑定当前合同和执行目标；
  委派与精确 Impl 返工复用现有 Kernel 接线，拒绝越界、过期和重复决策。
  step -> verify: 允许动作无需新增 Critic；未授权动作、合同变化、活跃后继、旧 attempt 被拒绝；
  依赖、能力、原返工上限及独立 Verify 保持。
- [x] P1-01 增量语义审查：通过 Run Manager 为当前已结束且未通过的 Impl 生成精确审查请求，
  显式 opt-in、去重、不把依赖等待或正常运行当失败，不重开已完成任务。
  step -> verify: Runtime/RM 实际入口触发一次 typed OA 请求；重放、心跳不重复触发；
  有后续进展、其他 Run、关闭策略和普通等待均不触发。
- [x] P1-02 技能与文档：区分 Task Map 候选审查、pre_impl 分配、运行中返工及局部 Replan；
  必要依赖与执行偏好分开，按需读取异常任务，保留验收责任；同步三份技能。
  step -> verify: 技能验证、分发一致性、配置文档与真实字段一致；实例能区分实现缺陷、
  缺确认、错误阶段门槛，不能自签验收或把同任务分配冒充改图授权。
- [x] P1-03 验证与对照：隔离 DAG/worker/失败实例，对照禁用与启用策略，记录实际
  派发、并发、返工、Critic 事件、重放去重和验证交接证据。
  step -> verify: focused impact closure、dev sentinel 和相关 deterministic mock E2E 通过；
  如实区分确定性 A/B 与真实 Provider 语义质量，不声称本轮已在 FMS 上线。

## 验证与收尾

使用 `scripts/dev-verify.py plan/run --base dev` 解释继承修复与本批改动的覆盖闭包，
必要时补充精确测试路径。提交本批实现和 active 记录，再以实现 commit 标记 done 并归档。
不 push、不 merge dev、不删除 worktree。
