> 状态: active

# 184 · 真实任务交付

来源：用户已批准执行真实验证第一至第三步；基线 `5dad3cb7`。

## 范围

隔离项目分别经 Kanban 和 Channel 受控入口，走 Preview/Approve、实际 Workflow 与交付验收。

## 验收

Issue -> verify: 原失败行为修复并独立测试；PRD -> verify: Planner/Critic、计划批准、实现、Verify/Judge 与原始 AC 闭合；缺阶段明确记未完成。

实施与证据见 `docs/impl/184-live-validation.md`。未闭合真实验收不得标 done。

## 本轮结果：未完成

- Kanban 真实建单、路线 Plan、提案批准与重复批准幂等已通过。
- 轻量 Issue 被缺失 Product Acceptance 产物阻断；标准 Issue 已真实调度 triage，但首次输入需人工补 Enter，600 秒角色预算耗尽后 blocked。原始缺陷仍未修复。
- Channel 生成完整 canonical PRD；Owner 发现流程审批说明混入 explicit_non_goals，已通过受控入口驳回。未确认、建单或进入 PRD 执行。
- 未到达实现、Verify/Judge、独立产品验收，保持 active。

后续：修复并验证原生终端首次输入确认 -> verify: 无人工 Enter 且有 native session/turn 接收证据；修订 PRD 非目标方法 -> verify: 原需求完整、流程限制不污染交付合同；再在冻结预算内重跑两条交付链路。

## 2026-10-23 后续复测

185 实施轮中，Issue triage 与 PRD scan/planner 首次输入均自动接收，没有人工 Enter；
旧终端问题本次未复现，未擅改 transport。Channel PRD 非目标污染已修正，Owner 确认、
完整建单、独立启动批准和重复批准幂等真实通过。Issue 仍在 operation token 预算耗尽时失败，
未达到产品交付验收。该任务保持 active，后续以新 [逐分支验证记录](../../docs/impl/185-routing-admission-validation.md)
和关联 185 backlog 继续追踪，旧失败记录保留。

2026-10-23续批真实结果：标准Issue TASK-5577E6从Kanban批准经Critic/实现/Verify/discovery/Judge
到Kernel ship及根/子Task done；候选和交付后外部6项检查、Docker真实看板通过。
PRD TASK-BC14CC两次开发和第一Verify通过、外部65项检查通过，但第二Verify未提交时
触发12,155,516/12M Run token上限而blocked，不能签收产品交付。两运行均已保存终态、
校验归档并清理。相同需求/预算的当前修复版PRD重跑在current-prd中继续，整体保持active。

## 2026-10-23 续批最终验收状态

`c40c2239 fix: evaluate file-based PRDs against bound source snapshots` 修复真实文件PRD
独立评审入口；自动闭包182 passed / 1 skipped，sentinel51通过。`12d8d75c`和
`ccac5410`补前置审批精确取证及规划命令方法。真实独立评审34项PASS，之后才Owner
确认和单次启动；未把文档评审当产品验收。
最新TASK-5CB69E首版Planner/Critic通过后，Owner实测发现取证查询673KB却遗漏建单前
原始审批事件，受控驳回返修。返修操作在2,096,853/2M token处blocked，未批准计划、
未派发writer、未形成候选。标准Issue先前已完整交付；PRD仍不满足完整交付验收。
本任务保持active；后续先解决有界返修/验证的上下文和预算可行性，再重跑，不能放宽
预算或删减AC制造绿灯。详见[本轮完整结果](../../docs/impl/185-bounded-execution-and-recovery.md)。


## 2026-10-24 真实运行与缺陷续批

冻结源码e4548e95的General TASK-8B42B7五阶段全部准入、Kernel done，独立报告核对及
Docker看板通过；同Task/Run的reader dispatch切换确实建立新session。
新PRD TASK-754624文档31项评审和Owner/Plan批准通过，三writer形成4cc787c6候选，
外部完整oracle65项和产品21项测试通过；但API Verify协议修复耗尽、CLI Verify准入
blocked，PRODUCT Verify期间Run达到12,113,967/12M tokens而blocked。未ship，
不能签收PRD完整交付；真实交付探针exit1，状态投影正确。两运行均已归档校验并清理。

运行结束后修复候选验收与affinity提示矛盾、增加不消耗提交授权的Verify预校验、避免
同reader兄弟预注册反复重启、修正fresh/resume遥测；补Critic原位scratch方法及
Planner/Critic真实事件producer与实施开始顺序核对。原运行失败保留，未热改冻结版本。
验收：坏报告 -> verify: CLI预校验拒绝且授权/事件/sidecar不变；修正后 -> verify:
正式提交仍通过既有准入；绑定PAS -> verify: 完整旅程范围明确；普通切片 -> verify:
原范围不扩大。修复后完整真实PRD仍需新Run验证，本项保持active。
详见[续批证据与解决路径](../../docs/impl/185-bounded-execution-and-recovery.md)。
证据根：`/home/min/workspace/public/zaofu-evidence/185-context-20261023T225800Z/`。

本批最终回归：21文件414 passed；最终预校验/技能22 passed、1 skipped（两组有交集）；
自动sentinel51及三流mock10通过。三份技能副本一致。未运行full pytest，修复后完整
真实PRD未重跑；详见续批实施文档和followup-automatic-receipt.json，保持active。
