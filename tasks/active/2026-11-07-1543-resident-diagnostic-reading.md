# R46：常驻诊断提示按请求读取并准确说明建议消费

> 状态: active

LS-v1 壳层失败诊断于15:34:26送达 Run Manager；16,266字节的定界上下文已足够定位请求，但 agent 首轮叠加全局投影输出超过25,316行。实际 resident briefing 仍要求 tail 最近120条事件并整读三个投影，还将案卷投影描述为比事件流可靠，称建议处于不自动执行的影子档。15:37建议已由真实 runtime 于15:39消费并派发OA，后两项描述与实现不符。

## 范围和验收

- 诊断按绑定请求、身份和证据引用读取 -> verify: 初始/重绑定/reprompt共用的briefing先定位本案，完整消费相关正文；不要求整读全局投影或原始事件尾部，不削弱冻结必读要求。
- 准确说明职责 -> verify: 投影仅供索引并核对权威Store/ledger/sidecar，建议由runtime按策略与准入消费，不承诺自动批准或直接写状态。
- 方法归入context-hygiene，保持受控CLI和角色边界 -> verify: 现有briefing边界、含空格引号路径的可执行CLI、resident启动/保留/reprompt、诊断路由测试；sentinel和mock E2E。
- 合并部署后核验未来briefing实际使用新内容，保留当前两个Verify/OA载体 -> verify: runtime加载回执与刷新后的briefing；不打断已有诊断。

调用地图：build_resident_run_manager_briefing → spawn/rebind/reprompt；run_manager._refresh_resident_briefing在再次提示前重建；_consume_agent_recommendations经受控准入消费。只修改提示与通用方法，不新建检索器、状态机或权限。

验证：5文件57 passed、1 skipped（外部示例配置缺失的既有sensor），199 recovery mock E2E 1 passed，sentinel53 passed，技能元数据通过。dev-verify planner因184个影响文件选择全仓；本次只改提示方法，依AGENTS按域闭包规则执行上述明确闭包，不声称全仓验证。最后同步rebind文字后重跑resident/CLI闭包与sentinel。真实链路RM建议evt-b48dd91df13e→消费evt-4bae659b8744→OA operation ...4b6db7c5aa19确认建议非影子；部署验证待合并后记录。
