# 184：Intake 语义路由与交接验收

本记录对应设计 184 的入口语义边界和本批四项任务。实现复用既有 Kanban 主 Agent、
Workflow Synthesis Operation、Proposal 编译器及 Task Map 合同，没有增加第二套状态机。

后续真实 Provider 验证与失败改进见 [184 真实验证记录](184-live-validation.md)。

## 行为与责任

新请求的 `kind=auto` 表示待选路。Intake 只收集需求，默认 lane 为 0，
不生成交付矩阵；关键词结果仅存在于 `inferred_kind` / `candidate_kind` 提示中。
`zf flow classify` 保持 auto 并返回 `clarify`，不能据关键词生成可执行路线。
提交预览返回 `workflow_route_selection_required` 阻断，不产生可用 Proposal。

用户或 Agent 可以显式选择已注册 kind/route，继续确定性路径；CLI 显式 classify
同时通过受控需求修订函数更新 canonical Requirement，避免只改展示 manifest。
已提交或运行中的请求禁止通过 classify 改 kind，仍须走受控恢复/重规划。
选择后的必需 source/target 输入仍会检查，不能借 auto 的中性字段要求绕过校验。
历史请求保留原 kind，不因重放 auto 隐式迁移。

需要模型建议时，调用方显式传入 `synthesis_backend`，使用已有持久化 Synthesis
Operation。Agent 根据完整需求和目录选路；内核验证版本、摘要、目录、schema 和准入。
没有 backend 时，系统返回待澄清，不额外发起模型调用。
已确认 Requirement 的 `ready` 表示需求可供 Synthesis 读取，并不表示路线已选定。

相同输入重试不修订 Requirement，同版本已完成结果可复用；真实语义字段变化才修订。
取消、需求修订、过期返回、Provider 失败沿用既有生命周期检查，不回退到关键词选路。
Proposal 仍须通过受控 submit 才启动；重复 submit 复用已启动实例。

## 验证材料

- `tests/test_workflow_auto_routing.py`：否定/解释/新功能输入、无默认 lane、旧请求重放、
  CLI 显式选择、同请求操作复用，以及选中路线后的必填字段。
- `tests/e2e/test_intake_semantic_handoff_mock_e2e.py`：真实受控请求、持久化操作消费、
  Proposal 准入、重复提交、Kernel IssueFlow light 入口直至带 Requirement 摘要的
  `task_map.ready`。仅 Provider 输出模拟；不代表模型正确理解否定句。
  同入口覆盖取消、修订和 Provider 失败后的晚到结果。
- `tests/fixtures/plan-counterexamples/cases.json`：9 个正反例，包含循环依赖、未知依赖、
  共享写入、未来证据、非法路径、漏 claim、语义缩减和假依赖。
  机械错误由现有验证器拒绝；漏 claim 保留现有诊断，语义结论由 Planner/Critic 判断。
  `expected_critic_verdict` 是人工标注的期望，不是实际 Critic 运行结果。

## 主 Agent 语义评测

`scripts/eval-intake-routing.py` 使用 12 个固定样例，包括否定、引用、混合需求、
普通问答、仅建单、明确交付、多语言与多轮纠正。评分比对人工标注的允许动作和路线，
复用实际解析器与路由目录，检查非法 envelope、越权建单、错路由和空输出。

真实 Provider 为显式运行层，例如：

```bash
PYTHONPATH=src python scripts/eval-intake-routing.py \
  --project-root /path/to/project --backend codex-headless \
  --output /tmp/zf-intake-eval-<UTC>/result.json
```

实际调用 KanbanHeadlessAgent，使用临时隔离会话状态与 `read_only` 权限；每例会话独立，
同一多轮样例复用其会话。它不 apply 提案、不启动工作流；目标项目需提供有效 `zf.yaml`。
报告记录目录、样例摘要、原始回复、失败、Provider 会话与 usage；模型未上报时写
`unreported`，不猜测。多轮记录保留每轮回复，顶层 usage 为最后一轮返回的 usage。
临时会话目录退出后清理，报告保留。

