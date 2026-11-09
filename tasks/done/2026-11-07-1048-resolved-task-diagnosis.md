# 退役已完成或被替代任务的历史诊断

> 状态: done — `bec58d6d fix: retire task diagnostics after canonical terminal events`

## 证据与原因

LS-v1 10:41 Run Manager projection 的8项pending诊断包含已完成FMS03/DETAIL/GATEWAY/FMS19及已被替代FMS04/FMS13/TODO。10:47只读当前HEAD回放仍产生10项，其中9项历史失败已经由task.done或task.superseded收束，新的Q04输入缺陷排在末尾。`_event_superseded_by_later_progress`没有消费这两类终结事实，通用scope又没有精确Task匹配，既留下旧诊断，也可能被同Run其他进展误清除。

## 限定实现

- 仅为task.attempt.failed与task.pipeline.verify.failed增加精确、事件顺序敏感的Task终结证据归约；同Run同Task的后续kernel task.done或正式task.superseded关闭旧诊断。
- 其他Task/Run、worker自报、未知Run、较早终结、仅实施完成/重试或全局计划进展均不能关闭该任务失败；终结之后的新失败继续待诊断。
- 保持事件账本与canonical store不变；语义问题仍由OA判断，RM只归约机械终结事实。
- 新增小模块，不继续扩大run_manager.py中的复杂逻辑。接入现有实际RM投影调用链。

## 验收

1. 当前实现复现队列残留 -> verify: 新回归先红。
2. 精确关闭与所有负例 -> verify: focused tests + RM真实projection调用测试。
3. 恢复/Store/Event契约闭包 -> verify: dev-verify plan/run按有界影响闭包、premerge sentinel、mock flow E2E；不声称全量pytest通过。
4. 合并dev并受控重载 -> verify: 真实只读队列对比，历史诊断退役、新Q04问题或已交接工作不会误报完成，worker PIDs保留。

关联：[199设计](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

## 实现与验证

`task_failure_retirement.py`接入RM既有后续进展判断，Task终结路径要求精确Run/Task和kernel来源；非Task故障沿用原归约。

- 新回归初始7 failed/15 passed；增加2项同Run无关进展负例后，新回归与原RM共189 passed。
- 22文件有界影响闭包415 passed，含真实RM projection、诊断/triage/router/attempt、EventLog和199 mock E2E。
- dev-verify保守扩展347文件到全套；本改动限定两类Task失败，明确用上述有界闭包替代deterministic全套，`run --skip-tier deterministic`保留serial/contract/mock_e2e。serial 10、sentinel 53、flow smoke 10通过。未运行全量pytest或真实Provider测试。
- 真实只读账本回放退役17个已终结的历史失败事件，待诊断只剩当前Q04协议输入故障；没有修改LS账本或TaskStore。旧队列最多10项，原含9个已解决/替代任务诊断。

## 部署验证

dev e1dd304e；合并后189 focused+53 sentinel通过。10:53仅控制面重载，watcher2692725→2737438，runtime-load 79291b296c00460f845be6b39b44bc87.json记录新代码；重载瞬间所有原pane/PID保留，随后Q04 Recovery Reader正常settled后归档（回执见LS记录），其他在途worker保留。10:55真实RM投影原8条已终结任务诊断全部消失，剩FMS02与更早GATEWAY01两条具有不同历史身份的诊断；本修复不把无精确终结证据的历史行强行标为解决，也不宣布当前产品阻塞已解除。新Q04和附件问题沿现有恢复/规划路径继续。
