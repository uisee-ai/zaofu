# V5 保真链路 P1/P2 修复复测

状态：本批修复已验证。真实 Run 完成 Candidate Verify、Discovery、Judge、Ship 和内核终态；过程包含两次显式 operator 恢复及隔离服务重载，不能报告为首轮无干预成功。

复用源码 worktree `/tmp/zf-v5-fidelity-20261031-004038/source`，分支
`wip/codex-20261031-v5-fidelity-e2e`，本轮起点 `2f72f850`。
依据：[原始失败记录](2026-10-31-kanban-judge-fidelity-e2e.md)、
[测试 Prompt](../runbooks/2026-10-31-0040-kanban-judge-fidelity-e2e-prompt.md)、
[本轮任务](../../tasks/done/2026-10-31-0250-v5-fidelity-p1-p2.md)。
实现边界见 [实现说明](../impl/v5-fidelity-owner-revision-and-verification.md)。

## 当前 HEAD 复核

| 项 | 当前问题与修复 |
|---|---|
| B01 | Plan 引用 Apply 没有正式需求修订授权，mandatory claim 改字被拒绝。新增回归先出现 2 failed，受控 owner amendment 后通过；覆盖旧文本、其他条款、其他 Run、重复 Apply、旧审批、新 Package 和 Judge 版本绑定。 |
| B02 | V5 模板关闭 Plan 门禁，synthesis 缺少审批语义字段。新增回归先出现 3 failed；增加有来源的审批决定并保护编译、Preview、Apply。 |
| B03 | 旧格式小批次缺少读取实例身份，工具包装/JSON 格式变化不能见证。统一页身份、严格完整匹配和受控包装解码；历史过滤输出仍 unknown。 |
| B04 | 有效空登记被 `decision_items` 真值误判为缺失。材料状态与 pending/blocking 数量分开；空/closed、缺失、坏结构、错误摘要均有回归。 |
| B05 | V5 静态 Candidate Verify 默认 8 路，与执行池容量耦合。独立 width 参数通过注册装配和编译进入现有 fanout；1/2/8 路实际 mock 派发、重放、全部验收门禁和容量保留均有回归。 |

## 验证层

`dev-verify plan --base dev` 由于 Config loader 的保守依赖扫描选择 713 个测试文件，并以
文件数量阈值升级为完整 tests。依照仓库当前按影响域验证的规则，未执行这个全量升级：
`dev-verify run --base dev --skip-tier deterministic` 执行其余自动层，后端另用明确的
受影响模块、直接调用方、共享合同和相关 mock E2E 列表运行；清单保存在私有证据目录。
这不构成完整 pytest 证明。

前端 `npm run build`、typecheck、Web unit、串行 10 项和 mock flow-smoke 10 项通过。
明确列出的 62 个后端测试文件得到 1241 passed、8 skipped、1 failed：唯一失败是
`test_workflow_request_enqueue_returns_without_provider_call` 在并行负载下 1.636 秒超过
1 秒断言，隔离重跑 1 passed（0.91 秒）；没有放宽断言，原失败日志保留。
后续 Config/多 Flow 参数修复层 686 passed、7 skipped，需求修订共享调用方层 87 passed。
第一次哨兵发现 Proposal 文件超过 1000 行，已将审批配置加载移至审批模块；
修复后哨兵 51 passed。以上各层有重叠，不能相加当作唯一测试数。

## 真实复测

隔离状态与证据：`/tmp/zf-v5-fixes-20261031-0305/`，Web 为 `0.0.0.0:8002`。
通过 Docker `mcp/playwright:latest`、host networking 创建项目 `hello-fidelity-fixes`，
Project ID `project-f46b465b`。源码和真实 Provider 子进程均绑定原 worktree 的 `PYTHONPATH`。
8001 未修改。所有产品业务提交与审批使用浏览器，未直接编辑 canonical runtime state。

输入沿用 Prompt 的 R01–R07，要求两级人工审批及全局 Candidate Verify 1 路，执行池 8 路。
在 Plan 引用 Agent 把默认问候 world 改为 driver，并使用原独立 oracle 的
`--default-name driver` 验证未修改的 R02–R07。

