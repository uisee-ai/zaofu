# 注册控制器的受控配置装配

对应 [Hello World V5 验收任务](../../tasks/active/2026-10-30-0604-hello-prd-playwright-smoke.md)。

`zf-prd-v5.yaml` 可以独立加载，但新项目原本只能由 Synthesis 调整已有 Flow 的 lanes / pool，
不能选择完整 V5，也不能引入它的 `bounded-direct-v1`。本次把完整控制器选择接入现有的
Request → Synthesis → Proposal → Apply config → Approve & Run，不另建启动路径。

## 注册与选择

- 生产目录和仓库根目录 `zf-*.yaml` 的 metadata 仍是目录来源；只有显式声明
  `metadata.zaofu.catalog.assembly: project-bound-v1` 的包进入 `registered_controllers`。
- 注册包必须有一个受支持 Flow、一个 ZfConfig，并能通过现有 loader。V5 声明 29 个角色。
- Agent 根据需求选择 `parameters.controller_id`、`controller_digest`，并提供
  `verification_commands`。目录、内容摘要与 Requirement 一起绑定到 Synthesis operation。
- 角色、skills、execution profiles 和完成策略来自被选中的注册包。未选控制器的请求仍受
  当前项目能力目录约束，目录中出现 `bounded-direct-v1` 不会使它对其他选择自动可用。
- 完整控制器选择保留注册的 lanes/pipeline；与局部 pool 修改互斥。运行中的配置不热替换。

## 项目适配和批准

Dashboard 的 Add/Open Project、Initialize Project 在未指定 kind/preset 且没有现成
`zf.yaml` 时，默认从注册目录加载 `zf-prd-v5.yaml`，通过共享 Project Init 入口生成项目
配置。HTTP 响应以 `config_generated=registered_controller` 和 `controller_id` 标识来源。
模板缺失时拒绝初始化，不回退到旧 multi-kind 草稿；已有配置和显式 kind/preset 仍按原路径处理。

初始化保留 v5 的 8 lanes、blocking Task Pipeline、on_demand、质量门和预算设置；项目名称、
说明、state_dir、session、Provider/mixed 选择、当前 Git 分支和检测/声明的技术栈检查绑定到
新项目。非 Codex 或 mixed 项目不继承 Codex 专用 model，等待后续明确模型修订。profiles 和
启用技能的依赖闭包物化到项目本地，支持移动项目或删除初始化所用 worktree。初始化仅创建
项目及其干净 Git 基线，不创建需求或启动 Workflow；需求及启动仍经过原有澄清和批准流程。
初始化成功后客户端清除项目列表读缓存，使新项目立即出现在选择器中，避免旧列表把选择退回
原项目。回归入口：`tests/test_project_controller_init.py`、`tests/test_project_admission.py`、
`web/tests/projectInitCache.test.ts`、`web/tests/project-wizard.spec.ts`。

候选替换完整控制器配置，保留现有项目名称、workspace、状态目录、session、本地 skill sources
以及 workdir provision paths。只把项目缺少的控制器技能及其依赖冻结到状态目录内的内容寻址包，
避免已安装技能重名；包是配置依赖的必需工件，不能当作可删除投影。Apply 会核验完整 manifest、
文件集合、内容摘要和权限；Preview 使用与启动相同的严格技能校验，不关闭碰撞检查。
PRD / target 路径来自不可变 Requirement，必须位于项目内；PRD 必须是现有文件。
Git candidate base 和 ship target 使用项目当前已提交的命名分支，避免假定新项目有 dev。

Agent 提供项目适用的验证命令，确定性代码把它们编入 enabled static gate。命令逐条出现在
完整 YAML 和 Diff 中，随候选摘要一起批准。这里适配的是业务项目的工具链；不会改写 WRC、
OA blocking、独立 Verify、生命周期、语义证据门禁、预算或自动 ship 策略。

Profile 文档内联进候选；摘要覆盖控制器、Profile 和实际展开的环境值。未知 ID、旧摘要、
跨项目路径、缺少验证命令和候选替换均拒绝。生成候选不会修改项目 `zf.yaml`；只有现有
token-gated `workflow-config-apply` 在精确批准、原配置摘要与验证结果一致时写入。Apply config
本身不启动 Run，仍需独立 Approve & Run 和消费者配置校验。

若项目已在 Git 跟踪 `zf.yaml`，Apply config 同时创建只包含该文件的本地 checkpoint commit，
将 commit 绑定到 Apply receipt；不会推送。要求 index 干净、没有其他 tracked 改动、Git 中的
配置仍与批准基线一致。保留正常 Git hooks，禁止混入别人的暂存内容。写入后 checkpoint 中断时，
只有同一幂等键可继续该次 Apply；另一批准键仍受原有 CAS 约束。Web Start 重试会先重放精确
Apply，因此旧版已写入但未 checkpoint 的 receipt 也能补全；原 receipt 保留并由新 receipt 引用。
交付启动的干净基线检查保持生效，不把任意脏 `zf.yaml` 加入忽略名单。

## Raw 审核修订与恢复

