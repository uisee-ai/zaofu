# 有界执行与局部重规划

Task Map 定义任务合同；OA 的执行安排引用既有 Task ID，不创建另一套任务图或状态。
候选 Plan 的依赖/验收问题在原本的 Planner/Critic 回合收敛。批准后 `pre_impl`
只安排既定任务的执行，`semantic_failure` 对精确异常目标作增量决定。

## 先观察，再决定

优先读 checkpoint 的 `execution-context`，或用
`zf trace dispatch --run-id <run> --state-dir <configured-state>` 查询。
同时看 task_status、execution_status、pipeline_stage、实际 worker、最新 operation 的
status/verdict/attempt、blockers、就绪队列和容量。Task 的 in_progress 不证明 worker
仍在运行，settled 不证明通过；历史失败不能盖过最新有效结果。
读取相关任务合同和证据即可；原 PRD 与其他 task ports 保持按需读取，不把可读来源
全部变成必读。摘要缺失或过期时先补查，不从旧会话记忆猜测当前状态。

## 自主动作和升级边界

- 以本次冻结配置和 execution-context.autonomous_actions 为准；null 沿用原 checkpoint
  策略，空列表没有直接执行授权。shadow 意见不会改变执行。
- `delegate`：在 pre_impl 提交 adopt/run_plan；为既有任务选择兼容角色，必要时用
  delegation.dispatch_rank 表达较小值优先的偏好。偏好仅作用于已就绪任务，仍遵守
  公平性、容量与写入范围。graph.edges 不覆盖 Task Map.blocked_by。
- `rework`：对当前已结束的 Impl 指出具体缺陷、证据及预期修正；原合同不变。
  不重试活跃 attempt、不新增返工额度、不以“部分提交”宣布任务阻塞。
- `continue`：无需语义改动时返回继续；这不签发通过或释放尚未满足的依赖。
- 修改依赖、AC、接口约定、验收 owner、写入边界时，提交受影响范围的 return_to_plan，
  保留未变任务和既有证据。只有范围或架构普遍失效才需要全局重规划。
- 缺 Owner 确认：先核对现有确认引用；确实缺失则明确提出所需决定，不能自行签认。
  临时环境或 Provider 故障交现有 Run Manager 恢复，不伪装成产品方案失败。

## 依赖判断

区分实际输入/行为前置与“习惯先后”。产品规定进入真实集成前必须完成的确认，
不自动等于所有业务实现必须等待；核对具体消费者和最早成立的验证阶段。
若当前 blocked_by 过强，提出来源、受影响边和验收责任的局部修订，不能直接删边。
只有已批准的执行偏好可以自主调整；两任务写路径不重叠也不能单独证明语义独立。

每次决定都绑定当前 Run/Package/Task/operation/attempt，并附依据。Kernel 接纳回执
表示该动作生效，实际派发和独立 Verify/集成收据分别证明执行与完成。
相同失败和输入没有变化时，不反复重试、重规划或请求相同确认；已有进展则重新核对。