已通过浏览器创建 Task `TASK-9869C9`，Run 为
`prepare-TASK-9869C9-5326884fb547ffae`。真实 synthesis 提问了 R04，Owner 通过 UI
明确空白姓名返回码 2、stdout 空、stderr 精确 `name must not be blank\n`，没有 `error:` 前缀。
首次 YAML 未手工修改即包含 `plan_approval: true`、`verifyWidth: 1`，保存为
`evidence/04-first-preview.yaml`。随后 UI Start Workflow 自动拉起运行。
Scan 两位 Agent 已完成，18/18 必读收据得到完整 Provider 工具输出见证；
观察范围明确为历史工具输出，未声称压缩后仍在当前上下文。Planner 已结束，plan-critic 运行中，
Impl 尚未派发。首次 Critic 错把 JSON 展示转义当成 COUNT 命令的实际字符，发生一次误退回。
独立解析两版原始 JSON 后，两条 command 的字面反斜杠计数均为 0；原文在隔离夹具下
均得到零测试返回 1、单测试返回 0。证据为 `critic-command-countercheck.json`。
Planner 的返修补充了这组证明，第二次 Critic 已明确撤销误报并 approve。此项是语义审查误报，不能报告为
修复了真实产品错误，也不能计作首轮无错误成功。补充 Critic 方法：区分序列化表示与
解码后值，拒绝必须基于原文可复核反例；不以目测转义作为执行失败证据。

由真实 Plan 暴露的组合列 `状态 / 负责人` 会使已关闭决策被显示为 pending；
增加回归先失败，再改为优先读取摘要绑定 Task Map 的结构化状态，相关 49 项通过。
原始运行归档只读复查显示：Discovery 的 117 条和 Judge 的 50 条不完整历史输出仍
保持 unknown，未通过新匹配器追认。后续终态、修订和逐阶段见证见下文。


Plan 引用修改已通过浏览器完成：格式化测试表 → 引用到 Agent → 展示
`/acceptance/0` 和 `GC-R01` diff → Apply。产生正式 Requirement revision 3→4；
独立读取证明 R02–R07 未改，原版本仍保留。新 Planner 的 manifest 同时包含新
Requirement 和 `owner-requirement-amendment`。Impl 在此期间没有派发。

复测补齐两个投影/追溯边界：已绑定 Requirement port 却因 source_index 未重复
`authority_refs` 被标作缺 PRD；以及 `orchestrator.replan_requested` 中间记录仍带
旧 Requirement，实际 resynth/Planner 输入已是新版。新增回归先红后绿，分别改为
消费已准入端口、从受控 owner amendment 补齐 replan 记录。已追加的历史事件不回写。
这些后发现修复的真实生效范围与剩余验证在终态部分说明。

Plan 修订的 Critic 进一步暴露同名 `plan-rework-context.json` 的两个已绑定版本。
旧解析器只按 source/artifact 名称取第一项，第二个 digest 无法读取；两次自动尝试后
`prd.plan.failed evt-6a34f6699e24` 升级基础设施故障，尚未进入 Impl。
修复精确版本选择，并补齐已有 `stage-replan-new-generation` 的受控 reader 修复重试。
149 项相关回归通过，Web token、必需授权、终态/语义失败/过期/重复拒绝均有测试。
04:02 UTC 重启本次隔离 Runtime/Web 加载修复，通过 Playwright APIRequestContext
调用项目 token-gated action，成功追加 `prd.scan.completed evt-5836bdda751c`。
这是一次显式 operator 恢复干预，不计作全程无人干预成功；未直接修改运行状态或重写失败事件。
新一轮保留同一个 Run、Requirement revision 4 和人工 Plan 审批，Planner 已重新完成。

恢复后的 Critic `run-fanout-prd-plan-evt-5836bdda-synth` 提交 approve，17/17 必读输入
均已形成 Provider 历史工具输出见证，包含两个同名不同 digest 的 context。
04:09:59 新审批请求 `evt-f0da04c7cf81` 绑定 Plan `evt-11b61d968301`、Requirement
revision 4；之前没有任何 Impl dispatch。Playwright 验证完整原文中的 driver、7 个
格式化表格，以及 Review packet 全部 ready、decisions=0。04:11:11 UI Approve 产生
`evt-a61add908037`；04:11:19 才派发 `dev-lane-0`，两级审批未绕过。

本次 Autoresearch 还报告旧人工升级被无关 worker 状态误消除。对未修改的 Run Manager
实现先复现 4 failed，收紧无作用域匹配后 186 项投影/运行管理/Web/告警测试通过。
原始真实事件只读重放保留 `evt-52287a4f0f69`，不再将其归为 false_positive。
这是投影恢复证据的修复，不自动关闭人工升级或把修复建议标成已批准；本次持续运行的
Runtime 无热替换，该监控修复以回归与真实账本重放验证。

