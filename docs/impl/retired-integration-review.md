# 正式替代任务后的集成诊断退休

Run Manager 的 `integration_review_sources` 应保留确切、仍有效的集成失败。任务改由新的显式后继承担时，原 operation 可以保留终结性的 blocked 历史；诊断投影必须识别旧任务已退休，不能因为它仍保留旧代次上下文而重复分诊。

复用 Task Pipeline 终结收敛已有的 `_authorized_supersession_event`：旧代次之后的 kernel `task.superseded`、`writer_task_map_adoption` 来源、cancelled 状态、同 Run、明确新 map 引用，以及其后的同 Run 新 map generation 准入必须同时成立。无关任务进度、未准入替代和跨 Run 事件不消除旧失败。来源退出 current 后仍在 covered 中，避免退回通用 `workflow.operation.blocked` 重复分诊。

入口由 `run_manager._pending_semantic_event_actions` 调用，真实 `run_manager_tick` 回归验证不会把旧集成故障重新发送给resident。历史事件、TaskStore 和 operation 状态均不在此只读筛选中改写。本修复不代替新后继的实现、Verify 或集成。

实施：[任务](../../tasks/2026-11-08-1735-retired-integration-review.md)。
