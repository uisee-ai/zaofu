> 状态: active

# 受控新代际保留操作员修复说明

持续监工中，需凭 human.escalate 开启新Plan代际并补上P16B替代关系。stage-replan-new-generation原仅回传旧失败诊断，丢弃操作员reason，导致有授权、有说明却不能让Planner消费。

- 新代际传递reason -> verify: controlled action产生的trigger.rework_feedback包含原失败与操作员说明、请求event引用。
- 保留授权、代际及幂等 -> verify: 无reason时兼容，重复请求不增加新trigger或重复说明，仍需合法stage-cap升级及approval_ref。
- 实际派发 -> verify: 新Planner受控上下文包含完整监工返工要求。

TDD先RED：guidance未出现；修复后 68 项定向测试通过，dev-verify closure 816 passed, 2 skipped；sentinel 50 passed, 1 skipped。