首次 Impl 没有产出代码：Agent 将 JSON 显示转义手工复制进 shell，提交了
`dev.blocked evt-8049181032cc`。冻结合同实际 command 的反斜杠数量为 0，摘要
`b225db12...`；执行事件 `evt-c0493d2afb6d` 的实际命令多出 2 个反斜杠，摘要
`444eaedd...`。从冻结快照解析后直接执行，零测试返回 1、一个真实测试返回 0。
因此是命令转录失败，不是上游合同错误；失败收据声称的 command_digest 不等于
实际执行文本摘要，不能据此证明合同不可满足。

自动 Discovery 重复了同类转录错误，且其替代任务 HELLO-002 缺少 rolling_smoke
标记；`task_map.amend.failed evt-5c73ff8e2b8e` 正确阻断了不完整替换，旧任务/Plan
未被覆盖。新增 Impl/Verify 方法要求解析绑定合同、按 ID 取 command 值直接执行，
以实际值/摘要核对失败，且两角色分页说明与统一 v2 接口同步。
04:24 将这三份角色方法更新安装到隔离项目的 source skills（非产品提交，记录部署摘要），
再由已有 `zf recover task-impl` preview/apply 追加 `task.rework.requested evt-c250251781e6`。
04:24:15 第 2 次 Impl 以原 Task/Plan/Requirement 开始；这是第二次显式 operator
恢复干预（CLI），不冒称全部恢复均通过 UI 或无需人工干预。

第 2 次 Impl 在相同 Provider session 中明确撤销转录误报，按冻结快照解析命令，
完成 9 个具名子进程 RED/GREEN 用例及确切提交自检。04:30:31
`dev.build.done evt-f094c9632445` 接纳提交 `1f3798121898217507d921e30cbdade6eb716ffa`；
04:30:46 独立 `verify-lane-0` 派发。其物化 skill 与本次源码方法摘要一致。
Impl 恢复会话沿用的旧物化包没有热替换，正确方法由受控 retry 的明确纠错上下文传递，
不能将其误称为自动热加载新 skill。

从该不可变 Git 提交提取两份产品文件，在工作目录之外运行冻结 oracle：R01–R07
及两种解析失败、至少 5 个真实测试的补充检查全部通过（实际 9 个测试）。独立 diff
严格只有 `hello.py`、`tests/test_hello.py`；源码审阅仅标准库、无网络与额外功能。
证据 `16-independent-oracle.json`。这不替代工作流后续 Verify/Discovery/Judge。

04:35:59 独立 Task Verify 完成（`evt-99caf7e203a5`），增量集成后 04:36:17
冻结候选 `da5ed83361204317d5036c42567c2ac0a2f677b0`。04:36:31 全局
`prd-lanes-verify` 只派发一名 `verify-lane-0`，没有缩减配置中的 Impl/Verify 8/8 池。
对冻结候选重复独立 oracle 和 diff 审阅仍全部通过，证据
`18-frozen-candidate-verification.json`；这次只证明真实 1 路配置，1/2/8 的可比实测
成本 A/B 没有执行，不能用本轮带返修/人工干预的总用量推算节省比例。


## 最终闭环与真实验收

- Candidate Verify：`test.passed evt-5b44749b8714`，04:42:24 UTC。
- Discovery：`flow.goal.closed evt-4fadb5d2531f`，04:49:15；当前 claim set 的
  objective_ref 为 Requirement revision 4，GC-R01 为 driver，15 个目标条款保留强制属性。
- Judge：`judge.child.completed evt-94ef30ad3c06`，04:53:28；closure result 已准入。
- 首次 completion gate 正确保留阻塞：`evt-e9528820eed4`，open_feedback/pending_handoff。
  只读重放发现 operator retry 的 dispatch 通过 `rework_feedback.blocking_event_id`
  绑定请求，而旧 reducer 未消费，交接一直停在 published。新增测试先 5 failed；
  精确匹配 task/run/generation 后，49 项交接、候选绑定和 Orchestrator 门禁回归通过。
  旧 dispatch/错身份/错验证提交仍不能关闭。真实账本重放由已存在的独立
  `test.passed` 关闭交接，不追加伪造反馈成功事件。
- 04:56 重载隔离 Runtime/Web 后，内核自动重新评估同一 completion claim，
  `ship.completed evt-669e1a00ff19` → `run.delivery.settled evt-7ede6d67a017` →
  `run.goal.completed evt-a78223304020`（04:57:05 UTC）。交付提交
  `424f14c8ec2c38084258f0079077671a5a6be69b`，冻结候选 `da5ed833...`。
  没有再次启动 Judge，也没有人工写 task.done/run.goal.completed。

