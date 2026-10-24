# 185 · 已支持路线的可靠性收尾

关联[185设计](../design/185-agent-routing-delegation-and-workflow-recovery.md)、
[本批任务](../../tasks/done/2026-10-24-1206-185-supported-route-reliability.md)、
[长期Run签收](../../tasks/done/2026-10-24-0947-185-long-horizon-recovery.md)。
本批以Owner批准的收敛范围验收；历史失败、原预算及来源提交仍保留。

## 当前支持范围

主Agent判断回答、澄清、跟踪或交付；交付路线来自当前zf.yaml目录。
简单源码任务可选择目录中适用的light：一个writer、独立Verify/Judge、Kernel交付。
不增加single执行内核、关键词路由器或第二任务状态机。
Kanban保留当前用户任务合同；Channel保留Owner意图、Leader及canonical PRD版本，
不能为了适配light缩小完整PRD。只有Owner明确批准的子任务才使用approved_task。
Task创建、Workflow预览/批准、运行派发仍是不同步骤。

启动前配置选择和Task Map后的适配复核保留。现有配置不适用时明确暂停、澄清或提出
受控重规划/修订；本批不实现运行中热换配置。语义选择归Agent/skill，机械边界归Kernel。

## 范围对账与延期条件

| 事项 | 当前结论 | 后续触发条件 |
| --- | --- | --- |
| PRD完整交付、长Run恢复 | 已完成，见下表真实证据；旧12M/2M目标由Owner修订替代 | 新缺陷或新验收要求 |
| Research结构化交接 | 已有真实6项AC/6项测试/5项提案及引用审阅；不是实际建单批准 | 下游实际消费协议变化 |
| Codex原生助手 | Owner取消，本批不接入；保留禁用/能力不足拒绝 | 重新明确提出并授权 |
| Kimi | Owner暂停 | 重新授权 |
| 运行中配置换代 | defer；当前不承诺热重载冻结配置 | 已确认必须保持同Run切换配置的实际场景 |
| 通用源码single新内核 | 本批用现有light承接适用任务，不新增平行内核 | 现有路线无法满足的明确任务合同 |
| 跨Provider接替 | defer；同Codex接续不算跨Provider证明 | 明确第二Provider、可用认证及切换验收 |
| 通用权限/外部副作用恢复包、跨计量域归一化 | defer；现有同域计量续算与缺证拒绝保留 | 外部写入、多权限或计量域迁移需求 |
| 强制只读OS隔离 | 未验证；普通Codex调用可用不等于隔离可用 | 不可信、多租户或强制只读承诺前必须完成 |

旧总任务/扩展任务标记superseded并指向本批；旧文中的“当前/待验”是当时的记录。
取消、延期、替代均不记为已实现。标准Issue及PRD真实交付相关旧任务按最新授权签收。

## 已有真实证据与本批回归对应

证据根简称：E=/home/min/workspace/public/zaofu-evidence/。

| 分支 | 真实证据（保留原冻结版本） | 当前回归 |
| --- | --- | --- |
| 未提交源码的writer恢复 | E/185-handoff-20261024T045000Z/light/delivery-review.json：未提交测试保留，独立Verify/Judge及evt-f079f3d60e11交付 | maintenance_worktree_checkpoint、writer_budget_recovery、recovery_checkpoint_binding |
| Verify进程丢失自动恢复 | E/185-continuation-20261023T164153Z/recovery-r2/bounded/recovery-verdict.json：watchdog接续，writer不重做 | reader_context_boundary、reader_budget_recovery |
| 原PRD reader预算阻断后续行 | E/185-supervised-20261024T070200Z/live/prd：原Run、已完成兄弟及上游工件保留 | reader_budget_recovery、workflow_resume_budget |
| gap版本/验收继承 | 同上：旧99b0ed0失败、新30aa6a3及实际main48778d1通过65+6项外部检查 | plan_acceptance_rework、flow_discovery_context |
| 完成后重启不重执行/交付 | 同上retained-task-reconciliation-result.json：4Task done、19次Operation/1次Ship不变 | goal_retained_task_settlement、long_horizon_root_task、pending_handoff_reconciliation |
| Goal终态不被旧closeout覆盖 | 同上goal-closeout-real-event-replay.json：真实事件前缀回放；不是重新Provider全流程 | run_manager_goal_closeout |
| 用量基线/长时策略 | 同上long-horizon-runtime-proof.json：基线不重置，3.836h真实交付；5h/72h是受控时钟测试 | workflow_budget_guard、workflow_resume_budget、long_horizon_run |
| Research结构化产物 | E/185-handoff-20261024T045000Z/research/owner-report-review.json：冻结18c625a2、6/6/5结构及语义审阅 | 本批仅对账，不重跑研究 |

真实数据与mock分开签收。本批恢复闭包15文件166项通过，当前没有为恢复链新增另一套机制。

## 本批发现与修复

### 测试映射

原规划6项未映射：workflow_request_identity、两个按路径加载的live验收脚本、三个
故意带缺陷的产品夹具。前者补身份/来源行为测试；后五项登记精确动态依赖，关联实际
验收测试，缺失测试文件仍报未映射，未知新路径仍拒绝。语义夹具分别验证原缺陷失败、
修复版本通过，不把故意带缺陷的样例直接当产品成功。
5 RED/1 PASS复现映射遗漏；当前整分支plan的errors为空。这只证明映射完整，
不等于全部选中测试已运行。自动测试发现可选.gitlab-ci.yml在当前Git树不存在；
将此检查明确标为skip，保留未验证说明，不添加虚构CI配置或声称CI通过。

### light启动批准重放

