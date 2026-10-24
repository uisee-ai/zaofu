# 185 · 长期 Run 与受控恢复

延续[184/185执行与恢复](185-bounded-execution-and-recovery.md)。生产
`prod-runtime/v1` 与 `refactor-controller-runtime/v3` 的
`workflow.run_limits.timeout_seconds=0` 表示无总墙钟期限；Run可以超过4小时。
Kernel根据验收证据发布 `run.goal.completed`，到时不会自动成功。
blocked/needs-input应向调用者返回原因与恢复点，等待输入或授权修复。
`zf start` 是驻留服务，单个Run完成不应终止整个项目服务。

Operation仍受execution profile的局部超时、用量和成本约束；停滞/生命周期观察与
Run Manager受控恢复保留。正数Run总时限仍是支持的显式策略，预算守卫代码不删除。
0只关闭对应维度，不清零累计用量。公共profile的token与cost预算本次保持不变；
本轮原Run和writer的token不限额是Owner单独批准的验收策略。

## 已启动Run

Run准入时固定预算。仅改zf.yaml只影响以后准入，不能覆盖原Run快照。
停止受影响执行、保留现场后，通过 `zf goal set --status active --timeout-seconds 0
--token-budget 0` 对指定Run发布预算修订；保留原准入基线、超限事实和用量。
具体Run参数以 `zf goal set --help` 为准。根任务通过已有受控恢复同步回执行态。

如果子Operation已经cancelled，受控 `rerun_fanout_child` 创建新的immutable
Operation/Attempt/scratch，关联旧Operation；原结果、Plan、candidate与已完成子项保留。
旧Operation不会被改写成running。仅provider context丢失造成suspended时，原有
Kernel redrive仍复用身份，两种恢复语义不同。

reader汇总仅在Run已恢复且generation仍当前时，允许继续
`workflow_run_terminal:run.goal.blocked` 造成的cancelled汇总；人工cancel不恢复。
必须收齐子项终态，失败子项仍按原aggregate失败规则处理。

验证入口：`tests/test_long_horizon_run.py`、`tests/test_reader_budget_recovery.py`，
连同原预算守卫、goal CLI、reader runtime、repair executor与结果准入闭包。
真实证据另记[监工记录](../records/2026-10-24-0702-185-supervised-repair.md)，
确定性通过不替代真实Kernel交付。

reader重试必须保留上游writer的impl-self-check和固定target描述符；这些是reader输入。
仅重建当前调用身份和结果输出，不能套用writer自身输出清理规则删除上游输入。

上下文切换的活跃执行查询消费dispatch_lost时必须按完整dispatch身份释放；
不能把旧dispatch的丢失推断为整个child终止，也不能漏掉该释放后永久等待settlement。
