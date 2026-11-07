# 已审阅集成失败的有界 Impl 恢复

> 状态: active

用户已授权持续监工、修复卡点并从断点继续。媒体任务的独立 Verify 已通过，但组合候选
rolling smoke 失败：固定100次紧循环耗尽时合法 ingest 尚在1秒轮转等待；保留原失败后
按真实时间续跑3.899秒即完成。不能重写旧通过、跳过集成或无限重试同一提交。

现有 `recover task-integration` 仅支持环境修好后的同提交重试；本项增加明确选择的
`--rework-impl --approval-ref`，只接受当前、已验证、无活动继任者的 blocked integration。
沿已有 task.rework.requested → projection → Impl/Verify 通路，授权仅覆盖下一代。

- 在 CLI 预演/应用恢复 -> verify: 原事件字节保留、幂等请求、调度下一代Impl；默认集成重试不变。
- 验证当前合同代次、失败来源、Impl/Verify同头通过、无活动attempt/继任操作 -> verify: 反例拒绝且不写事件。
- 超出常规返工次数时绑定明确审批引用 -> verify: 只放行指定下一代，不能重放到后续代次。
- 修复部署后恢复媒体夹具兼容性 -> verify: 原失败保留，真实流程与完整断言重跑、独立Verify和集成重新准入。

证据：evt-908ada24c515 Verify passed；evt-c78ce1754c45 integration needs_review；
`/tmp/zf-media-integration-20261107/probe.log` 保留原断言失败及追加时间观测。
运行修复与现场产品任务完成分开记录，不以恢复请求成功代替验收。

实现验证：新增10项先RED（旧CLI不支持恢复模式）；修正测试中的requested/started区分后，
聚焦闭包92通过。`dev-verify.py plan/run --base HEAD`覆盖本次差异及直接调用者，
自动层333通过/7跳过、sentinel50通过/1跳过、flow mock E2E10通过。
这是当前修复的影响闭包，未运行全量pytest，也不代表媒体产品集成已经通过。

现场预演发现结果adapter的canonical结果事件origin为空；新校验误要求kernel来源而安全拒绝。
用真实事件形态补RED后，改为核对已准入事件→control_result_ref→结果来源的身份/同头链，
不依赖origin字符串。完整333/7+50/1+10层再次通过，主checkout新增12项通过；
当前现场预演明确仅授权Media g3→g4。首次精确部署检查发现主checkout已有blocked-result replay，
因此未覆盖文件，改以唯一hunk保留既有修改。原预演拒绝无canonical写入。
