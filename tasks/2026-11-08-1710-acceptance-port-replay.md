# Product Acceptance 计划包身份重绑的重放一致性

> 状态: active

持续监工授权修复。FMS03 Planner已正式提交evt-a8ecebe5bb0d，task_map.ready evt-54a60ef90df1首次包准入成功，但Writer重复准入因explicit product_acceptance_spec与系统重绑后的已准入值不同而拒绝，evt-52eedc48a598使审批失效。

- 首次准入和重放统一五项系统身份字段绑定 -> verify: 实际admit函数连续两次准入复用同包、无重复事件。
- 语义字段仍逐值比较 -> verify: assertion/mandatory/owner/providerqualification改变仍拒绝。
- 当前真实包原输入与绑定后正文对账 -> verify: 仅schema/Run/Flow/planrevision/generation字段差异，语义摘要相等。
- 合入并部署后受控stage-retrigger复用已完成Planner产物 -> verify: 新ready正常审批/dispatch，旧cancelled trigger保持退休；无全量重规划。

验证：新增实际准入入口先2红4绿，修复后6通过；相关入口/产品验收/Plan包/Doc152与199 mock E2E共47通过。276文件影响闭包3894通过99.72s；serial10通过；哨兵53通过。dev-verify映射broad会默认全pytest，本次依仓库规则显式运行选出的276文件闭包，不声明全量/真实provider测试。

真实端口只读对账：仅plan_revision和task_map_generation两字段不同；统一绑定后的完整正文摘要d8338de952a2f228d2740ed8cb409582e0d38b3e47badbce1d4369b59a944534与已准入端口完全相等。并非放宽语义比较。

实现说明：[身份重绑重放](../docs/impl/product-acceptance-port-replay.md)。待部署及受控ready重发。不声明产品E2E完成。
