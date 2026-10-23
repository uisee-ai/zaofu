# 整体交付配对验证协议

用户批准按六步方法执行；任务见[执行记录](../../tasks/active/2026-10-21-0656-delivery-evaluation.md)。
此前结论与负结果见[原评估记录](real-plan-ab-full-build.md)，不由新试验覆盖。

## 不可变比较对象

A=`5191c9e6`，B=`4b0a96e0`；评估工具在独立worktree实现。两组同一产品种子提交、
PRD、模型gpt-6-astra/medium、单lane的PrdFlow fanout、预算、自动交付审批策略、
独立Critic/Verify和外部黑盒验收。原cargo人工Plan审批不变。
两组均使用显式self-check、maxAC=0，因此本批比较代码/skill差异，不估计maxAC配置变更收益。

首轮是小产品pilot，单位为一份完整PRD的一次交付；AC不是独立样本。固定首对cargo-mini，
两组都运行（第一组失败也不取消另一组）；两组目标完成且外部验收均通过才继续库存、发布。
AB/BA顺序使用固定种子生成并在首次调用前冻结。每个arm最多2400秒，run最多1000万token，
单操作600秒/150万token，语义rework按配置2次，不手工代写产品。预算上限不代表预算必耗尽。

全部mandatory独立验收、无新增严重缺陷和错误通过是硬门；完成率不得下降。
总金额成本比≤0.80、耗时比≤1.10、人工干预不增加为联合目标。金额必须有可追溯计价；
未知模型价格不算免费，计价缺失时金额门UNKNOWN，不拿输入token替代。报告失败、返工、
缓存、输出以及人工时间。估计型安全预算不是已证实账单。

## 证据路径与执行

`scripts/eval-delivery.py prepare --root /tmp/zf-delivery-eval-<UTC>/` 创建新输出，冻结
registration、各PRD、oracle、镜像和两个干净版本，两个arm克隆同一产品种子。
`run-arm --root ... --run-id cargo-mini-baseline` 通过真实 `zf init`、intake、clarify、
submit、start调用生产入口；不拼接假task事件或写canonical状态。status只读事件。

`tests/e2e/delivery_acceptance.py` 在目标提交的干净克隆执行npm ci/test/build、真实HTTP、
故障与重启、Docker Playwright；判定与对应原始日志归档。oracle置于产品外；host provider
不是安全沙箱，必须核对trace是否越界读取，不能把路径分开说成强制隔离。规划/假脚本退出0、
只有task.done或文件存在均不能替代产品通过。

机制层复用最近79项定向通过收据，并验证本轮评估器的冻结/失败分类边界。正式统计确认批次
在pilot后根据波动另行预注册；此轮最多三对，不能宣称显著性。发布是不同业务迁移探针，
不是由独立第三方保密的holdout。任何中途候选更改必须新批次；不得挑选有利重试。

## 完整cargo前置

完整cargo需先闭合Plan责任、保留人工审批并取得真实数据/GT/设备/仿真/业务决定。
小产品绿色只能允许扩大验证，不代表149要求/36指标已交付。该阶段需从相同产品基线
分别创建A/B运行；不能拿已有B规划结果与A从零启动直接比总时间。

运行结束/诊断完成后通过sanctioned CLI记录simulation.done并停止本轮tmux/provider，
复制必要证据到workspace evidence目录后清理自有临时目录。不得清理其他会话或8001。

## 2026-10-21 实际启动记录

- `...T0705Z`：两组在provider调用前同样触发目标app目录不存在的预检异常，保留
  preflight-failure.json；种子补空app/.gitkeep，无业务实现。
- `...T0710Z`：两组同样暴露Codex不能使用当前Claude SDK stream-json路径；无完成
  provider turn。候选额外有一次评估runner误用嵌套目录的 `start --simulation`，已记录
  operator-intervention.json且不重置计时。该批不能用于效果比较，两个arm均停止。
- 当前批 `/tmp/zf-delivery-eval-20261021T0718Z` 使用两组相同Codex/tmux；顺序为
  cargo-mini B/A、warehouse A/B、publishing B/A。registration摘要为
  `35d35c235fd5e417c25a85021d140e218e91e0798ea70e7b3b9343b92fd91118`。
  该批已结束：两组两路Scan均完成，但Planner都在600秒操作预算门被阻断，未进入Impl。
- 固定Docker镜像有完整Chromium但无headless-shell。外部浏览器运行器只增加
  `channel: chromium`，在首次产品验收前对两组一致修正；原oracle与兼容性修订摘要
  分别保留在oracle/和oracle-runner-v2/，oracle-runner-amendment.json说明不改变
  行为断言、API oracle、阈值或镜像。浏览器环境预检通过，不算产品证据。