最终工作树再次执行独立 oracle：R01–R07、缺值和未知选项检查通过，9 个真实子进程
测试通过。交付提交与冻结候选的两个产品文件逐字节相同；相对基线仍只有
`hello.py`、`tests/test_hello.py`。四份临时部署的测试项目 source skills 已按部署前摘要
恢复，未混入产品提交。证据 `20-method-restoration.json`、`22-final-proof.json`。

Docker Playwright 最终断言 Workflow Run 为“已完成 · 运行控制：Kernel”，
运行结果为“来源：内核终态事件”；`22-terminal-graph.png` 和 browser trace 留证。
页面 JavaScript errors 为 0。测试脚本曾发生错误定位 hidden option、错误模式下查找
引用文档和未关闭模态框的定位超时，均纠正定位后再验证，不能把脚本超时算成产品通过。
图中未调用的 research routes 仍显示等待，本批没有声称验证所有图投影的正确性。

| 验证点 | 本轮证据 |
|---|---|
| 两级人工门禁 | 首次 YAML 自动 plan_approval=true；Plan 修订重新审核，Approve 后才 Impl |
| 引用 Agent 正式变更 | UI 局部 diff + Requirement/claim diff；revision 3→4；R02–R07 未改 |
| 新需求贯穿下游 | Planner/Package、Task contract、冻结 candidate、Discovery/Judge closure 绑定新需求 |
| 输入历史送达 | 修复后 Critic 17/17，Impl 13/13，Task Verify 15/15，Candidate Verify 15/15，Discovery 20/20，Judge 26/26 必读项均完整见证 |
| 验证宽度 | 真实 Candidate Verify count=1、peak=1，执行池仍为 8/8；1/2/8 调度/重放另有确定性测试 |
| 恢复边界 | reader 修复经 Playwright token-gated API；Impl 经既有 CLI 受控 retry 且复用原 session；交接由重放与内核复评闭环 |

上述完整见证指 required sources 各自的页覆盖，不等同于“模型理解全部正确”或
“压缩后所有输入仍在当前窗口”。旧失败 Critic 的缺页、旧 Discovery 117/Judge 50
不完整输出及 OA 未观察项保持原状态，未用新成功结果涂改历史。

本轮账本截至最终证据采集记载 input=1,918,043、output=124,130、cache_read=49,480,704 tokens；
它包含返修、恢复、常驻角色及诊断开销，cache_read 单列，不能相加后当作模型上下文长度。
没有运行真实 1/2/8 成本 A/B，也没有据未定价用量声称费用为零。
完整 approved-config 输入约 155 KB 仍需按合同读取；进一步减少重复上下文尚需单独证明，
本批没有通过截断材料换取表面 token 下降。

本轮 Judge 首次通过；跨 attempt/session 的旧输出拒绝、封存见证保留由确定性测试覆盖，
**未强制注入一次真实 Judge 故障再复用 session**。600 秒 idle/provider kill、完整故障矩阵、
Channel/CLI 分别独立的真实入口 E2E，以及统计可靠性/成本对照仍属于原
[完整 E2E 任务](../../tasks/active/2026-10-31-0055-kanban-judge-fidelity-e2e.md)，不在本批中冒称通过。

最后增量门禁：交接影响闭包 49 passed，premerge 51 passed；其他层见上文及
`verification-summary.json`、`final-dev-verify-receipt.json`、`handoff-delta-receipt.json`。
各轮有重叠，不累计成“唯一测试总数”；未执行全量 pytest/coverage。


## 清理与保存

已对隔离产品项目和其事件绑定的 3 个 Autoresearch 诊断实例发送 `simulation.done`，
通过 `zf stop --fast --include-run-manager` 停止；8002 Web、Docker 浏览器和临时 token
已清理，8001 未操作。运行状态及 worker workdirs 在归档核验后移除，产品 Git bundle
已 verify；浏览器 trace、截图、原始日志与验证收据保存在私有证据目录
`/tmp/zf-v5-fixes-20261031-0305/evidence/`，文件权限 0600。

原开发 worktree 保留；隔离产品的交付源码保留。3 个运行时自动创建的诊断 checkout
含未提交配置/候选材料，已停止并保留，不强制删除其 source；各自 `.zf` 状态已归档清理。
最终核对没有归属本次模拟的残留进程，8002 已关闭。清理明细见 `cleanup.json`。
