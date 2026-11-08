# 已正式替代的任务退出集成诊断来源

> 状态: active

持续监工授权修复。FMS17 UI03 已在最新图准入并实际派发，旧 UI02 有 kernel task.superseded、TaskStore cancelled 和新 generation 准入证据。旧 integration operation 保留历史 blocked 是正确的，但 integration_review_sources 仍把 evt-6961dfc0234f 当当前诊断来源。不能靠清除历史事件或伪造旧operation成功收尾。

- 复用已有 Task Pipeline 正式替代证明过滤诊断来源 -> verify: 原失败保持但正常新代次替代后不再进入 RM 待诊断。
- 旧 blocked operation 不变更 -> verify: 无伪造通过/新结果/手动状态修改。
- 无关进度、跨Run、未准入或伪造替代不能掩盖真实失败 -> verify: 对应否定回归仍产生旧集成诊断。
- 接入真实 RM tick -> verify: 新后继缺少结果时仍运行，但已退休旧失败不再提示resident。
- 现场只读前后重放、合入部署 -> verify: 精确旧source从current集合移除，covered仍防止重复workflow.operation.blocked分诊，5个在途worker身份保持。

当前完整_pending_semantic_event_actions输出前10项未出现旧UI诊断，因此不声称它已造成新的实际重派或当前UI阻塞；本次修复的是已复现的错误来源筛选。实现说明链接至 [集成诊断退休](../docs/impl/retired-integration-review.md)。

验证：新增正式替代/否定边界回归先1失败15通过，修复后16通过（含实际RM tick mock）；63文件影响闭包1224通过7跳过，23.31秒，含199 mock E2E；串行10通过，哨兵53通过。冻结50条现场相关事件摘要5f0b0098c98808d36ea5382a5b01168aa15530648b972be2689ee53e5da16204，旧source由1到0，covered不变。未做全pytest或真实provider E2E。