- 新工具/相关机制闭包59 passed；增补计价、账本损坏和state_dir边界后工具/指令15 passed。
  两者有重叠，不合计成独立测试总数。dev-verify自动层29 passed/1 failed：缺少
  `.gitlab-ci.yml`，独立baseline复现；不伪造CI文件、不改测试预期。
- 真账本为subscription/unpriced，不能用cost_usd=0声明免费；已询问实际计价口径，
  不阻止继续质量/完整token/耗时验证。

## 首轮结果与结论边界

**整体改善未获证明，完整交付门未通过。** 单个配对pilot无法估计稳定完成率或统计显著性。
质量验收未执行，应记NOT_RUN，不能把未出现产品缺陷写成质量通过。

| 实测项 | A：5191c9e6 | B：4b0a96e0 |
| --- | ---: | ---: |
| goal完成 / 外部验收通过 | 否 / 未运行 | 否 / 未运行 |
| Scan完成数 | 2 | 2 |
| 被接纳Plan / Impl / 产品冷构建 / Verify | 均未到达 | 均未到达 |
| arm耗时（秒，启动至终态检测） | 892.03 | 882.28 |
| Planner预算检测耗时（秒） | 611.55 | 604.13 |
| 非缓存输入token | 175,972 | 221,983 |
| 缓存输入token | 1,506,944 | 1,616,768 |
| 输出token | 22,905 | 20,516 |
| 金额 | UNKNOWN | UNKNOWN |
| arm内手工改产品 / 手工恢复 | 0 / 0 | 0 / 0 |

账本按delta与dedupe_key计数，未重复累计cumulative快照。B非缓存输入增加26.1%、
缓存输入增加7.3%、输出减少10.4%。失败终止时间略短不代表交付更快；不同token类别
也不能相加后充当金额。操作预算由Kernel采样执行，600秒是配置阈值，实测存在检测延迟。
操作员准备/观察/诊断时间未完整计量，不宣称总人工时间改善。

两边Planner都把计划生成和指定结果scratch更新合并成大型shell命令，触发
`worker.scope_write.rejected`。A/B的`hook_workdir_guard.py`相同；其保守shell检查
命中“命令含项目根绝对路径、未含assigned workdir绝对路径”的条件。此证据不足以断言
所有命令副作用都安全，未绕过保护或手工执行被拒命令。

A在被拒后通过逐文件补丁继续，先后两次预提交校验失败：第一次包含assembly与依赖bundle
共用owner的自锁风险及capability映射不一致；第二次剩余product acceptance的
`assertion_id` / `observation_kind`缺失。B未到达预提交校验。两边最终均因操作预算超限
阻断，不能把全部延迟归因于一次保护拦截。A技术Scan还发生一次自动重派发，已计入全量
耗时/token，尚未证明与既有correction关闭竞态同因。

两边各38项加载skill的源码/运行副本摘要均匹配事件。结束后检查A的34次、B的39次
工具调用，未发现直接引用外部oracle路径或脚本名；这不是强沙箱隔离证明。

证据归档：`/home/min/workspace/public/zaofu-evidence/20261021-delivery-eval/`。
`pilot-20261021T0718Z/archive-manifest.json`绑定1,095份文件的摘要；保留注册、原oracle及
兼容性修订、原始事件/账本/日志/必要transcript、实际计划、校验失败与measurement。
不归档provider认证/private目录。前两次环境失败独立保留在setup目录，不计为产品收益。
两组均通过CLI发出simulation.done并stop成功，运行PID与tmux已退出；归档摘要复核后清理
本轮临时根目录，cleanup-receipt.json记录清理范围。最终报告的docs验证层8 passed；
未声称运行整个分支的全量确定性测试或真实产品E2E通过。

六步执行状态：①冻结完成；②机制闭包通过但自动工具层有既有CI文件缺失；③首对已执行、
交付失败；④迁移探针未启动；⑤完整cargo未启动；⑥本轮负结果已汇总归档、总体任务仍active。
按冻结门不继续库存/发布或完整cargo，也不更改本轮预算重试挑选结果。

下一轮先复现并解决通用Plan交付问题：将计划文件编写与受控scratch更新分开，明确给出
assembly owner与验收assertion的完整契约示例，并分别验证合法更新可通过、越权写入仍拒绝。
这些是待验证方向，不是已实施修复。修改后冻结新候选，重新开展相同条件的完整配对；
只有小产品真实交付和外部验收通过才扩大验证。原cargo仍需要Plan责任闭合、人工批准与真实输入。

## 2026-10-23 启动恢复诊断

