# OA 组队与恢复角色的配对验证

规格 215 的默认角色策略需要两个新的、单独批准的真实 8×8 运行窗口。A 组启用 OA 和常驻 Run Manager Agent。B 组使用同一 OA，关闭常驻 RM Agent，并显式启用已授权的按需 reflect，或具有 self_repair_consumer 的 Autoresearch。报告分列配置消费者和实际领取/反思回执。两组保持 WRC、RM runtime 和 Supervisor 均启用。记录相同 Reader 故障的修复、一次超时、消费者不可用及正常完成四类结果，每种场景各自建立 A/B 对照。[规格和放行条件](../design/specs/215-oa-stage-teams-and-execution-workbench.md) · [既有 8×8 历史证据](eight-by-eight-unattended-qualification.md)

## 本轮证据状态

| 对照 | 当前有的证据 | 尚待取得的结果 |
|---|---|---|
| 小需求、跨域需求的固定双扫描与按需 Plan | 确定性测试经编译、Proposal、角色派发及汇总入口验证零、一、多专家的差异和正式作者身份。 | 新批准需求上的实际首个有效结果耗时、遗漏条款、补读次数和人工介入。 |
| 常驻 RM 与按需诊断恢复 | 测试经真实调度与事件准入路径重建指定 Reader，核对重启、无消费者和审批边界；比较器可检测不成对的样本。 | 同条件真实 8×8 的 A、B 各一个有效窗口；逐项核实无人交付、业务覆盖、恢复耗时及交接次数。 |

当前默认策略保持原配置。上述实证窗口开始前无需推断哪一组更优。[本轮工程验收](215-oa-stage-team-implementation.md)

## 固定输入与现场采集

1. 在真实运行前取得本次窗口授权，记录实验 ID、UTC 时间、允许的模型/账号、资源限额、故障注入和停止条件。为 A、B 建不同 Run 和隔离状态目录。从同一源码基线、需求版本和已确认的验收合同开始。冻结输入、实际角色/模型版本、容量、权限、平台与故障参数。保留各组已批准 Proposal、有效配置描述符及 invoke。切换凭据、人工启动、手动重派或修补均记录时间和证据。
2. 对两组分别保存独立的、只读宿主记录及对应 Run 的 `events.jsonl` 和所引用的不可变侧车。业务窗口从初次 `plan.approved` **之后**算到实际终态，提前截止时记录截止事件。单独保留批准前失败和缺失窗口。若操作者在宿主直接重启而账本没有相应事件，以监督日志或进程日志登记该次介入。
3. 为每一例故障记录源事件、RecoveryCase 和有效动作的判据。诊断回复、请求派发、命令返回和实际解决原故障属于不同事实。例如按对照协议，绑定子任务的修复完成并被内核准入后才算恢复。未取得绑定的 effect 与 observed 事实时，首个有效恢复时长为未知。模型交接逐项登记实际调用或实际接收的回执；同一调用只计一次。无主等待的起止须能定位到事件时间，有合法等待责任人和唤醒条件的保留说明。
4. 逐项比对任务合同、冻结候选、覆盖报告、Judge 的正式回执和交付结果。Coverage 的 `evaluation` 由独立评估者根据验收证据填写，附本窗口引用。若已交付但缺 Judge，分别保留这两个状态。旧 LS-v1/8×8 证据可帮助选择故障；复用历史产物、人工接管的运行需据实单列。

## 固定扫描与按需 Plan 的对照

§8.2 是单独的 Plan 试验，与批准 Plan 后才开始的恢复 A/B 分开。各选一项小需求和一项跨域需求，为每项用两份新批准的 Run 比较原双扫描和显式按需分工。两边固定需求修订/摘要、源版本、基线、角色能力、模型、资源及同一套验收条款。保存各组的 Proposal/effective-config 摘要和实际 `workflow.invoke.accepted`；每次正式 replan 另记代次。

| 保存字段 | 现场证据与计算方式 |
|---|---|
| 选人及理由 | 从冻结的 `compiled_graph.stage_teams.plan` 记录 reason、唯一 author、所有 obligation_id/问题/输入/预期产物。未选策略时从冻结的 `prd-scan` 阶段保存双扫描名单，注明使用原模板。 |
| 首个有效结果耗时 | 以本次 `fanout.child.dispatched` 为起点，定位同 Run/阶段/child/attempt 首项通过结果准入的时间，保存两个事件 ID 并计算秒数。零专家组首项为正式 Planner 的合格交付；另列正式 Plan/Task Map 获准入的总耗时，以相同终点比较。没有合格回执时填未知。 |
| 覆盖、遗漏及补读 | 独立评估者按共同验收合同把每个条款映射到当前 Plan/Task Map 的具体任务和证据，保留缺口、冲突、附加阅读或重做。用本次 attempt 的 read receipt / 源索引及实际修订绑定确认补读的来源、原因和耗时，未知标记未知。 |
| 结论与干预 | 分开记录场景、策略、代次、实际样本数、模型/人工介入和评估者。对同一需求做成对对照，分别报告小需求和跨域需求。确定性 fixture 只证明组队、交接和计数可复现；真实模型的规划遗漏和速度须等待相应新窗口。 |

