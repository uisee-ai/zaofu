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

## 验证

- `tests/test_workflow_registered_controller.py`：真实合成/提案/配置 Apply 代码路径，能力隔离、
  Profile 摘要变化、候选篡改、路径隔离、项目适配，以及受控入口的 mock E2E。
- 现有 controller candidate、synthesis、proposal、config apply/consumer、Kanban preparation、
  Web proposal、profile catalog 和 intake handoff 回归继续覆盖直接调用方。
- 真实 Provider / Playwright 的首次阻塞记录保留在
  [原验收报告](../records/2026-10-30-hello-prd-v5-playwright-smoke.md)，续跑另存证据，不能用 mock
  通过替代真实 Scan / Plan / Impl / Verify / Judge 交付结论。