实际CLI正向链复现：第一次apply绑定Request后，重复相同proposal先执行旧参数预检，
报workflow_task_stale。CLI原有预检重复了ControlledActionService的校验，且顺序绕过
其加锁的proposal幂等路径。修复移除这层重复预检；Token与proposal存在性检查仍先执行，
由受控服务先验证精确提案/幂等，再对首次执行校验当前Task/来源/配置。
重复已完成动作只返回已有结果，不重新授权新的执行；首次旧合同/损坏来源仍拒绝。

双入口测试初版夹具误用目录字段、漏登记kind_routes、使用错误owner/tier；这些属于
夹具修正，不计产品缺陷。真正RED为首次启动成功后同批准重复提交被误拒绝。

## 验证记录

当前批次日志：E/185-reliability-20261024T120651Z/。各层有交集，不累加唯一测试数。
本批只签收当前收敛范围；完整分支验证未执行，下面分别记录各层结果。

### Task文档刷新误使启动批准失效

CLI重放修复后，真实控制链的light入口又报light_task_contract_invalid：批准摘要包含
Task文档路径和capsule revision，而Request绑定后的文档刷新会改变这些投影字段。
1 RED/7 PASS的独立回归复现。启动摘要改用已有contract_authority_payload语义边界，
不另建排除字段列表；需求、范围、验收、验证、Owner与Channel来源摘要变化仍使旧批准
失效，执行绑定及文档投影刷新不改变批准。各角色dispatch的独立版本校验不变。

升级影响：摘要算法发生变化，旧版本尚未执行的批准可能被判stale，应重新预览/批准；
不伪造旧摘要或修改历史ledger。当前本批没有活跃产品Run；历史已完成Run证据不重写。
已有长Run仍应在明确停止边界升级并先验证恢复绑定，不能据本批自动承诺跨版本兼容。

### Channel来源完整性异常受控返回

首次apply若canonical PRD sidecar被改写，来源加载抛出SidecarRefError，原入口未捕获，
CLI异常退出。补齐现有Workflow Request输入边界的窄异常处理，返回422及
workflow_input_invalid，记录受控失败且不产生workflow.invoke.requested。损坏和缺失
来源分别有回归，不将此异常吞成空输入或成功。最终启动入口闭包47项通过。

结构哨兵首轮50通过/1失败：新增独立异常分支使原996行模块达到1007行。
将两类相邻输入异常合并到同一受控失败路径，保留各自状态码语义；模块998行，
不新增结构债豁免。真实路由探针已经从先前候选快照冻结启动，此合并未热改探针；
它不执行Workflow apply，路由所用Agent/skill保持与最终实现一致。

定向验证：恢复15文件166 passed；Agent方法112 passed/1 skipped；启动入口47 passed；
工具映射/身份/合同/Request/light共101 passed/1 skipped（可选GitLab配置不存在）；
三流mock10 passed。各组有交集，不能累加为独立测试总数。首次入口失败与修复后
日志均保留。dev-verify自动层跳过升级为全仓的deterministic集合，使用上述影响闭包
替代；显式保留这一限制，不声称全仓回归或整个184分支merge-ready。

### 最终验证与真实Codex样例

| 层级 | 结果 | 证据文件 |
| --- | --- | --- |
| 恢复闭包 | 166 passed | recovery-closure.log |
| Agent方法与调用者 | 112 passed / 1 skipped（可选外部技能样例） | agent-method-closure.log |
| 映射、身份、合同、Request与light | 101 passed / 1 skipped（可选GitLab清单缺失） | final-tooling-contract.log |
| 启动入口和摘要 | 47 passed | entry-fixed-closure.log |
| 最终输入异常合并后的调用者 | 28 passed | input-boundary-final.log |
| 最终哨兵 | 51 passed | sentinel-final.log |
| 三流mock | 10 passed | flow-smoke.log |
| 自动层 | 串行10 passed；Web typecheck与unit passed；广域deterministic显式未跑 | automatic-receipt.json |
| 整个184分支验证规划 | errors=[]，813项测试文件被选中；不表示已跑 | final-branch-plan.json |
| 真实Codex路由建议 | 4/4样例通过 | live-routing/result.json |
| 真实建议到实际受控入口 | Kanban与Channel均到light Task Map，批准重放不新增invoke | real-proposal-admission-resumed.json |

四个真实模型样例分别为：明确的两文件修复推荐light；需求不明返回澄清Plan；
跨账户/订单/支付及迁移任务推荐标准PRD路线；Channel已确认PRD保留full_product并
推荐light。原始输入、回复、解析、usage和4个native session transcript均留存。
它们是样例验证，不能作为模型所有输入均正确的统计保证；native multi-agent显式禁用。

两份真实light建议随后使用未修改的模型payload建单、预览、提案、实际CLI精确批准与
重复批准，再由Orchestrator和dry-run transport产生单writer Task Map，保留范围/验收。
初版探针误以为两Run可同时准入：第二个被默认单Run容量正确排队。受控取消第一个
已达测量终点的Run后，队列按原批准继续，Channel full_product与单次invoke断言通过。
这一失败属测试前提错误，不改运行容量，不覆盖原失败日志。两测试Run最终均受控取消，
没有真实writer/Verify/Judge/Ship；此前完整真实产品交付证据仍按原版本单独引用。

候选基线5c2e8266加本批overlay冻结，source manifest见live-source-provenance.json。
最终异常分支合并与冻结快照的一处差异已逐文件标明；模型路由所用Agent/skill无变化，
后续真实建议准入使用最终候选，其他2485个源码/测试/技能文件一致。
本批未新跑浏览器、全pytest或覆盖率；未将逻辑read_only当作OS隔离证明。

已发simulation.done（仅探针结束，不伪造产品完成），两个Run均cancelled，
无测试tmux/Web启动，无遗留进程。临时状态归档逐文件SHA256校验后清理，
见cleanup-receipt.json；凭据未复制。
