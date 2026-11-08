# 计划准入事故恢复后的 Reader 收尾

对应 [199](../design/199-worker-failure-recovery-and-role-handoff.md) 的恢复责任闭环。

ls-v1 监工发现：FMS03 的新任务已真实派发，因先前 PlanPackage 准入错误排队的
Discovery 仍启动。原收尾检查只证明业务失败事件到替换任务的因果链；准入事故的
`rework_of` 指向原 `task_map.ready`，没有同一形状的业务替换链，因此没有退出。

`plan_admission_reader_closeout.recovered_plan_admission` 从既有事件证明相同事故
已经恢复：原 kernel ready 的 ref/digest、相同 incident 与 trigger 的 kernel
`task_map.admitted`、精确引用该 admission 的同 Run WRC generation，以及该
generation/ref/digest 对应的有效 PlanPackage。只有旧取消 trigger 的包时仍拒绝；
后续由新 trigger 正式重新准入的同包可以通过。不改变包的取消隔离规则。

该证明接入共享 `reader_failure_supersession`，实际 fanout 生命周期沿用既有
受控取消路径：首次派发前可停止排队 Reader，运行中的旧 Reader 关闭 operation
和对应 transport，重启重放保持幂等。取消原因是
`superseded_by_recovered_plan_admission`。不写产品通过、不回收其他业务 worker，
所有原失败、取消、重准入事件保留。

验证入口：`tests/test_plan_admission_reader_closeout.py` 覆盖实际 runtime tick、
首次派发前取消、重启，以及缺 WRC、仅有取消包、异事故、异 Run、异 trigger、
摘要/包不符和不可信事件的反例；原业务替换检查继续由
`tests/test_reader_replacement_closeout.py` 与 `tests/test_reader_failure_supersession.py`
覆盖。这项修复不代表所有 `task_map.amend.failed` 已具备自动补齐语义输入的能力。
