> 状态: active

# Plan 重合成绑定当前输入

本轮恢复中 `evt-54ae42f87776` 绑定新 Plan Package 却携带旧 Planner task_map。复用 stage retry 的引用规则到 candidate replan 重合成入口；不修改业务判定、审批要求或自动重试上限。

- candidate replan -> verify: 当前已接纳包及其 source event 的 map/index/ref/digest 一起绑定；显式空 index 清除旧值，缺失新摘要不继承旧摘要。
- stage retry -> verify: 原有两条回归保持通过；两个入口共享最小 helper。
- 未接纳草稿 -> verify: 不成为绑定来源；无历史source的兼容结果保持既有行为。

新增 public build_replan_resynth_event 回归先 2 failed / 2 passed，再与 stage/candidate/control recovery/race 测试共 130 passed。自动 sentinel 50 passed / 1 skipped，flow mock 10 passed；收据 `/tmp/zf-task-recovery-20261105/resynth-verification.json`。测试文件名首次两次查询错误未运行测试，已修正命令后取得上述有效结果。

操作纠正另记：首次重放给 previous_plan_candidate_refs 错传字符串，准入在 provider 执行前正确拒绝；更正为 ref/sha256 描述符。第二次受控请求 `evt-8539e0dc7f76` map 与当前包一致。该操作错误不是放松 schema 的理由。
