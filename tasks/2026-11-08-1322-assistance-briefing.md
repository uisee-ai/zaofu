# 只读 Worker 求助的有界 OA 简报

> 状态: done — `1624e6a0` fix: bound worker assistance briefings to advisory scope

用户已授权持续监工、修复问题并记录。现场 FMS13 求助 evt-d104ce6777c8，经 RM 27.646 秒转交，等待 OA 原检查点结束后于 13:17:08 派发。实际简报 407 行，包含全局 memory、Kanban、健康及旧式 kanban assign／改合同／派发指令，与只读 proposal_only 合同冲突；现有派发测试替换了真实简报 builder，未覆盖此问题。

依据：[199](../docs/design/199-worker-failure-recovery-and-role-handoff.md) 的 C01 按角色裁剪简报与 B05 固定引用恢复原则。

范围只含运行中 worker_guidance OA 简报及该派发链路。保留请求、身份、不可变完整引用、状态目录和结束协议；不更改计时、授权、结果准入或业务合同，不覆盖已有不可变简报。

- 真实请求经过 reactor 派发 -> verify: 不加载全局状态或旧式写入指令，完整保留请求关联与证据路由，重复 tick 不二次发送。
- 普通 OA 唤醒与其他 triage -> verify: 既有行为测试保持通过。
- 修复合并与受控加载 -> verify: 哨兵、影响闭包及确定性 mock E2E；保留在途 worker，真实后续求助自然加载另记，不把重建简报冒充真实派发。

验证：首次真实派发/全局加载回归 2 RED、7 PASS；修复后定向36 PASS。首次191文件回归2887 PASS、7 SKIP；结构哨兵发现原briefing超过1000行，已将主体移入worker_assistance，原briefing仅4行路由。最终239文件3474 PASS、7 SKIP；文档指令8 PASS；dev-verify自动serial10、哨兵53、flow mock10全部通过。选择器因文件数超过80建议全pytest，按仓库规则执行明确影响闭包而非全量；不是完整suite或真实Provider E2E。现场旧请求只读重新渲染完整保留Trigger和ref，不改原简报；新真实派发和排队耗时另记。

实现说明：[worker-assistance-bounded-briefing](../docs/impl/worker-assistance-bounded-briefing.md)。
