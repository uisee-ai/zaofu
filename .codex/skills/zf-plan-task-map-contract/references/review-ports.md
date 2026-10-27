# Plan Preview 来源与覆盖端口

此约定补充现有 Plan Package 阅读层，不新增 Task schema，也不让 Runtime 判断
业务覆盖是否充分。Planner 产出、Critic 核对，Preview 只读取本版明确绑定的端口。

1. `source_index.tasks[]` 保留每个任务的来源锚点；另以 `traceability[]` 从原始
   authority 独立枚举来源单元。每条有稳定 `source_id`（也支持 `trace_id`/`id`）、
   原文锚点和语义陈述。不能从已经写好的 coverage 反推全集，否则遗漏项永远不可见。
2. 显式提供逻辑名为 `coverage_ledger` 的 `plan_ports`，正文 `entries[]` 与来源
   使用相同 ID。逐条保留 disposition、原因、生产任务/AC、验证命令及责任阶段；
   未执行证据保持 NOT_RUN，未决定项保持 pending_decision。`covered` 表示计划映射，
   不表示实现或验收已通过。阅读层兼容两个端口明确的 `rows[]` 表示，但新产物优先
   使用上述字段，不能混用同名 ID 表达不同来源单元。
3. 通过现有 `{logical_name, ref, sha256}` 描述符绑定完整文件，并按当前路线将
   必需正文纳入 `required_plan_ports`。摘要、目录名、artifact_refs 或任意自定义
   路径字段不能替代绑定。作者不写 canonical state；交由 Kernel 搬移并校验摘要。
4. 页面和表单来自 `source_inventory.pages[]/forms[]`，保留来源 ID、标题和任务
   映射；`items[]` 中的输入文件数量不是页面数量。没有页面的产品据实留空。
5. 提交前以实际 Preview/API 解析入口核对来源总集、已映射集、缺失项和未决项。
   缺失来源 ID 不得因统计“行数一致”消失；损坏摘要必须显式失败。任务来源索引、
   完整需求覆盖和产品验收结果分别表述。

最小正反例：来源 A/B/C，账本 A=covered、B=pending_decision → 显示三条来源、
两条映射、一条缺失 C、一条未决 B；不能显示“全覆盖/验收通过”。只有任务摘录
而没有独立来源集时，应显示来源集不完整，不能从账本补造 A/B/C。

已接纳的旧 Plan 缺端口时，保留 incomplete 与具体原因。通过受控修订生成新版本，
重新 Critic/人工审批；不得改旧工件或摘要，也不从作者目录猜测未绑定的覆盖文件。
