# Reader 传输失败的读取断点恢复

Reader 或 Synth 在必读输入尚未完成时遇到 Provider transport error，不应把
已经增加的读取覆盖丢弃，也不应把没有产出的 Planner 判为内容不合格。

`reader_loss_progress.py` 使用原 operation 的固定读取策略，计算原 attempt
账本中去重的必读内容覆盖。仅当 Provider 以 `failed/transport_error` 结束，
operation、request hash、角色和 attempt 完全相同，且覆盖比上次失败增加时，
允许继续原请求。重复页、损坏账本、错误身份和正常结束不扩展恢复额度；没有
新进展时仍使用既有有限恢复策略。费用与其他执行预算继续由原 operation 约束。

恢复耗尽后，Kernel 的子任务失败事实带有 `reader_execution_failure` 报告，
Plan 聚合将其识别为执行故障，不再转换为缺少产出端口的 Plan preflight 缺陷。
原始失败、读取账本和操作历史均保留，恢复不构成语义批准。

当前 Reader 恢复入口位于 `reader_dispatch_recovery.py`，沿用角色占用检查、
预算修正恢复和成功派发后才消耗重试次数的规则。派发暂缓后重放同一次丢失时，
读取覆盖仍与前一次检查点比较，不能拿该次丢失自己保存的覆盖判定无进展。

## 已被旧版本误判的单 Reader

操作员可以在项目目录使用：

```bash
zf restart planner --resume-failed-reader <fanout.child.failed-event-id> \
  --recovery-approval-ref artifacts/supervision/reader-recovery-approval.json
```

审批文件必须在项目内，其正文绑定精确失败和 Run：

```json
{
  "action": "resume-reader-checkpoint",
  "failure_event_id": "<fanout.child.failed-event-id>",
  "workflow_run_id": "<workflow-run-id>"
}
```

入口先检查该单 Reader 及后续派生轮次：目标、需求与配置版本不变，没有准入结果
或 Critic 派发，全部子任务失败均由传输故障造成。只容许 Kernel 的执行失败或
仅包含缺少 producer 的历史 preflight。恢复 blocked Run 时，还要精确绑定该
失败链导致的 stage-cap escalation；其他阻塞、终态、拒绝和活跃操作均拒绝恢复。
CLI 原有的 Run contract drift 检查仍然执行。

在角色 respawn lease 内，受控入口通过 EventWriter 重开原 fanout、聚合与
operation。审批引用及 SHA-256 写入恢复事件。原请求、输入 manifest、读取
attempt、费用快照不变；后续误开 fanout 成为历史实例。先写 Run 恢复事实，
再派发原子任务，保证生命周期查询把派发归入恢复后的 Run。旧聚合的失败结果
退出当前投影，失败事件仍在账本中。CLI 重启目标角色并重新注入原简报，附上当前
runtime CLI 路径。中途失败可以重放，事件按精确 failure id 去重。

该入口不增加语义 replan generation，不伪造读取回执，不重试 policy 拒绝操作，
也不批准 Plan。原 Planner 完成后，仍需正常产出准入、独立 Critic 和后续验收。

验证入口：`tests/test_reader_progress_recovery.py`、
`tests/test_reader_restart_checkpoint.py`，并覆盖既有 Reader/Synth 重启、
operation closeout、fanout 投影、CLI、Run admission 和 mock flow 回归。

## 无审查结果的 Synth 超时

`zf restart plan-critic --resume-failed-synth <fanout.synth.completed-event-id>
--recovery-approval-ref <project-local-json>` 也支持 Kernel 记录的 `synth_timeout`。
审批正文使用 `action: resume-synth-checkpoint`，并绑定 `failure_event_id` 和
`workflow_run_id`。仅接受没有报告、没有准入结果且保有有效必读进展的原请求。
输入 manifest、原 attempt、角色和请求哈希必须一致；更新的同阶段 fanout、其他
占用该角色的操作、活跃 Provider turn、policy 拒绝和终态 Run 均不可恢复。

若 Run 被阻塞，必须证明其唯一当前终态沿 stage-cap escalation、阶段失败追溯至
该超时。优雅停机留下的 suspended operation 经 `WorkflowOperationService`
重投准入后，再通过 EventWriter 和 TaskStore 恢复 Run/任务并派发原 Synth。
角色 respawn lease 覆盖整个过程，恢复中断可重放；原读取账本、费用绑定、候选
和历史失败保持原样。该入口不启动新规划代际，也不替代 Critic 的语义判断。

真实 CLI 调用链与中断重放回归见 `tests/test_synth_timeout_checkpoint.py`。
