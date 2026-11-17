# 合并 R16 资格与任务接力可靠性

> 状态: active

用户批准先归档R16，再在独立worktree集成task-handoff-reliability并验证后合入dev。
基线dev 0625d001，R16归档ec56e54b，接力分支f3203084。本批不push；主dev未提交资料原样保留。

- [x] I01 R16收尾 -> verify: 三Task及goal已完成、六故障链和0临时介入；持久收据及B01–B07归档ec56e54b。
- [x] I02 集成交接输出 -> verify: next_action/continuation共存；receipt/pipeline/input_recovery共存，原CLI/API消费者通过。
- [x] I03 统一现场解析 -> verify: 真实Task工作区、自定义根目录和原request/lease/envelope同时绑定；失效/冲突拒绝，历史兼容和终止现场保存不冒充执行权。
- [x] I04 验证与可合并交付 -> verify: 相对dev影响闭包、sentinel、mock和Docker业务浏览器通过或已有基线失败逐项对账；三项新增差异修正并定向复验。实际合入dev和用户现场保全由后续操作者步骤完成。

初始自动合并后2项恢复夹具失败；补齐真实身份的回归10项失败/18项保护通过，包含诊断文本差异，不视为10个独立缺陷。
统一解析后93项定向通过。影响闭包及基线对账已记录于实现文档；最终105项定向、Web构建、12项Docker业务浏览器、53项sentinel及10项mock通过。原dev的15项既有失败保持可见。

实现说明：../../docs/impl/task-handoff-reliability.md。历史R16资格不代替新集成代码真实Provider证明。
