# 设计 182 实施与验收记录

> 状态: active。用户已批准独立 worktree 实施 B01–B07，并在新 Dashboard 重新规划洛书。
> 设计入口：[182](../design/182-plan-review-task-slicing-and-evidence-closure.md)。

## 已选择的边界

- D01：正文优先使用完整 Plan，Digest 单独标识；旧请求只从 exact task_map.ready 找回引用。
- D02：使用现有 verification_owner 分层校验，不引入新状态/schema。Impl 的未来 AC 保持 blocked；
  Task Verify 可省略未来行或标记当前阶段 not_applicable；canonical mandatory 不改变。
  Candidate Verify 要覆盖所有非 human AC，不能拿局部结果替代候选验证。
- D03：Planner/Critic 负责早期接线与阶段集成设计；全图共享路径仍只允许一个 writer owner。
- D04：TDD 绑定写入现有证据工件；环境或测试自身错误不算功能 RED。
- D05：本轮明确允许重拆受影响任务、依赖和 AC，保留来源与旧新映射。

## 实现入口

| Backlog | 实现或执行入口 | 验证 |
| --- | --- | --- |
| B01 | orchestrator_fanout → plan_preview_artifacts → operator_plan_preview → Plan API/React | tests/test_operator_plan_preview.py；真实页面测试待补 |
| B02 | verification_commands 的 AC 路由；Impl self-check、Verify result、模板及实际 writer handoff | tests/test_stage_acceptance.py；既有 typed admission / handoff 回归 |
| B03/B04 | zf-plan-execution-readiness；Planner/Critic 的实际依赖链 | tests/test_plan_readiness_skills.py；真实新 Plan 语义评审待完成 |
| B05 | yoke/tdd-evidence；开发及 Verify role context | 技能物化测试；本轮 RED/GREEN 输出；不认证未来洛书实现已 TDD |
| B06 | 新洛书项目、正式 flow intake/submit、Plan 人工审批门 | 重新规划与综合评审待执行 |
| B07 | 真实 Runtime 的确定性回归、自动验证层、浏览器验证 | 待最终结果 |

## 协议说明

Preview 新增 content_kind、content_error、digest_markdown、source_event_id、artifacts 和
artifacts_error，原字段兼容。已声明全文而不可读时显示 unavailable，不冒充摘要是全文。
正式请求固定 Plan 内容 digest；附件从同一 Plan 的 Package 提取，读取时验证 digest，
路径必须在当前 project/state 内，拒绝越界及符号链接逃逸。附件端点只接受已绑定的逻辑名，
返回纯文本并禁止 MIME 嗅探。GET 不发审批事件。

分层证据沿用唯一 contract。Impl 仍覆盖所有 mandatory ID，当前阶段必须 passed，未来
candidate/human 行必须 blocked。Verify 依据 immutable criterion owner 校验，不能通过结果
行改 owner 来掩盖本片失败。旧单一 owner 合同保持原完成要求，attempt/target/revision 绑定不变。
not_applicable 只描述当前 Verify 阶段的适用性，不豁免后续 mandatory AC。

## 证据与状态

功能 RED：B01 原代码 12 项回归失败（其中 API fixture 初始化问题另行修正，不计功能证明）；
B02 5 项失败，3 项原失败边界通过，包含真实 writer handoff 的拒绝。
GREEN：分层契约及相关 caller 回归 45 项通过；Preview 及附件 API 新回归 13 项通过；
角色方法/注入/相关回归 83 项通过、1 项环境条件 skip。自动扩大回归：11,498 passed、39 failed、19 skipped；其中新增技能索引缺项已修复，
其余 38 个失败节点在原 dev 工作目录逐项复现。主要为公开导出缺少 CI/文档/fixture、
既有 provider hook 哈希基线和测试进程的 python/pytest/ZF_CLI_CMD 环境。
不修改这些基线以掩盖失败。索引修复后 sentinel 51 passed；三流 mock smoke 10 passed；
影响闭包 340 passed、1 skipped。前端 build/typecheck 与自动 Web 单元层通过。
原始输出保存于本 worktree 的 reports/plan182/，浏览器结果及实际新 Plan 评审待完成。

本记录的通过范围限于 ZaoFu 修复与规划质量，不证明洛书产品已经实现、迁移或部署。
