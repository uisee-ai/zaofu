# 已准入接替任务后的恢复义务关闭

LS-v1 真实链路出现：有界恢复已经将失败任务替换成新生产者和消费者，WRC 准入新代际，Run Manager 仍对旧失败发起全局 discovery。reader 兜底在创建 fanout 后取消，但不应靠这条后置防线过滤每一次旧恢复。

真实 `task_map.ready` 把接替关系放在 `rework_summary.gap_tasks[*].supersedes_task_ids`。旧 immediate replan 只识别顶层字段，且 active/pending 与 reader 使用不同完成条件。

现在 `admitted_task_replacement` 为 Run Manager 和 reader 共享同一机械证明：原失败、同 run 的 Kernel ready、ready 对原失败的 rework_of、Kernel WRC admission 对 ready 的引用，以及全部后继已准入、旧任务不在准入集合。单纯较新的计划、取消状态或外部事件不足以关闭义务。

Run Manager 在派发即时重规划前和计算 active recovery 集合时检查这一证据。迟到的 triage 也按其原失败发生点核对，不会因分诊晚于接替准入而重新开启旧恢复。reader 现有后置防线保留。

验证：`tests/test_run_manager_admitted_replacement.py` 覆盖实际 payload 形状、真假接替和 Run Manager projection/tick 入口；`tests/test_reader_failure_supersession.py` 验证共享证明保留既有 reader 边界。任务记录：`tasks/done/2026-11-08-0924-admitted-recovery-retirement.md`。
