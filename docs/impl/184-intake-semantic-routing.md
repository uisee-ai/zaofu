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

受控 workflow-request 的 kind=auto 默认进入已有持久化 Synthesis Operation。
已初始化项目确认 `idea-to-product` 时也立即创建同一 Workflow Request 并开始准备，
使 Workflows 列表可见；不再仅记录一层无人执行的嵌套提案。确认表示准备需求、
问答与 YAML Preview，配置应用和运行仍需各自批准。显式 `proposal_only=true`
以及未初始化项目保留原提案行为；不凭产品想法伪造完整 Task 合同。
准备消费者的单轮限额只计算实际处理的操作；已完成预览或已返回问答的操作
不占用名额，避免同一项目的后续请求永远排队。
用户要求基于材料创建交付 Workflow 时，Kanban Agent 应保留来源、目标、验收和所选
profile/容量，提出准备 Workflow 的 Task；不能把仅写入事件的 `workflow-config-propose`
描述为已生成 YAML。PRD v4 Synthesis 可携带有界 `task_pipeline` 参数（profile、mode、
impl/verify 容量和最大活跃任务数），编译器生成 fanout 配置候选并执行原有预检；
反压队列默认串行，其他质量门保持原项目配置。已有 Task Pipeline 须在 Preview 中
显式修订。Preview DSL 可一次审查 profile、池配置、PRD 来源与目标路径；编译和预检
失败仍阻断，保存新 Preview 不应用项目配置或启动 Run。配置提案登记和验证请求的
回执分别标明“未生成 YAML”和“验证结果待返回”。
Kanban 问答暂停或回合结束后，前端依据 durable runtime 的 attempt、待回答交互和
更新时间补拉对话历史；SSE 结束消息漏收或历史读取暂时失败时由后续轮询恢复。
历史缺少结束事件时，同线程的 durable 状态收敛旧 Thinking/Preparing 展示；取消回合的
未回答问题变为取消回执，不能继续提交。较新的恢复流事件优先于旧轮询快照。
`waiting_input` 显示等待回答，可取消待回答回合，不显示仍在生成。发送在权限读取前
同步防重入，问答提交同样防止重复点击；请求失败后允许重新提交。
编译被阻断时，详情页仍按当前 Request revision/digest 展示不可变候选中的 YAML
和阻断原因；它不会成为可批准的 Request 绑定，旧 revision 的候选也不会显示。
backend 优先采用显式 synthesis_backend、原操作 backend、请求 backend、项目 Run Manager
backend，最后为 codex。Agent 根据完整需求和目录选路，内核校验版本、目录和准入。
单独的 flow intake/classify 仍只收集需求，不隐式调用模型。
已确认 Requirement 的 `ready` 表示需求可供 Synthesis 读取，并不表示路线已选定。

相同输入重试不修订 Requirement，同版本已完成结果可复用；真实语义字段变化才修订。
取消、需求修订、过期返回、Provider 失败沿用既有生命周期检查，不回退到关键词选路。
Proposal 仍须通过受控 submit 才启动；重复 submit 复用已启动实例。

### Task-bound Workflow 的路径合同

Delivery 点火预检按字段用途校验 Task 路径：`scope`、`shared_files`、
`exclusive_files` 以及普通 worker 的 `handoff_artifacts` 保持项目内相对路径，
不能指向 `.git` 或 `project.state_dir`。`exclusions` 表示禁止修改的位置，
允许列出 `.git/**` 和配置的运行态目录，但仍拒绝绝对路径、`..` 和越界符号链接。

由 `execution_binding.owner=workflow`（兼容旧 `evidence_contract.execution_owner`）
标记的父任务，其 `handoff_artifacts` 会绑定为 Workflow 输入 `artifact_refs`，
可引用已有文件或目录的绝对路径，例如外部交接 Markdown、素材目录和数据库样例目录。
这些引用不授予写入权限；绝对路径必须存在，不能指向项目内部状态，不能包含 `..`。
预览保留原始引用、排除项和 Task 摘要，不重写 Task，也不追加启动事件。
回归覆盖见 `tests/test_contract_validation.py` 与 `tests/test_workflow_start_service.py`。

### 批准后自动启动项目运行器

`workflow-start` 的 CLI、Kanban Agent 和 Channel Group 批准入口，以及
`zf flow submit --apply`，在请求受控提交后调用同一个运行器启动器
`src/zf/runtime/workflow_runtime_start.py`。预览、提案和被拒绝的请求不会启动运行器。
启动器按 `zf.yaml` 的 `project.state_dir` 锁定启动记录，复用已有 watcher，或在
项目根目录后台执行当前安装的 `zf start`。子进程固定目标 Project 上下文，移除
Web/Workflow 授权凭证，仍执行原有 preflight、tmux/transport、sidecar 和 catch-up。

