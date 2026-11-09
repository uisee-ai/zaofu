> 状态: done — d8f23a91 fix: preserve task scoped input continuity across recovery plans

# 恢复增量按目标输入分区证明连续性

持续监工授权。LS-v1 FMS24恢复报告已准入，但evt-ab4df9604104拒绝计划增量，原因task_execution_input摘要变化。dev354c70b8实际旧dad6ed42与新57958b13包：shared和FMS-24分区逐项完全一致，其他任务分区更新改变整文件摘要。FMS24执行绑定旧包且通过显式替代链继承FMS-24；新任务不得丢掉这一输入或其他已完成任务。

- [x] 复现实际桥接和继承入口误拒绝 -> verify: 固定包/事件/输入副本及最小回归先RED。
- [x] 只放行严格结构化、目标分区与shared不变的跨包输入 -> verify: 无关分区变化/重排允许；目标/shared/缺失/重复/篡改/跨run/祖先链异常拒绝；中间变化后还原也拒绝。
- [x] 原替代链和必读范围保持 -> verify: 实际gap桥接、候选输入预检与worker输入生成均成功，legacy仍完整比较，来源ref/hash真实，不扩大读取范围。
- [x] 闭包验证提交合并重载 -> verify: focused、sentinel、mock；通过受控重试原gap报告恢复，无再次reader重算；记录实际派发。

仅改机械输入连续性，不判断语义相似，不放宽Owner/验收，不直接写canonical状态。不push。

实际旧版本桥接误拒绝与新版本预检已记录（176658事件视图）；当前shared+原FMS-24分区完整保留，只改FMS25依赖。初轮4个真实RED（另1个无消费声明的legacy测试期望错误已修正）；216影响闭包、53sentinel、6恢复mock通过。新增包准入用例先因toy fixture未保留隐式Goal失败，补齐同版Goal后通过，不修改生产门禁。完整测试因自动计划仅按203文件数量建议全量而显式跳过，使用领域闭包；不声明full。

最终62项定向+合后62项、53哨兵通过。实现d8f23a91→dev7b53d2f0；Watcher3818097/Web3819712加载确认。受控stage-retrigger复用evt-c094bb14a878报告，evt-77944e472154成功生成并准入f7c8c832输入包，计划evt-abe393d369ce按原授权批准。00:39:30 UTC实际派发evt-6f792e89bb74：FMS-24-GAP-CONTINUE-02、ta-21c8d41e3fc9978e427e、pane%1754。合成base649bd4bb同时包含partial79407ae和Candidatebee44fe6；当前端口781bb76f精确读取shared及FMS24原分区，其他55绑定及53完成保持。Q06仍未批准；此处只关闭机械恢复阻塞。未push。