## 生成比较报告

在捕获目录准备 `pair.json`，每个 `path/sha256` 指向目录内 JSON 文件的完整原始字节。对照输入 `protocol.json` 在启动前冻结，两个审计文件分别从实际归档填写。使用下面的结构，示例标记需由真实值替换。

```json
{
  "experiment_id": "reader-rebuild-001",
  "evidence_mode": "real_provider",
  "authorized_at": "2026-11-25T12:00:00+00:00",
  "authorization_ref": "<本次真实窗口授权记录>",
  "controls": {"path": "protocol.json", "sha256": "<sha256>"},
  "arms": [
    {"group": "A", "run_id": "<A>", "state_dir": "state-a", "audit": {"path": "a.json", "sha256": "<sha256>"}},
    {"group": "B", "run_id": "<B>", "state_dir": "state-b", "audit": {"path": "b.json", "sha256": "<sha256>"}}
  ]
}
```

`protocol.json` 的 `controls` 对象固定七个可核验标识，分别为 `provider_profiles`、`capacity`、`permissions`、`target_baseline`、`fault_scenario`、`acceptance_contract` 和 `requirements`。宜采用冻结资料的摘要或不可变引用。并列的 `effective_actions` 映射恢复动作到本例可接受的效果事件，例如 `{"child-rebuild":["fanout.child.completed"]}`。应按真实生产者的 effect 绑定选择；若现有 effect 只确认派发，这条更严格的“完成”判据将报告未知。

每组 audit 包含上述七项实际取值、`run_id`、`evidence_mode`、`controls_sha256`、`invoke`、`plan_approval`、`window_end`。后三个值为该 Run 的事件 ID。若没有获准 Plan，尚无可对比的批准后窗口，应记录为启动前失败。真实运行另附非空 `provider_host_receipts`，其中每个 `path/sha256` JSON 对象绑定 `run_id`、`provider`、`model`、`trace_ref` 及 `host_capture_ref`。发生故障时填 `fault` 和 `recovery_case_id`。下列字段代表独立采集者对完整窗口的核对；缺失输出为 null，完整核对的空集合才写空数组。

| 字段 | 核对内容 |
|---|---|
| `interventions` | 每行含 `at`（时区明确的时间）、`action`、`evidence`，账本内介入也填 `event_id`。报告会把待核对的 Owner 事件另列。 |
| `model_handoff_event_ids` | 实际执行或接收的事件 ID，交接双方、实例和调用身份保存在独立审计附件。 |
| `unowned_waits` | 每段含 `opened`、`closed` 两个事件 ID 及 `evidence`。截至窗口结束仍未领取时用截止事件，注明等待持续中。 |
| `coverage` | 含 `evaluation` 和非空 `event_ids`。核对每项必需任务和验收条款的最终版本、通过与缺口。 |

```bash
PYTHONPATH=src uv run python scripts/compare-stage-team-pilot.py /path/to/capture/pair.json > /path/to/capture/comparison.json
```

两组保持相同的冻结有效配置和编译图，包括实际阶段、Reader 容量、验证门、RM runtime、看门狗和权限。比较器仅排除可区分两组的消费开关（常驻 RM、按需 reflect、Autoresearch 的启用及诊断消费）和项目名称/状态目录。两个组若放在不同目录中，公共工作流和授权资料应使用相同的项目相对配置。

正式 Judge 以 `goal.closure.synthesized` 的准入结果计入。比较器核对同一运行先前的签署结果准入、不可变引用与判词，或采用已核实的完整终局凭证；缺少相应依据的事件 ID 单列待核实。相同判词派生的兼容事件列在其下，独立的 `goal.closure.synthesis.failed` 归入尝试失败。交付以 `run.delivery.settled` 归集有确切因果关系的 ship 回执。没有直接 freeze 编号时，完整终局验收凭证可以证明具体 Judge 或交付事件属于该冻结；只见相同 Git HEAD 时保留未核实。不同 freeze、代次、目标 ref 或提交互相冲突时一律单列。

比较器读取每个 Run 的事件和绑定配置，检查配对字段与配置，输出批准后介入、模型交接、每段无主等待秒数、首个满足判据的恢复动作及时长、Task、Judge 和交付回执。Judge 与交付回执标明冻结身份已核对、属于其他版本或尚缺目标绑定。它复用现有 Run 隔离、恢复效果和自治信号投影。哈希校验保证归档内容一致；外部操作、业务覆盖结论、观察是否完整及归档来源仍由独立评估者核实。确定性测试使用 `deterministic_replay`，真实试点保留实际 provider/host 运行证据。任一关键项未知、尚未终止或有未核实 Owner 信号时，保持原默认配置。即使两组达标，变更默认策略也需 Owner 审核。

