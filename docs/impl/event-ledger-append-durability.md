# EventLog 追加耐久性（ZF-TEST-111）

本次选择性整合仅移植 v7 台账 ZF-TEST-111 的活动账本同步修复，
不改变 feature 的 `/issue-merge`、Triage、前端或执行项目语义。

`EventLog.append()` 在原有锁内写入完整事件行后先 flush、再 fsync，
成功后才允许 index 更新和 `EventWriter` projector 执行。
EIO/ENOSPC 直接传播；失败时字节可能已经可见，不截断账本或补写事件。

调用方回归见 `tests/test_event_log_durability.py`，覆盖大小 UTF-8 事件、
锁内同步顺序、正式 EventWriter 调用方及失败传播。feature 修改前该三例均失败。
本次不把 v7 的历史 VM 通过记录当作 feature 的新 VM 证明。

边界：仅保证对当前活动文件发出同步请求；首次创建的目录项、轮转事务、
多 Store/sidecar 原子性和物理断电不在此修复保证内。
逐事件同步增加 I/O 成本，聚焦正确性回归不代表性能验收。
