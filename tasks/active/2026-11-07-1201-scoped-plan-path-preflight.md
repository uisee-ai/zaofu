# 按实际派发集合检查计划写入冲突

> 状态: active — 用户授权修复监工中确认的Q04准入阻塞。

Q04只重排五任务，预检却对全图调用validate_writer_task_items，误报两项已完成shell前置的历史顺序写入冲突。实际load_writer_task_map先select再validate，二者不一致。

1. 重叠历史任务不在本次选择中 -> verify: 同输入真实loader接受而预检先红。
2. 预检按已有select_writer_task_items选择后检查写入范围 -> verify: 全量/选中两项冲突继续拒绝，未知任务选择继续拒绝；全图结构/目标/矩阵门保留。
3. 合并部署、Q04固定样本回放 -> verify: 只移除不适用的写入冲突，剩余语义缺口不豁免。

关联：[199实现](../../docs/impl/199-worker-recovery-implementation.md)。

验证：实际loader对所选单任务接受、对全量重叠拒绝；基线2红/2绿。5文件输入预检/提交/loader/失败协议闭包91通过；补直接提交/synth/角色/大小预检调用闭包25通过；sentinel53、flow mock10。真实固定Q04候选4错误降为3，只移除不在本次scope的writer路径误报，GOAL04/命令ID及AC映射仍拒绝。无全量/真实Provider E2E。