可复现的比较器回归覆盖正常来源、历史窗口重标、模式不符、越 Run 的回执、篡改摘要及控制条件不同。[比较器](../../scripts/compare-stage-team-pilot.py) · [确定性回归](../../tests/test_stage_team_qualification.py)

公开 Web 的事件详情、历史索引、Task 时间线、Trace 和 SSE 对 Worker 的 `agent.text`、`agent.thinking`、`agent.tool.use`、`agent.tool.result` 只展示活动类型、绑定标识与可验证的错误标记。原文保存在内部账本；已获准公开的回复由工作台会话入口按当前身份和目标单独读取。

同一产品在独立机器路径、tmux 和宿主授权下对照时，可在启动前的协议保存每组 `local_bindings`。比较器逐字段核对实际快照，仅归一化项目本地的会话名、目标目录、诊断输出目录、专属 worker cgroup 和宿主授权引用。配置中命令、受保护文件、权限、容量或超时不同都会保留差异。批准后的 Web 受控请求按 Run 归属审计。无法归属的动作进入复核。真实窗口还需绑定覆盖批准至终点的 `host_observation` 及原始采集摘要，缺少独立宿主观察时，介入次数保留未知。

现有项目 PrdFlow 与注册控制器沿相同审批路径绑定验证团队。已注册可自主委派的 PRD OA 若有影子 pre_impl 检查点，选择 Task 或 Candidate Verify 团队时会在待审批的候选配置中展示阻塞模式。缺少 OA 委派权限或检查点时停止装配。Owner 按候选配置摘要批准后，控制面才写入新配置。

### Judge 按 Task 核查独立 Verify 正文

当已签收的 Candidate Reader 合同绑定成员的独立 Task Verify 时，Goal closure 在冻结事实的 `task_verify_evidence` 中记录每个成员的 Task 提交、Verify 发布事件、准入事件、WRC 尝试和正文摘要。Judge 可从自己的输入索引按需读取 `task-verify-<task_id>`，回答已批准的 mandate。运行内核按被冻结的 Candidate、成员提交及承认这份正文的调用交叉核对来源。当前 Candidate 和 Discovery 的签收仍承担原有闭环职责，阅读单项 Task 正文不会增加一张 Candidate 票或代替 Judge 裁决。新增可读材料进入 closure identity 的摘要，同一批材料重复加载产生相同 identity。[实现](../../src/zf/runtime/goal_task_verify_evidence.py) · [回归](../../tests/test_goal_task_verify_evidence.py)

Judge 可在逐条结论的 `goal_coverage[].supporting_result_refs` 增补冻结成员的 Task Verify envelope。内核同时核对该结论的当前 Candidate 或 Discovery 主引证，以及本次 Judge 已完整读取该 Task 正文的凭证。首次 `zf result validate` 读取当前尝试的活动凭证，正式提交按同一规则核对封存凭证。顶层 `input_result_refs` 沿用冻结批次。

### Judge 重试后复核原 Goal 拒绝

周期性 Goal 对账可复核一次只因 `verification_evidence_missing` 被拒的完成声明。复核要求当前 Judge 的重试因果、正式准入、签署正文、原冻结和每位 Candidate Reader 的签名可复验；原拒绝时与当前时刻必须绑定同一完整批次。新审批、替代或作废版本、活动任务、人门及其他正常交付条件继续约束原声明。核实成立后沿原交付策略发布请求，交付和终态记录原 `rechecked_rejection_event_id`。历史拒绝、重试计数和原 Judge 签单均保留。产品交付和 Judge 当次实际阅读在无人验收报告中分别计量。

### Planner 与 Critic 交接的 JSON 原件

Planner 的 `zf result validate` 和 `submit` 对实际交给下一角色的 JSON 文件使用与 Critic 相同的读取规则。重复键的反馈定位到文件与 JSON 路径，并区分同值与冲突值。作者可以修改自己工作区的来源、更新绑定的摘要并在原尝试提交。准备好评审副本后，内核在派发 Critic 前再次检查该版本。需 Owner 评审和仅诊断的计划也核对其实际交付的来源。原有文本附件继续支持文本读取，JSON 端口沿用既有大小限制。

在真实 Run `prepare-TASK-9EEFCA-52b859f5ae02e2ca`，原先获准的任务图两次声明 `$.schema_version`。Critic 对冻结原件的读取失败，最终触发 Owner 门。以同一份请求和原件重放，新版预检会将具体字段返回 Planner。该样本的单版本结论保持为 Plan 阻塞；修复后从检查点继续取得的结果单独计量。[作者提交与冻结派发回归](../../tests/test_plan_handoff_json.py)
