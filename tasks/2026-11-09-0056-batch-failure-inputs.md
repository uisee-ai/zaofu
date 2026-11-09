> 状态: active

# 历史失败批次恢复判断复用完整依赖事件

持续监工授权：实际 LS-v1 177080 事件只读 profile 中，36 个失败批次重复全账本扫描；batch 投影 2.713 秒，两个失败归属/候选定位 helper 合计约 2.566 秒。完整证据：LS-v1 `docs/records/2026-11-09-workflow-batch-profile/`。本项不解决 Q05/Q06，不修改业务验收、预算或重试策略。

- [x] 同次 batch 投影集中选取四个失败判断 helper 的全部事件依赖 -> verify: 保留任意配置 failure_event、fanout.started、任意类型 trigger 引用、candidate 冻结/包装链、child.failed，原始次序与对象不变。
- [x] 接入实际 workflow resume 投影 -> verify: 加入大量无关事件时判断输出相同，helper 不再重复收到完整账本；新增事件下一调用立即生效，不跨调用缓存。
- [x] 保持恢复语义 -> verify: reader/discovery 与 candidate runtime/业务失败分流、跨 fanout、冻结链、恢复后撤销及 supersession 测试通过；完整现场输入新旧输出精确一致。
- [ ] 验证、合入、受控加载、记录 -> verify: 明确影响闭包、哨兵、恢复 mock E2E、合后检查；worker 在途身份保持；整体 CPU 另行采样，不把局部加速当整体关门。

OA/RM 边界：当前恢复输出未错但扫描成本随历史失败数放大；监工先测量再修复确定性投影，不把 operator 源码修复记为 agent 自动修复。

新回归初次误用main editable导入被仓库护栏拒绝，补PYTHONPATH后7 RED；实现后一个fixture漏verification-result.v1被正常拒识，更正fixture未改准入。最终25首轮、352影响闭包、53哨兵、4恢复mock通过。dev-verify按96反向测试文件数量建议full，遵守AGENTS领域闭包规则显式跳过自动deterministic/serial，并记录receipt；不宣称全量。

冻结177182事件，专用失败输入239条，36批次的108个helper判断及完整batch输出精确一致；无profiler旧1.300653s、新0.094607s。事件与状态均只读，完整恢复与supersession路径保持原全账本；整体Watcher CPU仍待观察。
