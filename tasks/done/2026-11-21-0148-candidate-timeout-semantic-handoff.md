# Candidate超时保留全部失败与未知结果

> 状态: done — 4c67f76e fix: retain mixed candidate outcomes across reader timeouts

用户授权隔离8×8发现问题、修复、记录及恢复。本项不合dev、不push。

真实evt-def7290dea52仅列lane2超时，漏掉此前7个失败Reader；OA预检报candidate aggregate failed members differ。lane2没有产品结果，不能伪造abstention或通过；lane0已准入median反例也不能被infra汇总消除。

- 原生超时汇总保留历史失败 -> verify: 正式timeout入口输出完整failed_children、原finding与超时finding，mixed分类而非全infra。
- 旧账本恢复 -> verify: 严格校验同fanout/Run/child/role/transport→call绑定及operation失败因果，不改旧事件；未知成员单列且不生成产品结论。
- OA输入与apply -> verify: 7个已有结果及精确未知成员可交接；缺身份、错误digest、结果/目标变化拒绝；无业务通过或全量Task重开。
- 验证与恢复 -> verify: 聚焦、影响闭包、sentinel、mock通过；真实只读预检及受控OA投递分别记录，实际语义结果另计。

验证：初始3 failed/8 passed复现真实交接及timeout producer问题；最终97项聚焦通过。相对已验证38977756的自动影响闭包3602 passed/7 skipped；sentinel53 passed、flow mock10 passed；全分支dev验证计划已保存，未跑全pytest/coverage。真实只读预检成功绑定7 admitted+1 unknown，见candidate-timeout-recovery-prepared.json；尚未投递OA、不宣称端到端修复完成。

范围核对：failed_task_ids中的AVERAGE来自browser AC归属，不是新average算术反例。保留median原始反例及browser执行缺口给OA，不能直接把二者都当源码返工。

独立研究会话复核发现expected_children闭合与旧aggregate后新carrier两个P1；追加RED为8 failed/14 passed（其中completed事实字段为新增验收），已补预期名单、role/stage、全部终态、后续重派/聚合重开拒绝及apply复核。补齐request/role/stage跨层一致性；合法completed sibling仅记录原完成事件事实，不伪造verdict。

另复现旧显式infra会绕过已保留语义结论的分类消费：2 failed/22 passed；严格限定kernel timeout+已准入negative sibling，完整Candidate binding有效才读作mixed，损坏为unknown阻止纯infra host后继，不重写历史。最终相关调用链聚焦139 passed/2 deselected；真实只读preflight仍为7 admitted+1 unknown，历史infra/有效mixed均已核对。完整最终闭包运行中。

最终闭包：3705 passed/7 skipped，sentinel53 passed，flow mock10 passed。相关聚焦139 passed/2 deselected。独立研究会话快速复核确认F1/F2及真实stage/role绑定修正，无新增阻塞项；其仅静态审核和纯内存探针，未冒充真实E2E。真实OA准入/后继效果待部署，暂不归档done。

真实闭环（02:18 UTC）：隔离控制器载入4c67f76e，受控请求evt-fe1df9af0f53创建原生OA checkpoint evt-643b978ece5f；operation.started=evt-0733a4319cac，quiescent.exited=evt-c619af1b08e0。通过实际checkpoint_input_ref/digest读取确认7份原control refs及1个无verdict的unknown；恢复后业务Task重派0。证据保存在SOURCE reports/eight-qualification-20261120T122929Z/candidate-timeout-native-proof.json及candidate-timeout-recovery-applied.json。

本项只结算timeout交接/分类/当前性修复；median/browser语义、独立最终验收、Judge/Goal/交付继续观察，不以OA开始运行冒充产品成功。38977756 Reader stuck实际respawn待证，另项保留active。
