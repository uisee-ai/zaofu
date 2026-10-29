# OA 有界执行与调度观测 P0–P1 实施记录

> 日期：2026-10-29
> 状态：已合入 `dev`
> 实现提交：`afeeb883 feat: add bounded OA execution dispatch`
> Backlog 归档提交：`2362edb3 docs: archive bounded OA dispatch backlog`
> 合并提交：`e3ade983 feat: land bounded OA execution dispatch`

## 实施范围

本批工作复用现有 Task contract、Plan Artifact Package、Task Pipeline、Operation、Attempt、EventLog、Run Manager 和 Kernel admission，没有增加第二套 Task schema 或状态机。

### P0-01：统一执行摘要

- 分离 Task 生命周期 `task_status` 与实际 `execution_status`；
- 展示 operation、worker、attempt、generation、verdict、阻塞原因和 ready queue；
- 展示 Run、Task Map、capacity 和 backpressure 绑定；
- 新增只读 CLI：`zf trace dispatch`；
- 新增 Task Pipeline summary API 和 Delivery Trace 执行摘要；
- 查询不修改 canonical state。

### P0-02：有界自主执行

- flow policy 支持显式 `autonomous_actions: [delegate, continue, rework]`；
- `pre_impl` 支持已接纳 Task 的合同内委派；
- `semantic_failure` 支持当前 Impl 的精确返工；
- 合同、依赖、generation、operation、attempt 或 dispatch 变化时拒绝旧决策；
- `dispatch_rank` 只调整已 ready 任务，不绕过依赖、能力、容量、Verify 或返工上限；
- execution context 以不可变 sidecar 保存，并按需缩小必读输入。

### P1-01：增量语义审查

Run Manager 只在显式启用、Run 活跃、generation 当前、Impl 已 settled 且未通过，并且存在有效结果证据时，生成现有类型的 OA triage 请求。普通等待、heartbeat、活跃后继、其他 Run、旧 generation、环境/Provider 故障和重复请求不会触发。

### P1-02：技能与文档

- 更新 canonical `skills/`；
- 同步 `.codex/skills/` 与 `.claude/skills/`；
- 增加 OA 有界执行与局部 Replan 参考；
- 增加 Planner/Critic 执行路线说明；
- 增加实现文档和 CLI reference。

### P1-03：验证与对照

- 增加 OA 权限、currentness、合同变化、后继 operation、Run Manager 去重和 CLI/API 一致性测试；
- 增加隔离 Runtime A/B mock E2E；
- 在相同 Task Map 下，baseline 与 bounded execution 均保持 2 个并发 worker、依赖阻塞和重启去重；
- 必读上下文从 `28,684` 字节降至 `19,308` 字节，减少约 `32.5%`；
- 两组 Critic 请求均为 `0`，重复派发均为 `0`。

## 验证证据

- P0/P1 定向测试：`35 passed`；
- 配置与 Task Pipeline 影响闭包：`659 passed, 7 skipped`；
- pre-merge sentinel：`50 passed, 1 skipped`；
- flow smoke：通过；
- Web typecheck/unit：通过；
- 完整 deterministic tier：`13,228 passed, 21 skipped`，另有 5 个基线文件缺失导致的 `FileNotFoundError`，缺失文件为 `tools/export-public.sh` 和历史 prompt，不属于本批变更行为回归。

## 运行边界

本批代码已合入 `dev`，但没有修改活跃 FMS 的配置、账本、进程或 Provider rollout。A/B 使用确定性 mock Runtime，证明调度、上下文和幂等行为；真实 Provider 语义质量、FMS 产品完成率和线上收益仍需独立 canary 验证。

## 194 合并后的 P0/P1 补齐

- 194/195 合并提交：`de7387c3 feat: integrate staged workflow and OA frontier contracts`。
- P1 增量语义审查改为复用 `oa_profile` 的统一 resolver；显式 profile 按 role name 或 instance id 解析，缺失时 fail-closed，不再硬编码 `orchestrator`。
- 新增最新控制器样例：`examples/prod/controller/prd-task-pipeline-v4-oa-bounded.yaml`。该样例同时声明 V4 blocking Task Pipeline、195 `oa_profile` 与 `normal_frontier_owner`，以及 P0/P1 的 `pre_impl` / `semantic_failure` 有界动作。
- `normal_frontier_owner: run_manager` 仍是兼容默认值；WRC consumer 尚未完成前，不能把 `wrc` 配置宣称为正常路径已迁移。
- 配置验证：`zf validate --path examples/prod/controller/prd-task-pipeline-v4-oa-bounded.yaml --cold-start` 的 `config_valid` 通过；cold-start 的仓库目录/状态目录失败属于示例文件不在项目根运行的预期环境条件。