`zf start` 完成启动后发布与 watcher PID/epoch 绑定的 readiness 投影；响应中的
`runtime_start.status=running` 表示运行器已就绪，不表示 Workflow 或代码交付已完成。
并发启动和重试复用同一进程；`runtime.start.requested`、`runtime.start.failed` 记录
启动副作用。日志位于 `<state_dir>/logs/workflow-runtime-start.log`。
启动失败或等待超过 30 秒返回明确失败/仍在启动信息，保留已提交 Request 与原提案；
修复环境后重试原提案，不追加第二个 invoke。首次提交生成的 Request 标识不会改写
原提案的输入摘要；Task、参数、配置和来源 currentness 检查继续生效。

### Dashboard 默认写权限

`zf web` 在未配置权限策略时默认启用写操作：loopback 使用可信本机会话，
非 loopback（含 `0.0.0.0`）复用或创建 Workspace 的 `web-action-token`（权限 0600）。
已有 Token/Passcode/可信会话策略保持不变；`--read-only` 显式禁用写操作。
`tools/start-webkanban.sh` 默认可写，并输出 `write_url`；浏览器从 URL fragment
读取授权到既有 Token 存储，立即清除 fragment，再发送 API 请求。Token 不进入 HTTP
请求路径、查询参数或 Referer。局域网匿名请求仍被拒绝；所有写操作仍经过受控 action。
Kanban Agent 默认文件权限统一为 `workspace_writer`（Write），Workflow 独立批准仍然必需。
显式选择 `dangerous_full` 继续要求原有确认字段，不通过省略权限字段隐式扩大写入范围。
未传权限字段的 API、Agent 直接调用、交互续跑、启动恢复和队列派发统一采用同一写权限
默认值；明确传入 `read_only` 继续生效。`ZF_WEB_TRUSTED_SESSION=0` 只禁用免鉴权的
可信会话，自动配置 Token 写操作；只读 Dashboard 需明确传入 `--read-only`。

回归见 `tests/test_workflow_runtime_start.py`、`tests/test_cli_web_access.py`、
`web/tests/dashboardLaunch.test.ts`。真实 watcher/tmux + mock Provider 的三个入口验证见
`tests/e2e/test_workflow_runtime_autostart.py`（显式 `host` 层），不等同真实模型交付验证。

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


## 统一准备与完整 YAML Preview（2026-10-25）

关联任务：2026-10-25-1258-workflow-prepare-and-yaml-preview.md（实施后归档至 tasks/done）。

Kanban/Channel 的交付建单提案使用 prepare_workflow=true，由批准后的 create-task 创建
Task，再调用同一个受控 workflow-request。完整合同、验收、来源和 Channel 授权从
canonical Task 继承；仅跟踪省略标记，旧显式 route Plan 保留兼容。
已有 Task 通过 `zf workflow prepare --task TASK-ID` 或 workflow-request(kind=auto) 准备。

Web AskUserQuestion 展示 Request 当前问题，答案持久化为新的 Requirement revision，
按原操作上下文/Provider 重新合成。CLI 用 --request、--revision 和可重复的
`--answer '原问题=答案'` 回答；--enqueue-only 仅入队。取消、失败、旧版本不回退
关键词选路。所选 Flow 的必需 root 缺失也回到澄清，不产生可执行提案。

Web 生命周期托管最多两个项目准备消费者，复用原 process lease/Operation；没有 zf start
也能完成准备。它不派发 Task、不运行完整 runtime tick、不启动 Run。CLI 默认同步消费
本 Request 当前 revision，不抢其他请求，运行器仍能消费相同持久化操作。

Proposal 的 effective_config_ref 增加完整、多文档、脱敏 yaml 字段，来自不可变配置候选。
原始 SHA 仍绑定私有候选，对外 YAML 用于审核，不作为批准写入的输入。Workflows 展示
Assembled zf.yaml、Config Diff、Execution Plan；CLI
`zf workflow preview --request REQUEST-ID --format yaml` 使用同一快照。旧 Proposal
没有该字段时不伪造全文，须准备新版才能看到全文。

配置变更仍先受控 Apply config，再精确 Approve & Run。提交重查 Task 合同与 Channel
Owner/PRD 绑定，不允许省略或替换 task_id 绕过来源。运行后 Plan 人审开关保持原语义。

回归：tests/test_workflow_prepare.py、tests/test_workflow_preparation_worker.py；浏览器：
web/tests/workflow-preparation.spec.ts。测试只模拟 Provider 语义结果和启动边界，不代表
真实模型质量或产品最终验收。
