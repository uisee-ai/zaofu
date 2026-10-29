> 状态: active

# 已接纳扫描报告完整交接与失败聚合重建

监工授权：修复不可自愈的问题并断点恢复。现场 evt-fe5141944813 已接纳扫描，但聚合仅取 verification-result.v1，丢失源报告中的两项 gap_tasks，误走 stage replan cap。前序 7c38380 禁止读取未接纳 scratch 是正确的安全边界，但只覆盖了精简 control result，漏测显式验证结果附带完整语义报告的链路。

- 从不可变已接纳 envelope 绑定的原始 ledger occurrence 恢复 report，与类型化控制结果合并，后者优先 -> verify: 旧草稿和后续未接纳事件均不能替换 gap；真实 Orchestrator 接纳、聚合生成完整 gap。
- 扩展原有 fanout-aggregate-rebuild 支持 flow.discovery.failed -> verify: 匹配既有 discovery aggregate 配置、当前 fanout、已终结 children，重建仍 failed、保留 gap、无重复扫描，幂等。
- 修复部署后受控重建真实聚合 -> verify: 真实两项既有 gap 通过机械验证；不修改 canonical Task 或伪造产品通过。

验证：两轮 RED（gap 丢失；受控重建拒绝 failed discovery）后修复；完整 Orchestrator 显式/legacy typed reader 接纳→聚合→controlled action→重建幂等、无 worker 重派。定向调用链及 stale-contract mock E2E 411 passed；sentinel 50 passed, 1 skipped；Flow smoke 10 passed。dev-verify --base dev 对本补丁及 inherited stack 的大模块闭包保守扩成全套，按仓库禁止普通补丁因文件数触发全量套件的规则，采用上述显式调用链闭包，未声称全量验证。现场只读预览恢复两个原有 gap，schema 校验通过；部署恢复待核实。