复合命令的 signed scratch 放行仅接受完整的简单绝对路径；混入裸项目根目录引用、
动态后缀或仅匹配 scratch 前缀的其他路径时仍拒绝。对应反例由
`tests/test_hook_recv_codex.py` 覆盖；该检查延续既有字符串启发式，不是 shell/Python 沙箱。

后续单次 mini 诊断在 Scan 阶段观察到三次 `worker.pane.dead_observed`，仅一路 Scan
完成，最终触发 600 秒 operation budget。未进入 Planner，不能用“未出现 scope rejection”
证明 Planner 实跑修复有效。事件与注册归档于
`/home/min/workspace/public/zaofu-evidence/20261023-mini-plan-fix/`；pane 原始退出原因未确定。

恢复等待存在独立的可复现缺口：`task.dispatched` / `fanout.child.dispatched` 写入的
内核心跳种子采用 900 秒 worker stuck 阈值，导致死 pane 的恢复晚于 operation 预算。
watchdog 现在只为这种种子保留 `min(stuck_threshold_seconds,
spawn_ready_timeout_seconds or 120)` 秒的启动宽限期；已有真实心跳仍使用原 stuck 阈值。
死 pane 探测、活动任务或 fanout 责任、原有重试上限和 safe-halt 门继续生效，未增加重试预算。

回归入口为 `tests/test_capture_logs_watchdog.py`：宽限期内不重启，期满后普通任务与
无独立 Kanban task 的 reader child 都能恢复，真实心跳保护长 turn。真实重跑仍需冻结
候选源码、PRD、预算和审批策略，并保留失败日志与必要的运行状态证据；这些机制测试不等于
完整产品 Build / Verify 通过。

本轮 `20261023T095656Z` 单次诊断已执行：两路 Scan 完成；Planner 的复合 artifact/scratch
写入实际通过，但两次预提交校验后仍缺 `assertion_id` / `observation_kind`，在 610.501 秒
触发 600 秒预算，未进入 Critic/approval/Impl/Verify。终态前未发生 pane 故障或 respawn，
因此恢复时限效果由确定性回归证明，不能用该次顺利启动作因果证明。完整证据在
`/home/min/workspace/public/zaofu-evidence/mini-recovery-20261023T095656Z/REPORT.md`；下一项应
修正技能/briefing 中的产物契约指导，并用失败样本验证，不能用放宽预算代替契约修复。

后续候选补齐 `zf-plan-task-map-contract/references/product-acceptance.json` 可校验示例，
在 PRD skill 和编译 briefing 中列出 journey/assertion 准确字段，并明确 assembly owner
和 real-E2E tier 的约束。源码及两套 skill 分发副本同步。离线回放保留原失败；仅在副本中
补 assertion_id、将原 type 映射为 observation_kind 后完整预检通过。该回放验证契约修正，
不替代新的真实 Planner 生成测试，也不把手工修正副本提交到原工作流。

该候选真实重跑 `20261023T102602Z`：两路 Scan 完成，Planner 确实读取新示例；最后几秒
写出草稿，但在 600.931 秒触发预算，未完成在线预检或提交。未修改草稿的离线预检确认
上述断言字段、assembly owner、E2E tier 问题消失，仍有 14 项 capability 集合不一致。
这只支持具体契约提示在一次生成中起效，不证明整体 Plan 交付改善。报告与原始产物位于
`/home/min/workspace/public/zaofu-evidence/mini-contract-20261023T102602Z/REPORT.md`。

## PRD Planner 的产物完成与独立评审

v4 blocking 诊断 `20261023T122200Z` 取消 operation/run 墙钟上限，保留资源预算与人工
Plan 审批。Planner 用时超过 600 秒后完成在线预检、修复 owner/tier 诊断并提交；但它保留
`recommendation: abstain` 等待独立 Critic，旧 `prd-plan` 缺少结果语义声明，导致该产物
报告进入 reader 的负面结论分支。提交被接收不等于该子报告语义通过。

PRD v3/v4 共用的 `prd-plan` 现在声明现有 `result_semantics: artifact_production`。
Kernel 通过派发身份传递此语义，区分“规划产物生成完成”和“是否批准计划”；真实 provider
执行失败仍失败，候选预检、独立 plan-critic、人工审批及后续产品验收继续生效。不能用
强迫 Planner 自填 approve 或放松矩阵校验替代这条责任边界。回归见
`tests/test_prd_plan_result_semantics.py`，覆盖真实 controller 加载、派发语义及结果适配。

该修正未热更新到已冻结的实跑。诊断证据在
`/home/min/workspace/public/zaofu-evidence/mini-v4-20261023T122200Z/`；首次技术 Scan 无
provider 活动，通过一次 `zf restart tech-scan` 恢复，需记入人工干预，不能声称全自动完成。
