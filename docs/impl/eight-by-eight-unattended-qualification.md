# 8×8 无监工真实验证记录

## 2026-11-20：启动前环境缺口

基于 dev `e178f43c` 新工作区启动隔离 PRD Run `workflow-055abbd5196f67c6`。
OA 使用 gpt-6-astra，其他配置角色使用 gpt-6-luna。8 Impl + 8 Task Verify 为
目标容量，尚未形成批准的 16 项任务，也未进入 Plan 批准后的无人干预窗口。

两名扫描 Reader 执行命令遇 `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`。
Runtime 观察到了无结果终止并按有界恢复处理，最终生成失败、Supervisor attention 和
Run Manager 诊断请求；RM 自身也受同一环境问题阻塞，无法读取材料或提交 recommendation。
不能把这种有检测但无修复的状态写成无人恢复成功。工作区、账本、原失败均保留。

手动模型预检只返回文本，未证明工具可启动；这是本轮操作者漏检。
已有 `zf doctor provider` 的真实沙箱探针可以复现故障，说明探针本身并未失效。
另一处缺口是 `zf start` 未调用探针，导致漏跑手动 doctor 时真实模型仍会被派发。
本次将现有探针接入 start，在启动 transport 前拒绝受限 Codex 的不可用沙箱。
失败通过 EventWriter 记录 `env.preflight.failed`，不关闭沙箱或改变模型。
详见 [Provider 预检手册](../manual/16-real-codex-provider-preflight.md)。

调用方回归：`tests/test_cli_start_provider_sandbox.py`，共同探针回归：
`tests/test_provider_sandbox.py`。探针只验证命令启动机制，不代表完整工作区能力或业务验收。
宿主仍缺发行版 bubblewrap/AppArmor profile，当前用户的非交互 sudo 要求密码；
需管理员执行现有 `scripts/setup-codex-sandbox.sh --apply` 后，以普通服务用户重验。
系统安全限制没有放宽。

本轮停止是操作者介入，发生于 Plan 批准前；若将本次启动也纳入端到端无人干预资格，
该项不能通过。计划后窗口尚未开始，业务、8+8 容量与最终交付均未验证。

## 2026-11-20：自动重试后 Reader 恢复被错误拒绝

保存的真实账本显示：扫描 Reader 的失败 `run_id` 为 retry-1 派发，但 Operation
的 durable `active_attempt_id` 保持首次 call 身份。二者分层本身正确；旧
`reader_rebuild_error` 将两者直接比较，导致即使宿主修复，child-rebuild 仍拒绝。

准入改为沿最新 kernel 派发核对失败的 transport ID，再核对嵌套 call 的 Operation、
请求摘要、workflow Run 和 durable attempt。外层 TaskAttempt 不冒充 call attempt；
不重写旧 Operation、放宽 Run 终态或跳过侧车校验。尚未派发的环境等待仍走原分支。

`tests/test_reader_rebuild_after_transport_retry.py` 从真实 Orchestrator/mock transport
的两次 Provider turn closed 重现失败，经 Run Manager recommendation → controlled
child-rebuild → Kernel 派发验证恢复；同时覆盖进程重建、幂等和错误身份拒绝。
真实 Provider 的恢复与后续 8×8 仍等待宿主修复，mock 通过不替代该层结论。

## 2026-11-20：宿主修复与旧升级记录关闭

获得用户授权后，执行现有 `scripts/setup-codex-sandbox.sh --apply` 安装发行版
bubblewrap 与 AppArmor profile，普通用户 doctor 实际沙箱命令成功。userns 限制
保持开启；没有关闭 Provider 沙箱。隔离 systemd runtime 重新启动，RM 原生会话
已能执行工具，但旧失败对应的人工升级仍阻止后续恢复。

已有 ControlledActionService 支持以精确 escalation_event_id、approval_ref 和
reason 关闭无合同字段的旧升级。Web 入口却提前统一拒绝，导致安全兼容路径不可达。
本轮仅把 legacy dismiss 接回已有实现，保持 token、当前性、幂等和绑定检查；
无合同的批准恢复仍拒绝。实际 Web 入口到 RM 消费由
`tests/test_web_legacy_escalation_dismiss.py` 覆盖。关闭提示不等于恢复任务，
后续必须观察新的合法 Reader Operation、派发和产物。

宿主修复、重启与人工关闭发生在 Plan 批准前。16 项业务验收和 8+8 并发尚未开始，
本段操作不能计入无人干预成功证据。

## 2026-11-20：部分 Reader 恢复后再次被人工门阻塞

15:21 UTC RM 精确 child-rebuild 已经通过实际准入，新 product-scan Operation 完成
真实 Luna 执行，15:25产物获准。另一 tech-scan 仍为旧失败，重新汇总后 reactor
对 infrastructure_failure 无条件升级 human，挡住已有 RM 诊断责任。现在只有
启用 resident RM 且该事件注册了 RM pending_action 时，保留该路由；未具备这两个
条件仍升级。此改变不批准任何重试，不改变审批、语义重规划或失败事实。

同时修正失败汇总把所有历史 fanout.child.failed 重新注入的缺陷：只补充当前
failed child、当前派发和当前事件的正文。成功或新派发不能被旧报告污染；历史
仍保留账本供诊断。针对两个真实问题新增调用方和多 child 重试回归：
`tests/test_reader_infrastructure_recovery_routing.py`、
`tests/test_fanout_current_failure_findings.py`。真实后续阶段须单独记录，不能从这些
测试推断16项完成或8+8并发通过。

## 2026-11-20：同问题的新失败被旧诊断去重吞掉

解除重复人工门后，只读复验发现最新 `prd.scan.failed` actionable、尚无后续成功，
但其 checkpoint 只由问题 fingerprint 计算；旧诊断的已派发回执导致新失败永远 seen。
再加上从旧到新的 fingerprint 去重，部分恢复后的新输入没有获得诊断机会。

对带 fanout_id 且明确 infra 的发生，将 checkpoint 绑定实际事件 ID，保留稳定问题
fingerprint，并选择同问题的最新失败。相同事件重放仍抑制，历史不改写，仍必须经过
RM裁决及受控准入才能重试。`tests/test_reader_recovery_diagnosis_occurrence.py`
经实际RM投影验证这一选择及幂等；普通语义失败、权限和预算策略不在此修复范围。

## 2026-11-20：单 child 成功不能消费较新的扫描失败

完整投影复验进一步发现：新 checkpoint 已产生，但旧恢复 case 因第一个 child
成功而 resolved，仍把新诊断过滤掉。真实剩余 tech-scan 由重启后的 RM 会话主动
恢复，并于15:59完成；这不证明该自动诊断队列正确，也不能算零干预通过。

现在只有前一 child-rebuild 的内核 effect 成功、同 Run/同 fanout、较新的内核
infra 失败输入，才能重新获得诊断准入。case identity 与历史保持；活动 effect、
人工等待、旧事件重放、异作用域及直接变更动作均不获豁免。队列选择与执行前
使用相同校验。新输入的业务含义与恢复方案仍由 RM 判断，内核不代替裁决。
`tests/test_reader_resolved_case_diagnosis.py` 覆盖完整 RM 投影与准入反例。
