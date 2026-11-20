> 状态: done — 9d7e6307 `fix: retire obsolete task-bound orchestration escalations`

# 原任务完成后的 OA 故障提示失效

承接用户授权的无人验证发现问题、修复、重验；基线 dev fcda9c63。
真实隔离轮 workflow-503a4acc817a313d：OA 提示 evt-5a8f4d563187 对应 TOOL-MAX
发布故障；该任务已由 Run Manager 自动发布、独立 Verify 与集成完成
（evt-d90ef9d24cc1），但 _pending_human_decisions 仍将提示列为 run 阻塞。
原提示缺少 Task 恢复身份，不能沿现有完成证据失效。原账本保持不变。

## 验收

- 既有已准入 OA semantic_failure 升级 -> verify: 只由 kernel 决策准入、checkpoint、
  Task 范围原请求的完整因果链恢复 run/task/map 身份，不解析业务文本猜测。
- 原任务随后由 kernel task_integration_receipt 完成 -> verify: 此旧故障提示在既有
  Run Manager 投影失效，不生成用户批准或业务通过、不重放任何动作。
- 另一任务/Run/map、worker 自报完成、未准入决策、缺失/冲突来源、普通审批 ->
  verify: 不失效；同 Task 后续新故障仍保留。
- 定向/调用方/影响闭包/哨兵/mock及真实账本只读对照 -> verify: 记录结果。

范围不包含自动额度查询、自动批准有效人工决定或热改正在运行的冻结源码。
说明：[持续推进与无人资格](../../docs/impl/unattended-progress-recovery.md)。

## 验证记录

基线2 failed/18 passed，修复后专项及调用方81 passed；dev-verify影响闭包1384 passed、
7 skipped，哨兵53 passed，流程mock10 passed，额外流程/8槽位/真实子进程mock11 passed。
真实账本只读对照：修复前原Task完成后仍pending；修复后完成前pending、精确完成后
不再pending。原运行/用户批准记录不变。初轮测试夹具错误地把Agent submitted当作kernel
来源并把诊断correlation_id当作Run，真实回放暴露后已按正式格式修正，权限仍来自kernel admission。
证据：主工作区reports/oa-failure-retirement-20261119/。
