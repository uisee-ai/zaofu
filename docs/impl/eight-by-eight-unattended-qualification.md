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
