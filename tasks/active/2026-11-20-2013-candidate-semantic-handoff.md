# 候选混合失败的宿主回交与 OA 分诊接续

> 状态: active

用户授权：8×8真实验证发现问题后修复、评估并断点恢复。真实窗口于20:12 UTC关闭，随后停止专属控制器。19条冻结宿主命令均成功，但候选汇总evt-91ab2ca06245为mixed；宿主回交错误请求infra-only恢复。RM语义请求evt-590110d275eb缺Candidate Reader身份，OA以target_attempt_missing拒绝；RM后置验证还等待旧事件名。保留所有原始审核结果，不判定数值范围争议或伪造产品通过。

- 完整宿主收据遇到mixed/quality/未知失败 -> verify: 不请求纯infra恢复、不重复失败动作；纯infra沿原受控回交。
- 自动Candidate分诊绑定当前已接纳Reader汇总 -> verify: 实际RM请求经OA入口建立checkpoint，输入保留各路结果及当前候选/计划/合同；无需虚构anchor TaskAttempt。
- 多Reader及过期反例 -> verify: 所有失败证据与作用域保留；非可信/异Run/错结果/过期候选/缺成员拒绝，不扩大权限或直接重跑业务。
- 当前语义路由匹配后置事件验证 -> verify: semantic_failure与legacy配置都等待实际发出的入口事件；请求发出不代表OA决策已完成。
- 宿主证据方法说明 -> verify: 区分Impl缓存复用与预授权宿主独立执行事实，准确记录execution_origin/原执行身份；不因reusable=false抹去宿主事实，也不将其当业务verdict。
- 聚焦、影响闭包、哨兵、相关mock后提交，受控接续 -> verify: 原mixed失败/19收据/16已完成任务不改；实际OA准入与后续决定另行记录。

真实账本追加复现：受控stage-retrigger保持operator来源，已由Kernel fanout.started接纳，但candidate_plan_scope仅查询kernel来源candidate.ready，错误选择旧候选事件并以snapshot candidate_event_id mismatch拒绝。只接纳原冻结身份不变、严格前向redrive血缘且已由同Run Kernel fanout准入的新候选；未准入/来源不可信/异Run/目标改变仍拒绝。

验证：有效初始回归5 failed/1 passed；修复后聚焦108 passed；相对aa4a9611自动影响闭包6072 passed/8 skipped（284.97s），sentinel53 passed，flow mock10 passed。真实账本只读回放8/8 Reader及19/19宿主命令均绑定候选09094c2d。尚待实际OA接续，保持active，不宣称产品最终或无监工验收通过。
