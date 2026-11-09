# R42：一对一批量替换保留原依赖图

> 状态: done — b10eed92 fix: preserve dependencies in one-to-one task replacements

LS-v1 的15项证据范围修订只读预检发现：现有 gap 入边继承对一对一批量替换也套用单任务拆分逻辑，既会遗漏旧节点之间的继承边，又会把下游外部依赖加到上游根任务。没有提交有缺陷的提案；2项测试已复现。

## 验收

- 一对一后继逐节点继承原入边并映射旧ID -> verify: 实际 build_gap_task_map_amend 输出保持 BASE→A→B 且 EXTRA 只约束 B，下游消费 B2。
- 保持显式前置及非变异输入 -> verify: 原提案不变，图通过现有验证，额外依赖不向上游移动。
- 保留单任务拆分行为 -> verify: 现有 module gap/runtime/recovery 回归与 sentinel、mock E2E。
- LS预检并执行 -> verify: 15项新旧业务字段和依赖逐项相等，仅增加已声明证据目录；受控准入/审批，40项总数及既有done/active保持。

验证：2项RED复现；补齐fixture的base_commit后9文件闭包109 passed（含两个mock E2E）、sentinel53 passed。真实15项proposal逐字段及依赖对账通过；受控应用结果见下文。

收口证据：dev67b7a17合并后39 passed及sentinel53；15项逐字段/依赖对账通过，审批evt-e822269fc606，15:15:31 WRC准入evt-cd08bfd99f01。真实30profile和canonical scope预检通过，计划40项保持。