注册包的首次选择仍要求候选与装配结果精确一致。后续 Raw 修订通过不可变
`workflow-preview-review.v1` 工件绑定原提案、Request revision、配置摘要和字段补丁；
提案编译器在 `compiler_inputs.preview_review_ref` 保留该来源，再从原候选重建补丁结果，
逐字验证新候选。Controller ID、包摘要与项目验证命令必须保持原合成绑定；不能用任意
candidate 或删除 controller 身份绕过检查。字段白名单、类型、preflight 与能力闭包继续生效。

model / lanes 等审核修改属于配置修订，不改写注册包的首次选择参数。连续修订从上一次
不可变候选开始，保留此前修改。Apply 中断后，同一 review 仅继续它已产生的下一 revision；
不会再递增 Request，也不会自动应用配置或启动 Run。新的候选仍需精确配置批准和启动批准。

Preview 同时暴露实际编译角色的 `config.<document>.roles.<name>.model` 与
`model_reasoning_effort`。一个审核补丁可分别设置 Planner、Impl、Verify/Judge。
对于 Flow 生成的角色，编译器会跳过同名手写角色，因此受控补丁物化该角色原有的技能、
权限、事件及生命周期后，仅改变指定模型字段；不允许通过此入口添加角色或改拓扑。
`tests/test_workflow_review_roles.py` 对比整个编译角色合同与工作流，防止模型调整丢失行为。

## 验证

### Git 基线与准备恢复

声明 `metadata.zaofu.catalog.assembly: project-bound-v1` 的项目在调用选路模型前
检查 Git 仓库、命名分支与有效 HEAD；注册控制器装配再次检查。无 Git 的只读路线
不因共享入口而被强加提交要求。Git 确定性缺项会终止当前准备尝试，并给出修复动作，
不会重复消费模型或盲重试三次。

非空项目保留原文件。`zf project git-baseline --root ROOT --paths-file FILES.json
--exclude dataset` 只预览显式源文件清单，输出文件内容摘要、分支、忽略项与清单摘要。
文件选择由项目指令和 agent 判断；受控 `project-git-baseline` action 或
`zf project git-baseline --root ROOT --apply --manifest MANIFEST.json` 执行批准的清单。
它自动排除配置中的 state_dir，拒绝陈旧摘要、越界、符号链接、凭据和已有暂存内容，
使用独立 index 且只提交批准路径。同一基线重复执行不产生第二次提交，不启动 Run。

`zf workflow prepare --request ID --retry-failed` 为失败准备创建新的 operation；新尝试
引用旧尝试，只有相同 Request revision / Requirement digest 的 failed/blocked 绑定可被
替换。旧失败保留在事件账本，已经准入的语义结果在当前 catalog 和能力检查后重新绑定；
不伪造需求修订。重复点击复用当前尝试。配置审批与启动审批不因恢复而省略。

Web 列表、详情和 CLI 共用 preparation 投影：`preparation_blocked`、
`preparation_failed`、`synthesis_queued`、`synthesis_running` 与真正 `proposed`
分开显示；失败原因进入 blockers，排队反馈不宣称 YAML 已准备完成。

- `tests/test_project_git_baseline.py`：真实 CLI/受控 action、精确提交与拒绝路径。
- `tests/test_workflow_prepare_recovery.py`：非空无 Git 项目、准入后装配失败、基线恢复、
  同 Request 新尝试和一次模型调用的真实提案装配回归。

- `tests/test_workflow_review_controller.py`：注册控制器 model / lanes 修订、连续修改、旧补丁与篡改拒绝、中断重试及配置批准边界。
- `tests/test_workflow_registered_controller.py`：真实合成/提案/配置 Apply 代码路径，能力隔离、
  Profile 摘要变化、候选篡改、路径隔离、项目适配，以及受控入口的 mock E2E。
- 现有 controller candidate、synthesis、proposal、config apply/consumer、Kanban preparation、
  Web proposal、profile catalog 和 intake handoff 回归继续覆盖直接调用方。
- 真实 Provider / Playwright 的首次阻塞记录保留在
  [原验收报告](../records/2026-10-30-hello-prd-v5-playwright-smoke.md)，续跑另存证据，不能用 mock
  通过替代真实 Scan / Plan / Impl / Verify / Judge 交付结论。


## Task Pipeline 的图归属

V5 Task Pipeline 的 operation 使用 `impl` / `verify` 等阶段名，编译 DAG 容器可能是
`prd-lanes-impl`。图通过 Kernel `task.pipeline.generation.admitted` 中的 Run、generation、
`stage_id` 精确关联，新增只读 `compiled_stage_id` 与来源事件，保留 operation 原始
`parent_stage_id`。不按角色名猜测或改写事件/调度状态。

Task Verify 属于该 generation 的任务流水线；后续 Candidate Verify 仍是独立阶段。
单个 Impl 调用结束不代表流水线结束；对应 generation 的 Kernel `candidate.ready` 才闭合
这个容器。后端完整图与轻量 execution summary、前端阶段卡和 Run 归属共用这份绑定。
验证包含跨 Run / generation / worker 声明拒绝、冻结边界，以及真实浏览器阻塞状态断言。

续测结果见 [V5 受控装配真实验收记录](../records/2026-10-30-hello-prd-v5-controlled-assembly.md)。