离线可使用 `--responses <JSON文件>` 替换 `--backend`，格式为 `{case_id: reply}`。
报告明确标为 `recorded_responses`；这种运行只验证评分器和记录内容。
目录缺少样例必需的路由时，真实评测标为 `catalog_ineligible`，不算通过。

本批仅运行离线确定性测试和 mock E2E，真实 Provider 语义质量尚未测量。
即使路由分数通过，也不能证明回答内容、任务拆分或最终产品验收通过；仍需独立评审。
本批未宣称设计 184 的所有生命周期扩展均已实现。

## 本批验证记录（2026-10-23 UTC）

基线 `d81797f5` 的关键词函数复现：`不要重构，只修复登录失败 -> refactor`、
`不需要调研，修复登录问题 -> workflow`、`新增订单导出功能 -> issue`。
新测试证明这些关键词只能作为提示，不能决定 auto 的执行类型。

执行目录 `/home/min/workspace/public/184`，以下 pytest 使用
`PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest -q --no-cov`。
这是复用依赖环境，导入代码仍来自 184。

| 验证范围（追加到上述命令） | 结果 |
| --- | --- |
| `tests/test_workflow_auto_routing.py tests/test_workflow_synthesis.py tests/test_workflow_requests.py tests/test_control_actions_workflow_request.py tests/test_workflow_start_service.py tests/test_cli_flow.py tests/test_intake_routing_eval.py tests/e2e/test_intake_semantic_handoff_mock_e2e.py` | 132 passed；后续 CLI 活跃请求与多行重试补测见下 |
| `tests/test_task_workflow_plans.py tests/test_kanban_agent_semantic_proposal.py tests/test_kanban_plan_requests.py tests/test_workflow_proposal.py tests/test_workflow_operation.py tests/test_workflow_operation_resume.py tests/test_workflow_request_acceptance.py tests/test_task_map.py tests/test_task_map_evidence.py tests/test_task_map_materialization.py tests/test_light_flow.py tests/test_channel_workflow_bridge_runtime.py` | 199 passed |
| `tests/test_intake_routing_eval.py tests/test_plan_counterexamples.py tests/test_plan_readiness_skills.py tests/test_skill_provenance.py` | 39 passed、1 skipped（可选外部 skill 示例未安装） |
| `tests/test_cli_flow.py tests/test_workflow_auto_routing.py tests/e2e/test_intake_semantic_handoff_mock_e2e.py` | 61 passed，含已提交请求禁止重新分类 |
| `tests/test_workflow_kind.py tests/test_workflow_inputs.py tests/test_web_workflow_proposals.py tests/test_cli_project_init_source.py tests/test_workflow_auto_routing.py tests/e2e/test_intake_semantic_handoff_mock_e2e.py` | 48 passed；1 个旧导入文本匹配测试误报，修正为 AST 导入检查后单独重跑该模块 17 passed |

多行 objective 的重复请求曾复现版本 `2 -> 3`，现按 Intake 已有的空白规范比较；
上述调用方补测同时验证修复与取消后重试不重启。导入边界测试新增 4 个正反例，
仍禁止 runtime 导入编译器模块，允许共享 request policy 的同名词函数。
文件大小哨兵首轮发现三个模块略超限制，新增复用判断移至路由辅助模块后通过；
没有增加豁免。Ruff、`git diff --check`、9 份 skill 元数据及三方内容一致性均通过。

已执行 `python scripts/dev-verify.py plan --base dev`。首轮计划将 500 个影响文件（最终暂存复核为 501 个）按数量
升级为全量；遵循 AGENTS.md 的领域闭包规则，确定性全量与 serial 层由上述显式闭包替代，
未执行完整 pytest，也未测试真实 Provider/host。自动层命令为：

```bash
PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python scripts/dev-verify.py run \
  --base dev --skip-tier deterministic --skip-tier serial --keep-going \
  --receipt /tmp/zf-184-routing-verify-20261023/automatic.json
```

回执 `passed`：Web typecheck、Web unit、premerge sentinels（51 passed）、
flow smoke（10 passed）均通过。相对 dev 的范围包含此前 184 的 Web 后端修复，
所以保留 Web 边界检查。临时复用的 `web/node_modules` 链接已移除。
测试组存在重叠，不累加为独立用例总数。
