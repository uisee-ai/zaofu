# Reader 恢复投影事件索引

现场LS-v1的Supervisor构建对41条Reader失败分别执行恢复判定，反复扫描约15.9万条事件。保留完整历史和判定规则，在同一 `workflow_resume.build_workflow_resume_checkpoints` 调用内复用 `ReaderReplanIndex`：按类型保留原位置，按ID定位原事件，延迟建立同一快照的Task失败因果作用域。

`plan_reader_stage_replan` 仍负责原有Run、阶段、Task/失败operation、成功重置、cap、gap plan、候选替代和授权代次判定。索引只替代部分查找，不跳过失败、不删除历史、不提供新的状态或权限来源。单独调用仍可自行建立索引；投影返回后不保留它。不同事件列表或追加事件后不能复用旧索引，下一次投影重新建立，事件对象在一次投影期间按只读快照消费。

固定158708事件前缀（末事件 `evt-4eec8127d1f2`）回放，3个恢复checkpoint内容完全一致；该Reader投影未加cProfile的单次测量由14.12秒降至7.60秒。数据是本机该前缀的观察值，不是延迟上限；候选替代、自动化、其他投影及整个Watcher周期仍有独立成本。

验证包括索引与单次决策一致、新快照成功生效、跨Task cap隔离，以及真实workflow_resume入口。任务记录：[Reader恢复投影索引](../../tasks/2026-11-08-1634-reader-replan-index.md)。本修改不证明LS真实业务E2E通过。
